from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from bisect import bisect_right
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zipfile import BadZipFile, ZipFile, ZipInfo

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from databento_dbn import (
    Compression,
    DBNDecoder,
    InstrumentDefMsg,
    Metadata,
    OHLCVMsg,
    StatMsg,
)

from .trading_day import futures_session_is_open, trading_day_bounds_utc, trading_day_date


DATASET = "GLBX.MDP3"
SUPPORTED_ROOTS = frozenset({"MNQ", "MES", "NQ", "ES"})
ROLL_POLICY_VERSION = "volume_previous_completed_session_v1"
# Version 6 adds verified date-specific holiday closures to the historical halt
# calendar. Reject earlier materializations rather than silently retaining
# incorrect session completeness and holiday coverage assumptions.
CACHE_FORMAT_VERSION = 6
DEFAULT_TIMEFRAMES = (
    ("minute", 1),
    ("minute", 5),
    ("minute", 15),
    ("hour", 1),
    ("hour", 4),
    ("day", 1),
)
PRICE_SCALE = 1_000_000_000
DBN_READ_CHUNK_BYTES = 1024 * 1024
PARQUET_BATCH_ROWS = 250_000
_OUTRIGHT_PATTERN = re.compile(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,4})$")
_TIMEFRAME_SECONDS = {"second": 1, "minute": 60, "hour": 3600}
_ARRAY_COLUMNS = (
    "timestamp_ns",
    "close_timestamp_ns",
    "open_nano",
    "high_nano",
    "low_nano",
    "close_nano",
    "volume",
    "instrument_id",
    "raw_symbol_code",
    "session_ordinal",
)
_ARRAY_DTYPES: Mapping[str, np.dtype[Any]] = {
    "timestamp_ns": np.dtype(np.int64),
    "close_timestamp_ns": np.dtype(np.int64),
    "open_nano": np.dtype(np.int64),
    "high_nano": np.dtype(np.int64),
    "low_nano": np.dtype(np.int64),
    "close_nano": np.dtype(np.int64),
    "volume": np.dtype(np.uint64),
    "instrument_id": np.dtype(np.uint32),
    "raw_symbol_code": np.dtype(np.uint32),
    "session_ordinal": np.dtype(np.int32),
}
_UINT64_MAX = (1 << 64) - 1
_SERIES_VALIDATION_CHUNK_ROWS = 1_000_000
# Keep the on-disk cache layout below legacy Windows MAX_PATH even when pytest,
# a workspace manager, or TOPSIGNAL_DATABENTO_CACHE_DIR adds a deep prefix. The
# complete SHA-256 remains in both manifests and is always used for validation;
# only the directory token is shortened.
_SERIES_PATH_FINGERPRINT_LENGTH = 20
# The hot LRU is a hard working-set guard, so charge conservatively above the
# measured ~535-byte Python expansion rather than letting container overhead
# put the process beyond the configured byte ceiling.
_EAGER_CANDLE_ESTIMATED_BYTES = 640
_DEFAULT_PROXY_CACHE_ROWS = 16_384
_MMAP_STORAGE_BYTES_PER_ROW = sum(
    int(_ARRAY_DTYPES[name].itemsize) for name in _ARRAY_COLUMNS
)


class DatabentoCacheError(ValueError):
    """A deterministic local archive/cache validation error."""


class DatabentoCacheMissingError(DatabentoCacheError):
    pass


class DatabentoCacheStaleError(DatabentoCacheError):
    pass


@dataclass(frozen=True)
class ArchiveDescriptor:
    path: str
    name: str
    size: int
    mtime_ns: int
    change_ns: int
    device: int
    inode: int
    sha256: str
    job_id: str
    dataset: str
    schema: str
    root_symbol: str
    start_ns: int
    end_ns: int


@dataclass(frozen=True)
class CacheBuildResult:
    cache_root: str
    version_dir: str
    source_fingerprint: str
    roots: tuple[str, ...]
    timeframes: tuple[str, ...]
    archive_count: int
    records_by_schema: Mapping[str, int]
    reused: bool


@dataclass(frozen=True)
class _Instrument:
    root_symbol: str
    instrument_id: int
    raw_symbol: str
    contract_key: str
    instrument_class: str
    security_type: str
    activation_ns: int
    expiration_ns: int
    min_price_increment_nano: int
    unit_of_measure_qty_nano: int
    definition_ts_ns: int
    source_sha256: str
    security_update_action: str = "A"


@dataclass(frozen=True)
class _RollDecision:
    root_symbol: str
    trading_date: date
    instrument_id: int
    raw_symbol: str
    contract_key: str
    decision_session_date: date | None
    from_instrument_id: int | None
    current_volume: int | None
    candidate_volume: int | None
    reason: str
    policy_version: str = ROLL_POLICY_VERSION


@dataclass(slots=True)
class CachedReplayCandle:
    """Small adapter with the candle attribute contract used by strategies."""

    user_id: Any
    contract_id: str
    symbol: str
    live: bool
    unit: str
    unit_number: int
    candle_timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    is_partial: bool
    raw_payload: None
    fetched_at: None
    source: str
    source_instrument_id: int
    source_raw_symbol: str
    source_file_sha256: str
    roll_policy_version: str
    nominal_close_time: datetime


class CachedCandleList(list[CachedReplayCandle]):
    _topsignal_sorted_closed = True
    _topsignal_verified_replay = True
    _topsignal_input_fingerprint: str | None = None
    _topsignal_series_fingerprint: str | None = None
    _topsignal_slice_start: int | None = None
    _topsignal_slice_end: int | None = None
    _topsignal_user_id: str | None = None
    _topsignal_contract_id: str | None = None
    _topsignal_symbol: str | None = None
    _topsignal_unit: str | None = None
    _topsignal_unit_number: int | None = None


@dataclass
class _MappedSeries:
    directory: Path
    metadata: Mapping[str, Any]
    arrays: dict[str, np.ndarray]
    raw_symbols_by_code: Mapping[int, str]
    _lease_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _lease_count: int = field(default=0, init=False, repr=False)
    _retired: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        expected_entry: Mapping[str, Any],
        expected_source_fingerprint: str,
    ) -> "_MappedSeries":
        metadata = _read_json(directory / "metadata.json")
        arrays: dict[str, np.ndarray] = {}
        try:
            for name in _ARRAY_COLUMNS:
                arrays[name] = np.load(directory / f"{name}.npy", mmap_mode="r")
            _validate_mapped_series(
                directory,
                metadata=metadata,
                arrays=arrays,
                expected_entry=expected_entry,
                expected_source_fingerprint=expected_source_fingerprint,
            )
            raw_by_code = metadata.get("raw_symbols_by_code")
            if isinstance(raw_by_code, dict):
                raw_symbols_by_code = {
                    int(code): str(symbol) for code, symbol in raw_by_code.items()
                }
            else:
                raw = metadata.get("raw_symbols")
                raw_symbols_by_code = {
                    int(code): str(symbol)
                    for symbol, code in (
                        raw.items() if isinstance(raw, dict) else []
                    )
                }
            if not raw_symbols_by_code:
                raise DatabentoCacheError(
                    f"databento_series_raw_symbol_mapping_missing:{directory}"
                )
            known_codes = set(raw_symbols_by_code)
            raw_codes = arrays["raw_symbol_code"]
            for start in range(0, int(raw_codes.size), _SERIES_VALIDATION_CHUNK_ROWS):
                stop = min(
                    int(raw_codes.size), start + _SERIES_VALIDATION_CHUNK_ROWS
                )
                if any(int(code) not in known_codes for code in np.unique(raw_codes[start:stop])):
                    raise DatabentoCacheError(
                        f"databento_series_raw_symbol_code_unknown:{directory}"
                    )
        except Exception:
            for array in arrays.values():
                _close_memmap(array)
            raise
        return cls(
            directory=directory,
            metadata=metadata,
            arrays=arrays,
            raw_symbols_by_code=raw_symbols_by_code,
        )

    def acquire(self) -> "_MappedSeriesLease":
        with self._lease_lock:
            if self._closed:
                raise DatabentoCacheStaleError(
                    f"databento_series_mapping_closed:{self.directory}"
                )
            self._lease_count += 1
        return _MappedSeriesLease(self)

    def close(self) -> None:
        """Retire the store's handle, closing after every lazy view is gone."""

        close_now = False
        with self._lease_lock:
            self._retired = True
            if self._lease_count == 0 and not self._closed:
                self._closed = True
                close_now = True
        if close_now:
            self._close_arrays()

    def _release(self) -> None:
        close_now = False
        with self._lease_lock:
            if self._lease_count <= 0:
                return
            self._lease_count -= 1
            if self._retired and self._lease_count == 0 and not self._closed:
                self._closed = True
                close_now = True
        if close_now:
            self._close_arrays()

    def _close_arrays(self) -> None:
        # Do not force-close NumPy's mmap object here. A caller may still hold
        # a zero-copy ndarray view obtained from a lazy sequence after the
        # sequence itself has been released. NumPy keeps the base memmap alive
        # for such views, but explicitly closing ``array._mmap`` invalidates
        # that memory and can terminate the interpreter on the next access.
        # Dropping our references lets NumPy close each mapping naturally when
        # the final array/view is collected, while versioned cache files make
        # delayed OS-handle release safe during source invalidation.
        self.arrays.clear()


class _MappedSeriesLease:
    """One shared lifetime token for a lazy view and all of its child slices."""

    __slots__ = ("mapped", "_released")

    def __init__(self, mapped: _MappedSeries) -> None:
        self.mapped = mapped
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.mapped._release()

    def __del__(self) -> None:
        self.release()


class MmapReplayCandle:
    """Read-only candle proxy that converts only fields a strategy accesses."""

    __slots__ = (
        "_sequence",
        "_absolute_index",
        "_cached_timestamp",
        "_cached_nominal_close",
        "_cached_open",
        "_cached_high",
        "_cached_low",
        "_cached_close",
        "_cached_volume",
        "_cached_instrument_id",
        "_cached_raw_symbol",
    )

    def __init__(self, sequence: "MmapCandleSequence", absolute_index: int) -> None:
        self._sequence = sequence
        self._absolute_index = absolute_index
        self._cached_timestamp: datetime | None = None
        self._cached_nominal_close: datetime | None = None
        self._cached_open: float | None = None
        self._cached_high: float | None = None
        self._cached_low: float | None = None
        self._cached_close: float | None = None
        self._cached_volume: int | None = None
        self._cached_instrument_id: int | None = None
        self._cached_raw_symbol: str | None = None

    @property
    def user_id(self) -> Any:
        return self._sequence._user_id

    @property
    def contract_id(self) -> str:
        return self._sequence._contract_id

    @property
    def symbol(self) -> str:
        return self._sequence._root_symbol

    @property
    def live(self) -> bool:
        return False

    @property
    def unit(self) -> str:
        return self._sequence._unit

    @property
    def unit_number(self) -> int:
        return self._sequence._unit_number

    @property
    def candle_timestamp(self) -> datetime:
        value = self._cached_timestamp
        if value is None:
            value = _datetime_from_ns(
                int(self._sequence._arrays["timestamp_ns"][self._absolute_index])
            )
            self._cached_timestamp = value
        return value

    @property
    def open_price(self) -> float:
        value = self._cached_open
        if value is None:
            value = int(
                self._sequence._arrays["open_nano"][self._absolute_index]
            ) / PRICE_SCALE
            self._cached_open = value
        return value

    @property
    def high_price(self) -> float:
        value = self._cached_high
        if value is None:
            value = int(
                self._sequence._arrays["high_nano"][self._absolute_index]
            ) / PRICE_SCALE
            self._cached_high = value
        return value

    @property
    def low_price(self) -> float:
        value = self._cached_low
        if value is None:
            value = int(
                self._sequence._arrays["low_nano"][self._absolute_index]
            ) / PRICE_SCALE
            self._cached_low = value
        return value

    @property
    def close_price(self) -> float:
        value = self._cached_close
        if value is None:
            value = int(
                self._sequence._arrays["close_nano"][self._absolute_index]
            ) / PRICE_SCALE
            self._cached_close = value
        return value

    @property
    def volume(self) -> int:
        value = self._cached_volume
        if value is None:
            value = int(self._sequence._arrays["volume"][self._absolute_index])
            self._cached_volume = value
        return value

    @property
    def is_partial(self) -> bool:
        return False

    @property
    def raw_payload(self) -> None:
        return None

    @property
    def fetched_at(self) -> None:
        return None

    @property
    def source(self) -> str:
        return "databento_local_cache"

    @property
    def source_instrument_id(self) -> int:
        value = self._cached_instrument_id
        if value is None:
            value = int(
                self._sequence._arrays["instrument_id"][self._absolute_index]
            )
            self._cached_instrument_id = value
        return value

    @property
    def source_raw_symbol(self) -> str:
        value = self._cached_raw_symbol
        if value is None:
            code = int(
                self._sequence._arrays["raw_symbol_code"][self._absolute_index]
            )
            value = self._sequence._raw_symbols_by_code.get(code, "")
            self._cached_raw_symbol = value
        return value

    @property
    def source_file_sha256(self) -> str:
        return self._sequence._source_fingerprint

    @property
    def roll_policy_version(self) -> str:
        return self._sequence._roll_policy_version

    @property
    def nominal_close_time(self) -> datetime:
        value = self._cached_nominal_close
        if value is None:
            value = _datetime_from_ns(
                int(
                    self._sequence._arrays["close_timestamp_ns"][
                        self._absolute_index
                    ]
                )
            )
            self._cached_nominal_close = value
        return value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (MmapReplayCandle, CachedReplayCandle)):
            return NotImplemented
        fields = (
            "user_id",
            "contract_id",
            "symbol",
            "live",
            "unit",
            "unit_number",
            "candle_timestamp",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
            "is_partial",
            "raw_payload",
            "fetched_at",
            "source",
            "source_instrument_id",
            "source_raw_symbol",
            "source_file_sha256",
            "roll_policy_version",
            "nominal_close_time",
        )
        return all(getattr(self, field) == getattr(other, field) for field in fields)

    __hash__ = None


class MmapCandleSequence(Sequence[MmapReplayCandle]):
    """O(1) read-only view over one contiguous interval of mmap arrays."""

    _topsignal_sorted_closed = True
    _topsignal_verified_replay = True
    _topsignal_mmap_backed = True
    _topsignal_lazy_replay = True
    _topsignal_storage_bytes_per_row = _MMAP_STORAGE_BYTES_PER_ROW

    def __init__(
        self,
        lease: _MappedSeriesLease,
        *,
        user_id: Any,
        contract_id: str,
        root_symbol: str,
        unit: str,
        unit_number: int,
        left: int,
        right: int,
        _proxy_owner: "MmapCandleSequence | None" = None,
        _proxy_cache: OrderedDict[int, MmapReplayCandle] | None = None,
        _proxy_lock: threading.RLock | None = None,
        _proxy_cache_rows: int | None = None,
    ) -> None:
        mapped = lease.mapped
        row_count = int(mapped.arrays["timestamp_ns"].size)
        if left < 0 or right < left or right > row_count:
            raise IndexError("invalid mmap candle view bounds")
        self._lease = lease
        self._arrays = mapped.arrays
        self._raw_symbols_by_code = mapped.raw_symbols_by_code
        self._user_id = user_id
        self._contract_id = str(contract_id)
        self._root_symbol = str(root_symbol)
        self._unit = str(unit)
        self._unit_number = int(unit_number)
        self._left = int(left)
        self._right = int(right)
        self._proxy_cache = _proxy_cache if _proxy_cache is not None else OrderedDict()
        self._proxy_lock = _proxy_lock if _proxy_lock is not None else threading.RLock()
        self._proxy_cache_rows = (
            max(1, int(_proxy_cache_rows))
            if _proxy_cache_rows is not None
            else _positive_int_setting(
                None,
                env_name="TOPSIGNAL_BACKTEST_PROXY_CACHE_ROWS",
                default=_DEFAULT_PROXY_CACHE_ROWS,
            )
        )
        self._proxy_owner = _proxy_owner if _proxy_owner is not None else self
        self._source_fingerprint = str(mapped.metadata["source_fingerprint"])
        self._roll_policy_version = str(mapped.metadata["roll_policy_version"])
        series_fingerprint = str(mapped.metadata["series_fingerprint"])
        self._topsignal_series_fingerprint = series_fingerprint
        self._topsignal_slice_start = self._left
        self._topsignal_slice_end = self._right
        self._topsignal_input_fingerprint = _slice_fingerprint(
            series_fingerprint,
            self._left,
            self._right,
            user_id=str(user_id),
            contract_id=self._contract_id,
        )
        self._topsignal_user_id = str(user_id)
        self._topsignal_contract_id = self._contract_id
        self._topsignal_symbol = self._root_symbol
        self._topsignal_unit = self._unit
        self._topsignal_unit_number = self._unit_number
        self._topsignal_physical_stream = (
            series_fingerprint,
            self._root_symbol,
            self._unit,
            self._unit_number,
        )
        self._topsignal_physical_row_count = len(self)

    def __len__(self) -> int:
        return self._right - self._left

    def __iter__(self) -> Iterator[MmapReplayCandle]:
        for relative_index in range(len(self)):
            yield self[relative_index]

    def __getitem__(
        self, index: int | slice
    ) -> MmapReplayCandle | "MmapCandleSequence" | list[MmapReplayCandle]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                return [self[position] for position in range(start, stop, step)]
            absolute_left = self._left + start
            absolute_right = max(absolute_left, self._left + stop)
            return MmapCandleSequence(
                self._lease,
                user_id=self._user_id,
                contract_id=self._contract_id,
                root_symbol=self._root_symbol,
                unit=self._unit,
                unit_number=self._unit_number,
                left=absolute_left,
                right=absolute_right,
                _proxy_owner=self._proxy_owner,
                _proxy_cache=self._proxy_cache,
                _proxy_lock=self._proxy_lock,
                _proxy_cache_rows=self._proxy_cache_rows,
            )
        normalized = int(index)
        if normalized < 0:
            normalized += len(self)
        if normalized < 0 or normalized >= len(self):
            raise IndexError("mmap candle index out of range")
        absolute_index = self._left + normalized
        with self._proxy_lock:
            cached = self._proxy_cache.get(absolute_index)
            if cached is not None:
                self._proxy_cache.move_to_end(absolute_index)
                return cached
            candle = MmapReplayCandle(self._proxy_owner, absolute_index)
            self._proxy_cache[absolute_index] = candle
            while len(self._proxy_cache) > self._proxy_cache_rows:
                self._proxy_cache.popitem(last=False)
            return candle

    @property
    def start_ns(self) -> np.ndarray:
        return self._array_view("timestamp_ns")

    @property
    def close_ns(self) -> np.ndarray:
        return self._array_view("close_timestamp_ns")

    @property
    def volume_values(self) -> np.ndarray:
        return self._array_view("volume")

    @property
    def open_nano_values(self) -> np.ndarray:
        return self._array_view("open_nano")

    @property
    def high_nano_values(self) -> np.ndarray:
        return self._array_view("high_nano")

    @property
    def low_nano_values(self) -> np.ndarray:
        return self._array_view("low_nano")

    @property
    def close_nano_values(self) -> np.ndarray:
        return self._array_view("close_nano")

    @property
    def instrument_id_values(self) -> np.ndarray:
        return self._array_view("instrument_id")

    @property
    def raw_symbol_code_values(self) -> np.ndarray:
        return self._array_view("raw_symbol_code")

    @property
    def session_ordinal_values(self) -> np.ndarray:
        return self._array_view("session_ordinal")

    def search_start(self, value: datetime | int, *, side: str = "left") -> int:
        return self._search("timestamp_ns", value, side=side)

    def search_close(self, value: datetime | int, *, side: str = "right") -> int:
        return self._search("close_timestamp_ns", value, side=side)

    def materialize(self, start: int = 0, end: int | None = None) -> CachedCandleList:
        normalized_start, normalized_end, step = slice(start, end).indices(len(self))
        if step != 1:
            raise ValueError("mmap candle materialization requires a contiguous slice")
        absolute_left = self._left + normalized_start
        absolute_right = max(absolute_left, self._left + normalized_end)
        return _materialize_mapped_slice(
            self._lease.mapped,
            user_id=self._user_id,
            contract_id=self._contract_id,
            root_symbol=self._root_symbol,
            unit=self._unit,
            unit_number=self._unit_number,
            left=absolute_left,
            right=absolute_right,
        )

    def _array_view(self, name: str) -> np.ndarray:
        return self._arrays[name][self._left : self._right]

    def _search(self, name: str, value: datetime | int, *, side: str) -> int:
        if side not in {"left", "right"}:
            raise ValueError("search side must be 'left' or 'right'")
        target_ns = _datetime_to_ns(value) if isinstance(value, datetime) else int(value)
        relative = int(
            np.searchsorted(self._array_view(name), target_ns, side=side)
        )
        return relative


def _materialize_mapped_slice(
    mapped: _MappedSeries,
    *,
    user_id: Any,
    contract_id: str,
    root_symbol: str,
    unit: str,
    unit_number: int,
    left: int,
    right: int,
) -> CachedCandleList:
    arrays = mapped.arrays
    source_fingerprint = str(mapped.metadata["source_fingerprint"])
    series_fingerprint = str(mapped.metadata["series_fingerprint"])
    roll_policy = str(mapped.metadata["roll_policy_version"])
    result = CachedCandleList()
    append = result.append
    raw_symbols = mapped.raw_symbols_by_code
    for index in range(left, right):
        append(
            CachedReplayCandle(
                user_id=user_id,
                contract_id=contract_id,
                symbol=root_symbol,
                live=False,
                unit=unit,
                unit_number=unit_number,
                candle_timestamp=_datetime_from_ns(
                    int(arrays["timestamp_ns"][index])
                ),
                open_price=int(arrays["open_nano"][index]) / PRICE_SCALE,
                high_price=int(arrays["high_nano"][index]) / PRICE_SCALE,
                low_price=int(arrays["low_nano"][index]) / PRICE_SCALE,
                close_price=int(arrays["close_nano"][index]) / PRICE_SCALE,
                volume=int(arrays["volume"][index]),
                is_partial=False,
                raw_payload=None,
                fetched_at=None,
                source="databento_local_cache",
                source_instrument_id=int(arrays["instrument_id"][index]),
                source_raw_symbol=raw_symbols.get(
                    int(arrays["raw_symbol_code"][index]), ""
                ),
                source_file_sha256=source_fingerprint,
                roll_policy_version=roll_policy,
                nominal_close_time=_datetime_from_ns(
                    int(arrays["close_timestamp_ns"][index])
                ),
            )
        )
    result._topsignal_series_fingerprint = series_fingerprint
    result._topsignal_slice_start = left
    result._topsignal_slice_end = right
    result._topsignal_input_fingerprint = _slice_fingerprint(
        series_fingerprint,
        left,
        right,
        user_id=str(user_id),
        contract_id=str(contract_id),
    )
    result._topsignal_user_id = str(user_id)
    result._topsignal_contract_id = str(contract_id)
    result._topsignal_symbol = root_symbol
    result._topsignal_unit = unit
    result._topsignal_unit_number = int(unit_number)
    return result


class DatabentoReplayStore:
    """Thread-safe mmap reader with binary slicing and a byte-bounded hot LRU."""

    def __init__(
        self,
        cache_root: str | Path | None = None,
        *,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        build_missing_timeframes: bool = False,
    ) -> None:
        self.cache_root = _resolve_cache_root(cache_root)
        self.max_entries = _positive_int_setting(
            max_entries,
            env_name="TOPSIGNAL_BACKTEST_CACHE_MAX_ENTRIES",
            default=8,
        )
        self.max_bytes = _positive_int_setting(
            max_bytes,
            env_name="TOPSIGNAL_BACKTEST_CACHE_MAX_BYTES",
            default=512 * 1024 * 1024,
        )
        self.build_missing_timeframes = bool(build_missing_timeframes)
        self._lock = threading.RLock()
        self._pointer_signature: tuple[int, int, int, int, int] | None = None
        self._manifest: dict[str, Any] | None = None
        self._version_dir: Path | None = None
        self._mapped: OrderedDict[str, _MappedSeries] = OrderedDict()
        self._slices: OrderedDict[
            tuple[Any, ...], tuple[CachedCandleList, int]
        ] = OrderedDict()
        self._slice_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def history_bounds(self, root_symbol: str) -> tuple[datetime, datetime] | None:
        root = str(root_symbol).strip().upper()
        with self._lock:
            manifest = self._refresh_manifest()
            minute_entry = self._series_entry(manifest, root, "minute", 1)
            if minute_entry is None:
                return None
            series = manifest.get("series")
            first_timestamp_ns = min(
                int(entry["first_timestamp_ns"])
                for key, entry in (series.items() if isinstance(series, dict) else [])
                if str(key).startswith(f"{root}:")
                and isinstance(entry, dict)
                and "first_timestamp_ns" in entry
            )
            return (
                _datetime_from_ns(first_timestamp_ns),
                _datetime_from_ns(int(minute_entry["source_end_ns"])),
            )

    def has_root(self, root_symbol: str) -> bool:
        try:
            return self.history_bounds(root_symbol) is not None
        except DatabentoCacheError:
            return False

    def open_candles(
        self,
        *,
        user_id: str,
        contract_id: str,
        root_symbol: str,
        unit: str,
        unit_number: int,
        start: datetime,
        end: datetime,
        closed_by: datetime,
    ) -> MmapCandleSequence:
        """Open an O(1) candle view without allocating one object per bar."""

        with self._lock:
            (
                root,
                normalized_unit,
                number,
                mapped,
                left,
                right,
            ) = self._resolve_mapped_slice(
                root_symbol=root_symbol,
                unit=unit,
                unit_number=unit_number,
                start=start,
                end=end,
                closed_by=closed_by,
            )
            lease = mapped.acquire()
            try:
                return MmapCandleSequence(
                    lease,
                    user_id=user_id,
                    contract_id=contract_id,
                    root_symbol=root,
                    unit=normalized_unit,
                    unit_number=number,
                    left=left,
                    right=right,
                )
            except Exception:
                lease.release()
                raise

    def load_candles(
        self,
        *,
        user_id: str,
        contract_id: str,
        root_symbol: str,
        unit: str,
        unit_number: int,
        start: datetime,
        end: datetime,
        closed_by: datetime,
        max_rows: int | None = None,
    ) -> CachedCandleList:
        with self._lock:
            (
                root,
                normalized_unit,
                number,
                mapped,
                left,
                right,
            ) = self._resolve_mapped_slice(
                root_symbol=root_symbol,
                unit=unit,
                unit_number=unit_number,
                start=start,
                end=end,
                closed_by=closed_by,
            )
            count = right - left
            if max_rows is not None and count > max(1, int(max_rows)):
                raise DatabentoCacheError(
                    "databento_replay_memory_budget_exceeded: materialized slice "
                    f"contains {count:,} bars, above the configured {int(max_rows):,}-bar ceiling"
                )
            series_fingerprint = str(mapped.metadata["series_fingerprint"])
            key = (
                series_fingerprint,
                left,
                right,
                str(user_id),
                str(contract_id),
            )
            cached = self._slices.get(key)
            if cached is not None:
                self._hits += 1
                self._slices.move_to_end(key)
                return cached[0]
            self._misses += 1
            candles = self._materialize_slice(
                mapped,
                user_id=user_id,
                contract_id=contract_id,
                root_symbol=root,
                unit=normalized_unit,
                unit_number=number,
                left=left,
                right=right,
            )
            estimated_bytes = max(1, count) * _EAGER_CANDLE_ESTIMATED_BYTES
            if estimated_bytes <= self.max_bytes:
                self._slices[key] = (candles, estimated_bytes)
                self._slice_bytes += estimated_bytes
                self._evict_slices()
            return candles

    def _resolve_mapped_slice(
        self,
        *,
        root_symbol: str,
        unit: str,
        unit_number: int,
        start: datetime,
        end: datetime,
        closed_by: datetime,
    ) -> tuple[str, str, int, _MappedSeries, int, int]:
        root = str(root_symbol).strip().upper()
        normalized_unit = str(unit).strip().lower()
        number = int(unit_number)
        _validate_timeframe(normalized_unit, number)
        manifest = self._refresh_manifest()
        entry = self._series_entry(manifest, root, normalized_unit, number)
        if entry is None and self.build_missing_timeframes:
            manifest = self._build_missing_series(root, normalized_unit, number)
            entry = self._series_entry(manifest, root, normalized_unit, number)
        if entry is None:
            raise DatabentoCacheMissingError(
                f"databento_timeframe_cache_missing:{root}:{timeframe_key(normalized_unit, number)}: "
                "run build_databento_cache.py with the required timeframe"
            )
        mapped = self._open_series(entry)
        start_ns = _datetime_to_ns(_as_utc(start))
        minute_entry = self._series_entry(manifest, root, "minute", 1)
        if (
            minute_entry is not None
            and start_ns <= int(minute_entry["first_timestamp_ns"])
        ):
            # Preserve the legacy raw-row resampler at the left history
            # boundary: the first sparse minute may belong to an earlier
            # session-anchored bucket (for example MNQ's 22:03 launch bar
            # belongs to the 22:00 five-minute candle).
            start_ns = min(start_ns, int(entry["first_timestamp_ns"]))
        requested_end_ns = min(
            _datetime_to_ns(_as_utc(end)),
            _datetime_to_ns(_as_utc(closed_by)),
            int(entry["source_end_ns"]),
        )
        left = int(
            np.searchsorted(mapped.arrays["timestamp_ns"], start_ns, side="left")
        )
        right = int(
            np.searchsorted(
                mapped.arrays["close_timestamp_ns"],
                requested_end_ns,
                side="right",
            )
        )
        return root, normalized_unit, number, mapped, left, max(left, right)

    def clear(self) -> None:
        with self._lock:
            self._clear_unlocked(reset_manifest=False)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "slice_entries": len(self._slices),
                "slice_bytes": self._slice_bytes,
                "mapped_series": len(self._mapped),
            }

    def _refresh_manifest(self) -> dict[str, Any]:
        pointer = self.cache_root / "current.json"
        if not pointer.is_file():
            self._clear_unlocked(reset_manifest=True)
            raise DatabentoCacheMissingError(
                f"databento_cache_missing:{pointer}: run build_databento_cache.py"
            )
        stat = pointer.stat()
        signature = (
            int(stat.st_mtime_ns),
            int(stat.st_size),
            _source_change_ns(pointer, stat),
            int(stat.st_dev),
            int(stat.st_ino),
        )
        if signature != self._pointer_signature or self._manifest is None:
            manifest = _read_json(pointer)
            if int(manifest.get("cache_format_version", -1)) != CACHE_FORMAT_VERSION:
                raise DatabentoCacheStaleError("databento_cache_format_changed")
            if str(manifest.get("roll_policy_version")) != ROLL_POLICY_VERSION:
                raise DatabentoCacheStaleError("databento_roll_policy_changed")
            version_dir = self.cache_root / str(manifest.get("version_dir") or "")
            if not (version_dir / "manifest.json").is_file():
                raise DatabentoCacheMissingError(
                    f"databento_cache_version_missing:{version_dir}"
                )
            prior_fingerprint = (
                str(self._manifest.get("source_fingerprint"))
                if self._manifest is not None
                else None
            )
            prior_version = (
                str(self._manifest.get("version_dir"))
                if self._manifest is not None
                else None
            )
            if (
                prior_fingerprint != str(manifest.get("source_fingerprint"))
                or prior_version != str(manifest.get("version_dir"))
            ):
                self._clear_unlocked(reset_manifest=False)
            self._manifest = manifest
            self._version_dir = version_dir
            self._pointer_signature = signature
        self._validate_sources(self._manifest)
        return self._manifest

    def _validate_sources(self, manifest: Mapping[str, Any]) -> None:
        for raw in manifest.get("archives", []):
            if not isinstance(raw, dict):
                continue
            path = Path(str(raw.get("path") or ""))
            try:
                stat = path.stat()
            except OSError as exc:
                self._clear_unlocked(reset_manifest=False)
                raise DatabentoCacheStaleError(
                    f"databento_source_missing:{path}"
                ) from exc
            expected_identity = (
                int(raw.get("size", -1)),
                int(raw.get("mtime_ns", -1)),
            )
            actual_identity = (int(stat.st_size), int(stat.st_mtime_ns))
            expected_change = raw.get("change_ns")
            expected_device = raw.get("device")
            expected_inode = raw.get("inode")
            actual_change = _source_change_ns(path, stat)
            if (
                actual_identity != expected_identity
                or (
                    expected_change is not None
                    and int(expected_change) != actual_change
                )
                or (
                    expected_device is not None
                    and int(expected_device) != int(stat.st_dev)
                )
                or (
                    expected_inode is not None
                    and int(expected_inode) != int(stat.st_ino)
                )
            ):
                self._clear_unlocked(reset_manifest=False)
                raise DatabentoCacheStaleError(
                    f"databento_source_changed:{path}: rebuild the local cache"
                )

    def _series_entry(
        self,
        manifest: Mapping[str, Any],
        root_symbol: str,
        unit: str,
        unit_number: int,
    ) -> Mapping[str, Any] | None:
        if root_symbol not in set(manifest.get("roots", [])):
            return None
        series = manifest.get("series")
        if not isinstance(series, dict):
            return None
        value = series.get(f"{root_symbol}:{timeframe_key(unit, unit_number)}")
        return value if isinstance(value, dict) else None

    def _build_missing_series(
        self, root_symbol: str, unit: str, unit_number: int
    ) -> dict[str, Any]:
        assert self._version_dir is not None
        version_manifest = _read_json(self._version_dir / "manifest.json")
        _ensure_series(
            self._version_dir,
            version_manifest,
            root_symbol,
            unit,
            unit_number,
        )
        version_manifest["built_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(self._version_dir / "manifest.json", version_manifest)
        _write_json_atomic(self.cache_root / "current.json", version_manifest)
        self._pointer_signature = None
        self._manifest = None
        return self._refresh_manifest()

    def _open_series(self, entry: Mapping[str, Any]) -> _MappedSeries:
        fingerprint = str(entry["series_fingerprint"])
        mapped = self._mapped.get(fingerprint)
        if mapped is not None:
            self._mapped.move_to_end(fingerprint)
            return mapped
        assert self._version_dir is not None
        assert self._manifest is not None
        mapped = _MappedSeries.open(
            self._version_dir / str(entry["path"]),
            expected_entry=entry,
            expected_source_fingerprint=str(self._manifest["source_fingerprint"]),
        )
        self._mapped[fingerprint] = mapped
        # Mappings are tiny virtual-memory handles; cap them separately from
        # the materialized candle byte budget.
        while len(self._mapped) > 32:
            _key, evicted = self._mapped.popitem(last=False)
            evicted.close()
        return mapped

    def _materialize_slice(
        self,
        mapped: _MappedSeries,
        *,
        user_id: str,
        contract_id: str,
        root_symbol: str,
        unit: str,
        unit_number: int,
        left: int,
        right: int,
    ) -> CachedCandleList:
        return _materialize_mapped_slice(
            mapped,
            user_id=user_id,
            contract_id=contract_id,
            root_symbol=root_symbol,
            unit=unit,
            unit_number=unit_number,
            left=left,
            right=right,
        )

    def _evict_slices(self) -> None:
        while self._slices and (
            len(self._slices) > self.max_entries or self._slice_bytes > self.max_bytes
        ):
            _key, (_candles, size) = self._slices.popitem(last=False)
            self._slice_bytes -= size
            self._evictions += 1

    def _clear_unlocked(self, *, reset_manifest: bool) -> None:
        self._slices.clear()
        self._slice_bytes = 0
        for mapped in self._mapped.values():
            mapped.close()
        self._mapped.clear()
        if reset_manifest:
            self._manifest = None
            self._version_dir = None
            self._pointer_signature = None


_DEFAULT_STORE_LOCK = threading.Lock()
_DEFAULT_STORE: DatabentoReplayStore | None = None
_DEFAULT_STORE_ROOT: Path | None = None


def get_default_databento_cache() -> DatabentoReplayStore:
    global _DEFAULT_STORE, _DEFAULT_STORE_ROOT
    root = default_cache_root()
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None or _DEFAULT_STORE_ROOT != root:
            if _DEFAULT_STORE is not None:
                _DEFAULT_STORE.clear()
            _DEFAULT_STORE = DatabentoReplayStore(root)
            _DEFAULT_STORE_ROOT = root
        return _DEFAULT_STORE


def clear_default_databento_cache() -> None:
    global _DEFAULT_STORE, _DEFAULT_STORE_ROOT
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is not None:
            _DEFAULT_STORE.clear()
        _DEFAULT_STORE = None
        _DEFAULT_STORE_ROOT = None


def _resolve_cache_root(value: str | Path | None) -> Path:
    if value is None:
        return default_cache_root()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    return path.resolve()


def _positive_int_setting(
    explicit: int | None,
    *,
    env_name: str,
    default: int,
) -> int:
    raw: Any = explicit if explicit is not None else os.getenv(env_name, str(default))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return int(default)


def _normalize_timeframes(
    values: Sequence[tuple[str, int] | str],
) -> list[tuple[str, int]]:
    output: list[tuple[str, int]] = []
    for value in values:
        if isinstance(value, str):
            parsed = parse_timeframe(value)
        else:
            parsed = (str(value[0]).strip().lower(), int(value[1]))
            _validate_timeframe(*parsed)
        if parsed not in output:
            output.append(parsed)
    if ("minute", 1) not in output:
        output.insert(0, ("minute", 1))
    return output


def _validate_timeframe(unit: str, unit_number: int) -> None:
    number = int(unit_number)
    if number <= 0:
        raise DatabentoCacheError("databento_timeframe_must_be_positive")
    if unit == "second":
        if number < 60 or number % 60:
            raise DatabentoCacheError(
                "databento_ohlcv_1m_cannot_resample_below_one_minute"
            )
        return
    if unit in {"minute", "hour"}:
        return
    if unit == "day" and number == 1:
        return
    raise DatabentoCacheError(
        f"unsupported_databento_resample_timeframe:{unit}:{number}"
    )


def _timeframe_seconds(unit: str, unit_number: int) -> int:
    if unit == "day":
        return 86_400
    base = _TIMEFRAME_SECONDS.get(unit)
    if base is None:
        raise DatabentoCacheError(f"unsupported_databento_timeframe:{unit}")
    return base * int(unit_number)


def _validate_archive_set(descriptors: Sequence[ArchiveDescriptor]) -> None:
    identities: set[tuple[str, str]] = set()
    schemas: defaultdict[str, set[str]] = defaultdict(set)
    paths: set[str] = set()
    for descriptor in descriptors:
        if descriptor.path in paths:
            raise DatabentoCacheError(f"duplicate_databento_archive:{descriptor.path}")
        paths.add(descriptor.path)
        identity = (descriptor.job_id, descriptor.sha256)
        if identity in identities:
            raise DatabentoCacheError(
                f"duplicate_databento_archive_identity:{descriptor.job_id}"
            )
        identities.add(identity)
        schemas[descriptor.root_symbol].add(descriptor.schema)
    missing = [
        root
        for root, available in sorted(schemas.items())
        if not {"definition", "ohlcv-1m"}.issubset(available)
    ]
    if missing:
        raise DatabentoCacheError(
            f"databento_definition_and_ohlcv_required:{','.join(missing)}"
        )


def _source_fingerprint(descriptors: Sequence[ArchiveDescriptor]) -> str:
    canonical = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "roll_policy_version": ROLL_POLICY_VERSION,
        "session_timezone": "America/New_York",
        "session_boundary": "18:00",
        "price_scale": PRICE_SCALE,
        "archives": [
            {
                "job_id": item.job_id,
                "root_symbol": item.root_symbol,
                "schema": item.schema,
                "sha256": item.sha256,
            }
            for item in descriptors
        ],
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _series_fingerprint(
    source_fingerprint: str,
    root_symbol: str,
    unit: str,
    unit_number: int,
) -> str:
    canonical = {
        "source_fingerprint": source_fingerprint,
        "cache_format_version": CACHE_FORMAT_VERSION,
        "roll_policy_version": ROLL_POLICY_VERSION,
        "root_symbol": root_symbol,
        "unit": unit,
        "unit_number": int(unit_number),
        "resampling": "globex_session_anchored_complete_ohlcv_v4_verified_holiday_dates",
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _slice_fingerprint(
    series_fingerprint: str,
    left: int,
    right: int,
    *,
    user_id: str,
    contract_id: str,
) -> str:
    value = (
        f"{series_fingerprint}\0{left}\0{right}\0{user_id}\0{contract_id}"
    ).encode()
    return hashlib.sha256(value).hexdigest()


def _read_current_manifest(cache_root: Path) -> dict[str, Any] | None:
    path = cache_root / "current.json"
    try:
        return _read_json(path)
    except FileNotFoundError:
        return None


def _source_stats_match(
    manifest: Mapping[str, Any], descriptors: Sequence[ArchiveDescriptor]
) -> bool:
    prior = manifest.get("archives")
    if not isinstance(prior, list) or len(prior) != len(descriptors):
        return False
    expected = {
        (
            str(item.get("path")),
            int(item.get("size", -1)),
            int(item.get("mtime_ns", -1)),
            int(item.get("change_ns", -1)),
            int(item.get("device", -1)),
            int(item.get("inode", -1)),
        )
        for item in prior
        if isinstance(item, dict)
    }
    actual = {
        (
            item.path,
            item.size,
            item.mtime_ns,
            item.change_ns,
            item.device,
            item.inode,
        )
        for item in descriptors
    }
    return expected == actual


def _source_change_ns(path: Path, stat: os.stat_result | None = None) -> int:
    """Return the filesystem change time, including NTFS ChangeTime on Windows.

    Python 3.10 exposes Windows creation time through ``st_ctime``. That value
    does not change when a caller edits a file and restores its last-write time,
    so query ``FILE_BASIC_INFO.ChangeTime`` directly when available.
    """

    current = stat if stat is not None else path.stat()
    if os.name != "nt":
        return int(current.st_ctime_ns)
    try:
        import ctypes
        from ctypes import wintypes

        class _FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandleEx
        get_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = create_file(
            str(path),
            0x0080,  # FILE_READ_ATTRIBUTES
            0x0001 | 0x0002 | 0x0004,  # read/write/delete sharing
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        try:
            info = _FileBasicInfo()
            if not get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(
                    ctypes.get_last_error(),
                    "GetFileInformationByHandleEx failed",
                )
            return int(info.ChangeTime) * 100
        finally:
            close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return int(current.st_ctime_ns)


def _all_roots_have_series(
    manifest: Mapping[str, Any],
    version_dir: Path,
    unit: str,
    unit_number: int,
) -> bool:
    series = manifest.get("series")
    if not isinstance(series, dict):
        return False
    key_suffix = timeframe_key(unit, unit_number)
    for root in manifest.get("roots", []):
        entry = series.get(f"{root}:{key_suffix}")
        if not isinstance(entry, dict) or not _series_files_complete(
            version_dir / str(entry.get("path") or ""),
            expected_entry=entry,
            expected_source_fingerprint=str(manifest.get("source_fingerprint") or ""),
        ):
            return False
    return True


def _series_files_complete(
    directory: Path,
    *,
    expected_entry: Mapping[str, Any] | None = None,
    expected_source_fingerprint: str | None = None,
) -> bool:
    if not (directory / "metadata.json").is_file() or not all(
        (directory / f"{name}.npy").is_file() for name in _ARRAY_COLUMNS
    ):
        return False
    if expected_entry is None or expected_source_fingerprint is None:
        return True
    arrays: dict[str, np.ndarray] = {}
    try:
        metadata = _read_json(directory / "metadata.json")
        expected_rows = int(expected_entry.get("rows", -1))
        expected_fields = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "roll_policy_version": ROLL_POLICY_VERSION,
            "source_fingerprint": expected_source_fingerprint,
            "series_fingerprint": str(
                expected_entry.get("series_fingerprint") or ""
            ),
            "rows": expected_rows,
        }
        for field, expected in expected_fields.items():
            actual = metadata.get(field)
            actual = int(actual) if isinstance(expected, int) else str(actual or "")
            if actual != expected:
                return False
        for name in _ARRAY_COLUMNS:
            array = np.load(directory / f"{name}.npy", mmap_mode="r")
            arrays[name] = array
            if (
                array.ndim != 1
                or array.shape != (expected_rows,)
                or array.dtype != _ARRAY_DTYPES[name]
            ):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    finally:
        for array in arrays.values():
            _close_memmap(array)


def _dbn_entries(archive: ZipFile, schema: str) -> list[ZipInfo]:
    marker = f".{schema}.dbn.zst"
    return sorted(
        [entry for entry in archive.infolist() if entry.filename.endswith(marker)],
        key=lambda entry: entry.filename,
    )


def _iter_dbn_entry(archive: ZipFile, entry: ZipInfo) -> Iterator[Any]:
    decoder = DBNDecoder(compression=Compression.ZSTD)
    try:
        with archive.open(entry, "r") as source:
            while True:
                chunk = source.read(DBN_READ_CHUNK_BYTES)
                if not chunk:
                    break
                yield from decoder.write_and_decode(chunk)
            yield from decoder.decode()
        if decoder.buffer():
            raise DatabentoCacheError(f"truncated_dbn_payload:{entry.filename}")
    except DatabentoCacheError:
        raise
    except Exception as exc:
        raise DatabentoCacheError(
            f"invalid_dbn_zstd_payload:{entry.filename}:{exc}"
        ) from exc


def _validate_dbn_metadata(
    metadata: Metadata, descriptor: ArchiveDescriptor
) -> None:
    actual_dataset = str(metadata.dataset)
    actual_schema = _schema_value(metadata.schema)
    if actual_dataset != descriptor.dataset or actual_schema != descriptor.schema:
        raise DatabentoCacheError(
            f"dbn_metadata_mismatch:{actual_dataset}:{actual_schema}"
        )


def _schema_value(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "name", None) or getattr(value, "value", None) or str(value)
    normalized = str(raw).lower().replace("_", "-")
    return {
        "ohlcv-1-m": "ohlcv-1m",
        "ohlcv1m": "ohlcv-1m",
    }.get(normalized, normalized)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _raw_symbol_root(raw_symbol: str) -> str | None:
    match = _OUTRIGHT_PATTERN.fullmatch(raw_symbol)
    return match.group(1) if match is not None else None


def _contract_key(
    raw_symbol: str,
    *,
    expiration_ns: int,
    reference_date: date,
) -> str:
    year = (
        _datetime_from_ns(expiration_ns).year
        if expiration_ns >= 0
        else _contract_year_from_symbol(raw_symbol, reference_date.year)
    )
    return f"{raw_symbol}@{year:04d}"


def _contract_year_from_symbol(raw_symbol: str, reference_year: int) -> int:
    match = _OUTRIGHT_PATTERN.fullmatch(raw_symbol)
    if match is None:
        raise DatabentoCacheError(f"invalid_outright_symbol:{raw_symbol}")
    digits = match.group(3)
    value = int(digits)
    if len(digits) == 1:
        year = (reference_year // 10) * 10 + value
        while year < reference_year - 1:
            year += 10
        while year > reference_year + 8:
            year -= 10
        return year
    if len(digits) == 2:
        year = (reference_year // 100) * 100 + value
        if year < reference_year - 20:
            year += 100
        elif year > reference_year + 79:
            year -= 100
        return year
    return value


def _resolve_contract_code(
    *,
    raw_symbol: str,
    timestamp_ns: int,
    candidates: Sequence[_Instrument],
    codes: Mapping[str, int],
    cache: dict[tuple[str, int], tuple[str, int] | None],
) -> tuple[str, int] | None:
    calendar_date = _datetime_from_ns(timestamp_ns).date()
    cache_key = (raw_symbol, calendar_date.toordinal())
    if cache_key in cache:
        return cache[cache_key]
    if _raw_symbol_root(raw_symbol) is None:
        cache[cache_key] = None
        return None
    eligible = [
        item
        for item in candidates
        if item.expiration_ns >= 0
        and (item.activation_ns < 0 or item.activation_ns <= timestamp_ns)
        and (item.expiration_ns < 0 or item.expiration_ns > timestamp_ns)
    ]
    selected = (
        min(
            eligible,
            key=lambda item: (
                item.expiration_ns if item.expiration_ns >= 0 else (1 << 63) - 1,
                item.contract_key,
            ),
        )
        if eligible
        else None
    )
    inferred_contract_key = _contract_key(
        raw_symbol,
        expiration_ns=-1,
        reference_date=calendar_date,
    )
    contract_key = (
        selected.contract_key
        if selected is not None
        else inferred_contract_key
    )
    if contract_key not in codes and inferred_contract_key in codes:
        # A later point-in-time definition can revise activation without
        # changing the delivery identity. The timestamp and raw delivery code
        # remain sufficient to disambiguate repeated one-digit decades.
        contract_key = inferred_contract_key
    code = codes.get(contract_key)
    resolved = (contract_key, int(code)) if code is not None else None
    cache[cache_key] = resolved
    return resolved


def _raw_symbols_by_code(
    manifest: Mapping[str, Any], root_symbol: str
) -> dict[str, str]:
    codes = manifest["raw_symbol_codes"][root_symbol]
    return {
        str(code): str(contract_key).rsplit("@", 1)[0]
        for contract_key, code in codes.items()
    }


def _nullable_ns(value: Any) -> int:
    parsed = int(value)
    return -1 if parsed in {0, (1 << 63) - 1, _UINT64_MAX} else parsed


def _nullable_fixed_int(value: Any) -> int:
    parsed = int(value)
    return -1 if parsed == (1 << 63) - 1 else parsed


def _validate_ohlcv(
    open_nano: int,
    high_nano: int,
    low_nano: int,
    close_nano: int,
    volume: int,
) -> None:
    if min(open_nano, high_nano, low_nano, close_nano) <= 0:
        raise DatabentoCacheError("databento_nonpositive_ohlcv_price")
    if high_nano < max(open_nano, low_nano, close_nano) or low_nano > min(
        open_nano, high_nano, close_nano
    ):
        raise DatabentoCacheError("databento_invalid_ohlcv_envelope")
    if volume < 0:
        raise DatabentoCacheError("databento_negative_ohlcv_volume")


def _read_json_entry(archive: ZipFile, filename: str) -> dict[str, Any]:
    try:
        payload = archive.read(filename)
    except KeyError as exc:
        raise DatabentoCacheError(f"databento_archive_entry_missing:{filename}") from exc
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatabentoCacheError(f"invalid_databento_json:{filename}") from exc
    if not isinstance(value, dict):
        raise DatabentoCacheError(f"invalid_databento_json_object:{filename}")
    return value


def _validate_archive_manifest(
    archive: ZipFile,
    manifest: Mapping[str, Any],
    *,
    verify_hashes: bool,
) -> None:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise DatabentoCacheError("databento_manifest_files_missing")
    for raw in files:
        if not isinstance(raw, dict):
            raise DatabentoCacheError("invalid_databento_manifest_file")
        filename = str(raw.get("filename") or "")
        try:
            entry = archive.getinfo(filename)
        except KeyError as exc:
            raise DatabentoCacheError(
                f"databento_manifest_payload_missing:{filename}"
            ) from exc
        expected_size = int(raw.get("size", -1))
        if expected_size >= 0 and int(entry.file_size) != expected_size:
            raise DatabentoCacheError(
                f"databento_manifest_size_mismatch:{filename}"
            )
        if not verify_hashes:
            continue
        expected_hash = str(raw.get("hash") or "").removeprefix("sha256:")
        if len(expected_hash) != 64:
            raise DatabentoCacheError(
                f"databento_manifest_hash_missing:{filename}"
            )
        digest = hashlib.sha256()
        with archive.open(entry, "r") as source:
            while True:
                chunk = source.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise DatabentoCacheError(
                f"databento_manifest_hash_mismatch:{filename}"
            )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DatabentoCacheError(f"invalid_databento_cache_json:{path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    _write_json(temporary, value)
    temporary.replace(path)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _datetime_from_ns(value: int) -> datetime:
    seconds, nanos = divmod(int(value), 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=nanos // 1_000
    )


def _datetime_to_ns(value: datetime) -> int:
    normalized = _as_utc(value)
    return int(normalized.timestamp()) * 1_000_000_000 + normalized.microsecond * 1_000


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _close_memmap(array: np.ndarray) -> None:
    mmap = getattr(array, "_mmap", None)
    if mmap is not None:
        try:
            mmap.close()
        except OSError:
            pass


def _validate_mapped_series(
    directory: Path,
    *,
    metadata: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
    expected_entry: Mapping[str, Any],
    expected_source_fingerprint: str,
) -> None:
    expected_metadata: dict[str, Any] = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "roll_policy_version": ROLL_POLICY_VERSION,
        "source_fingerprint": expected_source_fingerprint,
        "series_fingerprint": str(expected_entry.get("series_fingerprint") or ""),
        "rows": int(expected_entry.get("rows", -1)),
        "unit": str(expected_entry.get("unit") or ""),
        "unit_number": int(expected_entry.get("unit_number", -1)),
        "first_timestamp_ns": int(expected_entry.get("first_timestamp_ns", -1)),
        "source_end_ns": int(expected_entry.get("source_end_ns", -1)),
    }
    for field, expected in expected_metadata.items():
        actual = metadata.get(field)
        if isinstance(expected, int):
            try:
                actual = int(actual)
            except (TypeError, ValueError):
                actual = None
        else:
            actual = str(actual or "")
        if actual != expected:
            raise DatabentoCacheError(
                f"databento_series_metadata_mismatch:{directory}:{field}"
            )

    rows = expected_metadata["rows"]
    if rows <= 0:
        raise DatabentoCacheError(f"databento_series_empty:{directory}")
    for name in _ARRAY_COLUMNS:
        array = arrays.get(name)
        if array is None:
            raise DatabentoCacheError(
                f"databento_series_array_missing:{directory}:{name}"
            )
        if array.ndim != 1 or array.shape != (rows,):
            raise DatabentoCacheError(
                f"databento_series_shape_mismatch:{directory}:{name}:"
                f"{array.shape}:{rows}"
            )
        if array.dtype != _ARRAY_DTYPES[name]:
            raise DatabentoCacheError(
                f"databento_series_dtype_mismatch:{directory}:{name}:"
                f"{array.dtype}:{_ARRAY_DTYPES[name]}"
            )

    timestamps = arrays["timestamp_ns"]
    close_timestamps = arrays["close_timestamp_ns"]
    if not _array_is_strictly_increasing(timestamps):
        raise DatabentoCacheError(
            f"databento_series_timestamps_not_strictly_increasing:{directory}"
        )
    if not _array_is_strictly_increasing(close_timestamps):
        raise DatabentoCacheError(
            f"databento_series_close_timestamps_not_strictly_increasing:{directory}"
        )
    for start in range(0, rows, _SERIES_VALIDATION_CHUNK_ROWS):
        stop = min(rows, start + _SERIES_VALIDATION_CHUNK_ROWS)
        if not np.all(close_timestamps[start:stop] > timestamps[start:stop]):
            raise DatabentoCacheError(
                f"databento_series_invalid_close_boundary:{directory}"
            )
    first_timestamp_ns = int(timestamps[0])
    last_timestamp_ns = int(timestamps[-1])
    last_close_ns = int(close_timestamps[-1])
    source_end_ns = expected_metadata["source_end_ns"]
    if first_timestamp_ns != expected_metadata["first_timestamp_ns"]:
        raise DatabentoCacheError(
            f"databento_series_first_timestamp_mismatch:{directory}"
        )
    if not last_timestamp_ns < source_end_ns <= last_close_ns:
        raise DatabentoCacheError(
            f"databento_series_source_end_mismatch:{directory}"
        )


def _array_is_strictly_increasing(array: np.ndarray) -> bool:
    rows = int(array.size)
    prior: int | None = None
    for start in range(0, rows, _SERIES_VALIDATION_CHUNK_ROWS):
        stop = min(rows, start + _SERIES_VALIDATION_CHUNK_ROWS)
        chunk = array[start:stop]
        if chunk.size == 0:
            continue
        if prior is not None and int(chunk[0]) <= prior:
            return False
        if chunk.size > 1 and not np.all(chunk[1:] > chunk[:-1]):
            return False
        prior = int(chunk[-1])
    return True


def default_cache_root() -> Path:
    configured = os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        return path.resolve()
    return (Path(__file__).resolve().parents[2] / "storage" / "databento").resolve()


def timeframe_key(unit: str, unit_number: int) -> str:
    normalized = str(unit).strip().lower()
    number = int(unit_number)
    _validate_timeframe(normalized, number)
    suffix = {
        "second": "s",
        "minute": "m",
        "hour": "h",
        "day": "d",
    }[normalized]
    return f"{number}{suffix}"


def parse_timeframe(value: str) -> tuple[str, int]:
    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+)(s|m|h|d)", text)
    if match is None:
        raise DatabentoCacheError(f"invalid_databento_timeframe:{value}")
    unit = {"s": "second", "m": "minute", "h": "hour", "d": "day"}[
        match.group(2)
    ]
    number = int(match.group(1))
    _validate_timeframe(unit, number)
    return unit, number


def inspect_archive(path: str | Path, *, compute_sha256: bool = True) -> ArchiveDescriptor:
    archive_path = Path(path).expanduser().resolve()
    if not archive_path.is_file():
        raise DatabentoCacheError(f"databento_archive_not_found:{archive_path}")
    try:
        with ZipFile(archive_path) as archive:
            metadata = _read_json_entry(archive, "metadata.json")
            manifest = _read_json_entry(archive, "manifest.json")
            _validate_archive_manifest(
                archive,
                manifest,
                verify_hashes=compute_sha256,
            )
    except (BadZipFile, OSError) as exc:
        raise DatabentoCacheError(
            f"invalid_databento_zip:{archive_path.name}:{exc}"
        ) from exc

    query = metadata.get("query") if isinstance(metadata.get("query"), dict) else {}
    dataset = str(query.get("dataset") or "")
    schema = str(query.get("schema") or "")
    symbols = query.get("symbols") if isinstance(query.get("symbols"), list) else []
    job_id = str(metadata.get("job_id") or manifest.get("job_id") or "").strip()
    if dataset != DATASET:
        raise DatabentoCacheError(f"unsupported_databento_dataset:{dataset}")
    if schema not in {"definition", "ohlcv-1m", "statistics"}:
        raise DatabentoCacheError(f"unsupported_databento_schema:{schema}")
    if len(symbols) != 1 or not str(symbols[0]).upper().endswith(".FUT"):
        raise DatabentoCacheError("unsupported_databento_symbols")
    root = str(symbols[0]).upper().removesuffix(".FUT")
    if root not in SUPPORTED_ROOTS:
        raise DatabentoCacheError(f"unsupported_databento_root:{root}")
    expected = {
        "stype_in": "parent",
        "stype_out": "instrument_id",
        "encoding": "dbn",
        "compression": "zstd",
    }
    for field, value in expected.items():
        if str(query.get(field) or "").lower() != value:
            raise DatabentoCacheError(
                f"unsupported_databento_{field}:{query.get(field)}"
            )
    if not job_id or str(manifest.get("job_id") or "") != job_id:
        raise DatabentoCacheError("databento_job_id_missing_or_mismatched")
    stat = archive_path.stat()
    return ArchiveDescriptor(
        path=str(archive_path),
        name=archive_path.name,
        size=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
        change_ns=_source_change_ns(archive_path, stat),
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        sha256=_sha256_path(archive_path) if compute_sha256 else "",
        job_id=job_id,
        dataset=dataset,
        schema=schema,
        root_symbol=root,
        start_ns=int(query.get("start") or 0),
        end_ns=int(query.get("end") or 0),
    )


def build_databento_cache(
    archives: Sequence[str | Path],
    *,
    cache_root: str | Path | None = None,
    timeframes: Sequence[tuple[str, int] | str] = DEFAULT_TIMEFRAMES,
    force: bool = False,
) -> CacheBuildResult:
    """Build immutable Parquet and mmap artifacts, then atomically publish them."""

    if not archives:
        raise DatabentoCacheError("databento_archives_required")
    root = _resolve_cache_root(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    normalized_timeframes = _normalize_timeframes(timeframes)

    # The cheap identity pass makes repeat tooling calls instant. Content hashes
    # are still the canonical source identity used by every built artifact.
    quick = [inspect_archive(path, compute_sha256=False) for path in archives]
    current = _read_current_manifest(root)
    if (
        not force
        and current is not None
        and int(current.get("cache_format_version", -1)) == CACHE_FORMAT_VERSION
        and str(current.get("roll_policy_version")) == ROLL_POLICY_VERSION
        and _source_stats_match(current, quick)
    ):
        version_dir = root / str(current["version_dir"])
        missing = [
            (unit, number)
            for unit, number in normalized_timeframes
            if not _all_roots_have_series(current, version_dir, unit, number)
        ]
        if not missing:
            return _build_result(root, version_dir, current, reused=True)

    descriptors = [inspect_archive(path) for path in archives]
    descriptors.sort(
        key=lambda item: (
            item.root_symbol,
            item.schema != "definition",
            item.schema != "ohlcv-1m",
            item.start_ns,
            item.job_id,
        )
    )
    _validate_archive_set(descriptors)
    fingerprint = _source_fingerprint(descriptors)
    reusable_rel: Path | None = None
    if (
        not force
        and current is not None
        and str(current.get("source_fingerprint")) == fingerprint
    ):
        candidate_rel = Path(str(current.get("version_dir") or ""))
        candidate_dir = root / candidate_rel
        if (candidate_dir / "manifest.json").is_file():
            reusable_rel = candidate_rel
    if reusable_rel is None and not force:
        # Backward compatibility for the original deterministic V2 layout when
        # current.json was lost but the immutable version is still present.
        legacy_rel = Path("versions") / fingerprint
        if ((root / legacy_rel) / "manifest.json").is_file():
            reusable_rel = legacy_rel

    if reusable_rel is None:
        generation = uuid.uuid4().hex
        # Keep generation paths comfortably below legacy Windows MAX_PATH;
        # the complete source fingerprint remains in both manifests.
        version_rel = Path("versions") / f"{fingerprint[:12]}-{generation[:12]}"
        version_dir = root / version_rel
        staging = (
            root
            / "versions"
            / f".{fingerprint[:12]}.{uuid.uuid4().hex[:12]}.tmp"
        )
        staging.mkdir(parents=True, exist_ok=False)
        try:
            build_manifest = _build_parquet_and_rolls(staging, descriptors)
            build_manifest.update(
                {
                    "cache_format_version": CACHE_FORMAT_VERSION,
                    "roll_policy_version": ROLL_POLICY_VERSION,
                    "source_fingerprint": fingerprint,
                    "archives": [asdict(item) for item in descriptors],
                    "version_dir": str(version_rel).replace("\\", "/"),
                }
            )
            for unit, number in normalized_timeframes:
                _ensure_series_for_all_roots(staging, build_manifest, unit, number)
            build_manifest["built_at"] = datetime.now(timezone.utc).isoformat()
            _write_json(staging / "manifest.json", build_manifest)
            version_dir.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(version_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    else:
        version_rel = reusable_rel
        version_dir = root / version_rel
        build_manifest = _read_json(version_dir / "manifest.json")
        build_manifest.update(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "roll_policy_version": ROLL_POLICY_VERSION,
                "source_fingerprint": fingerprint,
                "archives": [asdict(item) for item in descriptors],
                "version_dir": str(version_rel).replace("\\", "/"),
            }
        )
        for unit, number in normalized_timeframes:
            _ensure_series_for_all_roots(version_dir, build_manifest, unit, number)
        build_manifest["built_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(version_dir / "manifest.json", build_manifest)

    manifest = _read_json(version_dir / "manifest.json")
    _write_json_atomic(root / "current.json", manifest)
    clear_default_databento_cache()
    return _build_result(root, version_dir, manifest, reused=False)


def _build_result(
    cache_root: Path,
    version_dir: Path,
    manifest: Mapping[str, Any],
    *,
    reused: bool,
) -> CacheBuildResult:
    series = manifest.get("series") if isinstance(manifest.get("series"), dict) else {}
    timeframes = sorted(
        {
            str(key).split(":", 1)[1]
            for key in series
            if ":" in str(key)
        }
    )
    records = manifest.get("records_by_schema")
    return CacheBuildResult(
        cache_root=str(cache_root),
        version_dir=str(version_dir),
        source_fingerprint=str(manifest.get("source_fingerprint") or ""),
        roots=tuple(sorted(str(value) for value in manifest.get("roots", []))),
        timeframes=tuple(timeframes),
        archive_count=len(manifest.get("archives", [])),
        records_by_schema=dict(records) if isinstance(records, dict) else {},
        reused=reused,
    )


_DEFINITION_SCHEMA = pa.schema(
    [
        ("root_symbol", pa.string()),
        ("definition_date_ordinal", pa.int32()),
        ("instrument_id", pa.uint32()),
        ("raw_symbol", pa.string()),
        ("contract_key", pa.string()),
        ("instrument_class", pa.string()),
        ("security_type", pa.string()),
        ("security_update_action", pa.string()),
        ("activation_ns", pa.int64()),
        ("expiration_ns", pa.int64()),
        ("min_price_increment_nano", pa.int64()),
        ("unit_of_measure_qty_nano", pa.int64()),
        ("definition_ts_ns", pa.int64()),
        ("source_id", pa.uint16()),
    ]
)
_OHLCV_SCHEMA = pa.schema(
    [
        ("timestamp_ns", pa.int64()),
        ("session_ordinal", pa.int32()),
        ("instrument_id", pa.uint32()),
        ("raw_symbol_code", pa.uint32()),
        ("open_nano", pa.int64()),
        ("high_nano", pa.int64()),
        ("low_nano", pa.int64()),
        ("close_nano", pa.int64()),
        ("volume", pa.uint64()),
        ("source_id", pa.uint16()),
    ]
)
_STATISTICS_SCHEMA = pa.schema(
    [
        ("timestamp_ns", pa.uint64()),
        ("reference_timestamp_ns", pa.uint64()),
        ("instrument_id", pa.uint32()),
        ("raw_symbol", pa.string()),
        ("contract_key", pa.string()),
        ("price_nano", pa.int64()),
        ("quantity", pa.uint64()),
        ("sequence", pa.uint32()),
        ("stat_type", pa.string()),
        ("update_action", pa.string()),
        ("stat_flags", pa.uint8()),
        ("source_id", pa.uint16()),
    ]
)
_ROLL_SCHEMA = pa.schema(
    [
        ("root_symbol", pa.string()),
        ("trading_date", pa.date32()),
        ("session_ordinal", pa.int32()),
        ("instrument_id", pa.uint32()),
        ("raw_symbol", pa.string()),
        ("contract_key", pa.string()),
        ("raw_symbol_code", pa.uint32()),
        ("decision_session_date", pa.date32()),
        ("from_instrument_id", pa.int64()),
        ("current_volume", pa.int64()),
        ("candidate_volume", pa.int64()),
        ("reason", pa.string()),
        ("policy_version", pa.string()),
    ]
)


class _PartitionedParquetWriter:
    def __init__(self, base: Path, schema: pa.Schema) -> None:
        self.base = base
        self.schema = schema
        self._key: tuple[str, int, int] | None = None
        self._columns: dict[str, list[Any]] = {
            field.name: [] for field in self.schema
        }
        self._parts: defaultdict[tuple[str, int, int], int] = defaultdict(int)
        self.rows_written = 0

    def append(
        self,
        root_symbol: str,
        partition_date: date,
        values: Mapping[str, Any],
    ) -> None:
        key = (root_symbol, partition_date.year, partition_date.month)
        if self._key is not None and self._key != key:
            self.flush()
        self._key = key
        for field in self.schema:
            self._columns[field.name].append(values.get(field.name))
        if len(next(iter(self._columns.values()))) >= PARQUET_BATCH_ROWS:
            self.flush()

    def flush(self) -> None:
        if self._key is None or not self._columns[self.schema[0].name]:
            return
        root_symbol, year, month = self._key
        directory = (
            self.base
            / f"root={root_symbol}"
            / f"year={year:04d}"
            / f"month={month:02d}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        part = self._parts[self._key]
        self._parts[self._key] += 1
        table = pa.Table.from_pydict(self._columns, schema=self.schema)
        pq.write_table(
            table,
            directory / f"part-{part:05d}.parquet",
            compression="zstd",
            compression_level=3,
            use_dictionary=True,
            write_statistics=True,
            row_group_size=PARQUET_BATCH_ROWS,
        )
        self.rows_written += table.num_rows
        self._columns = {field.name: [] for field in self.schema}

    def close(self) -> None:
        self.flush()


class _SessionResolver:
    """Resolve sequential UTC bars without a zone conversion per row."""

    def __init__(self) -> None:
        self.session_date: date | None = None
        self.start_ns = 0
        self.end_ns = 0

    def ordinal(self, timestamp_ns: int) -> int:
        if self.session_date is None or not (
            self.start_ns <= timestamp_ns < self.end_ns
        ):
            timestamp = _datetime_from_ns(timestamp_ns)
            session_date = trading_day_date(timestamp)
            start, end_inclusive = trading_day_bounds_utc(session_date)
            self.session_date = session_date
            self.start_ns = _datetime_to_ns(start)
            self.end_ns = _datetime_to_ns(end_inclusive) + 1_000
        return self.session_date.toordinal()


class _MappingResolver:
    def __init__(self, metadata: Metadata, *, root_symbol: str) -> None:
        self._by_day_id: dict[tuple[int, int], str] = {}
        prefix = root_symbol.upper()
        mappings = metadata.mappings
        iterable = (
            mappings.items()
            if isinstance(mappings, dict)
            else ((mapping.raw_symbol, mapping.intervals) for mapping in mappings)
        )
        for raw_value, intervals in iterable:
            raw_symbol = str(raw_value).strip().upper()
            if not raw_symbol.startswith(prefix):
                continue
            for interval in intervals:
                try:
                    instrument_id = int(
                        interval.get("symbol")
                        if isinstance(interval, dict)
                        else interval.symbol
                    )
                except (TypeError, ValueError):
                    continue
                start = (
                    interval.get("start_date")
                    if isinstance(interval, dict)
                    else interval.start_date
                )
                end = (
                    interval.get("end_date")
                    if isinstance(interval, dict)
                    else interval.end_date
                )
                day = start
                while day < end:
                    self._by_day_id[(day.toordinal(), instrument_id)] = raw_symbol
                    day += timedelta(days=1)

    def resolve(self, timestamp_ns: int, instrument_id: int) -> str | None:
        calendar_day = _datetime_from_ns(timestamp_ns).date().toordinal()
        return self._by_day_id.get((calendar_day, int(instrument_id)))


def _build_parquet_and_rolls(
    version_dir: Path,
    descriptors: Sequence[ArchiveDescriptor],
) -> dict[str, Any]:
    parquet_root = version_dir / "parquet"
    definition_writer = _PartitionedParquetWriter(
        parquet_root / "definitions", _DEFINITION_SCHEMA
    )
    ohlcv_writer = _PartitionedParquetWriter(parquet_root / "ohlcv_1m", _OHLCV_SCHEMA)
    statistics_writer = _PartitionedParquetWriter(
        parquet_root / "statistics", _STATISTICS_SCHEMA
    )
    records_by_schema: defaultdict[str, int] = defaultdict(int)
    contracts: dict[str, dict[str, list[_Instrument]]] = {
        root: {} for root in sorted({item.root_symbol for item in descriptors})
    }
    unique_instrument_mapping: dict[str, dict[int, str | None]] = {
        root: {} for root in contracts
    }

    for source_id, descriptor in enumerate(descriptors):
        if descriptor.schema != "definition":
            continue
        with ZipFile(descriptor.path) as archive:
            for entry in _dbn_entries(archive, descriptor.schema):
                metadata_seen = False
                for record in _iter_dbn_entry(archive, entry):
                    if isinstance(record, Metadata):
                        _validate_dbn_metadata(record, descriptor)
                        metadata_seen = True
                        continue
                    if not isinstance(record, InstrumentDefMsg):
                        raise DatabentoCacheError(
                            f"unexpected_dbn_record:{entry.filename}:{type(record).__name__}"
                        )
                    records_by_schema[descriptor.schema] += 1
                    raw_symbol = str(record.raw_symbol).strip().upper()
                    instrument_class = _enum_value(record.instrument_class)
                    security_update_action = _enum_value(record.security_update_action)
                    definition_ts_ns = int(record.ts_recv)
                    definition_date = _datetime_from_ns(definition_ts_ns).date()
                    activation_ns = _nullable_ns(record.activation)
                    expiration_ns = _nullable_ns(record.expiration)
                    contract_key = (
                        _contract_key(
                            raw_symbol,
                            expiration_ns=expiration_ns,
                            reference_date=definition_date,
                        )
                        if instrument_class == "F"
                        and _raw_symbol_root(raw_symbol) == descriptor.root_symbol
                        else ""
                    )
                    instrument = _Instrument(
                        root_symbol=descriptor.root_symbol,
                        instrument_id=int(record.instrument_id),
                        raw_symbol=raw_symbol,
                        contract_key=contract_key,
                        instrument_class=instrument_class,
                        security_type=str(record.security_type or ""),
                        activation_ns=activation_ns,
                        expiration_ns=expiration_ns,
                        min_price_increment_nano=_nullable_fixed_int(
                            record.min_price_increment
                        ),
                        unit_of_measure_qty_nano=_nullable_fixed_int(
                            record.unit_of_measure_qty
                        ),
                        definition_ts_ns=definition_ts_ns,
                        source_sha256=descriptor.sha256,
                        security_update_action=security_update_action,
                    )
                    prior_mapping = unique_instrument_mapping[
                        descriptor.root_symbol
                    ].get(instrument.instrument_id)
                    if prior_mapping is None and instrument.instrument_id not in unique_instrument_mapping[
                        descriptor.root_symbol
                    ]:
                        unique_instrument_mapping[descriptor.root_symbol][
                            instrument.instrument_id
                        ] = raw_symbol
                    elif prior_mapping != raw_symbol:
                        unique_instrument_mapping[descriptor.root_symbol][
                            instrument.instrument_id
                        ] = None
                    definition_writer.append(
                        descriptor.root_symbol,
                        definition_date,
                        {
                            "root_symbol": descriptor.root_symbol,
                            "definition_date_ordinal": definition_date.toordinal(),
                            "instrument_id": instrument.instrument_id,
                            "raw_symbol": raw_symbol,
                            "contract_key": contract_key,
                            "instrument_class": instrument_class,
                            "security_type": instrument.security_type,
                            "security_update_action": security_update_action,
                            "activation_ns": instrument.activation_ns,
                            "expiration_ns": instrument.expiration_ns,
                            "min_price_increment_nano": instrument.min_price_increment_nano,
                            "unit_of_measure_qty_nano": instrument.unit_of_measure_qty_nano,
                            "definition_ts_ns": definition_ts_ns,
                            "source_id": source_id,
                        },
                    )
                    if (
                        instrument_class == "F"
                        and _raw_symbol_root(raw_symbol) == descriptor.root_symbol
                    ):
                        contracts[descriptor.root_symbol].setdefault(
                            contract_key, []
                        ).append(instrument)
                if not metadata_seen:
                    raise DatabentoCacheError(f"dbn_metadata_missing:{entry.filename}")
    definition_writer.close()

    missing_definitions = [root for root, values in contracts.items() if not values]
    if missing_definitions:
        raise DatabentoCacheError(
            f"databento_outright_definitions_missing:{','.join(missing_definitions)}"
        )
    raw_symbol_codes: dict[str, dict[str, int]] = {
        root: {
            contract_key: index + 1
            for index, contract_key in enumerate(sorted(root_contracts))
        }
        for root, root_contracts in contracts.items()
    }
    contracts_by_raw: dict[str, dict[str, list[_Instrument]]] = {
        root: defaultdict(list) for root in contracts
    }
    for root, root_contracts in contracts.items():
        for timeline in root_contracts.values():
            timeline.sort(key=lambda item: item.definition_ts_ns)
            instrument = timeline[-1]
            contracts_by_raw[root][instrument.raw_symbol].append(instrument)
        for candidates in contracts_by_raw[root].values():
            candidates.sort(key=lambda item: item.expiration_ns)
    contract_code_cache: dict[str, dict[tuple[str, int], tuple[str, int] | None]] = {
        root: {} for root in contracts
    }
    daily_volumes: dict[str, dict[date, dict[str, int]]] = {
        root: {} for root in contracts
    }
    raw_bounds: dict[str, list[int]] = {root: [0, 0] for root in contracts}

    for source_id, descriptor in enumerate(descriptors):
        if descriptor.schema != "ohlcv-1m":
            continue
        session_resolver = _SessionResolver()
        prior_timestamp_ns: int | None = None
        with ZipFile(descriptor.path) as archive:
            for entry in _dbn_entries(archive, descriptor.schema):
                mapping: _MappingResolver | None = None
                for record in _iter_dbn_entry(archive, entry):
                    if isinstance(record, Metadata):
                        _validate_dbn_metadata(record, descriptor)
                        mapping = _MappingResolver(
                            record, root_symbol=descriptor.root_symbol
                        )
                        continue
                    if not isinstance(record, OHLCVMsg):
                        raise DatabentoCacheError(
                            f"unexpected_dbn_record:{entry.filename}:{type(record).__name__}"
                        )
                    records_by_schema[descriptor.schema] += 1
                    if mapping is None:
                        raise DatabentoCacheError(f"dbn_metadata_missing:{entry.filename}")
                    timestamp_ns = int(record.ts_event)
                    if prior_timestamp_ns is not None and timestamp_ns < prior_timestamp_ns:
                        raise DatabentoCacheError(
                            f"databento_rows_not_monotonic:{descriptor.job_id}"
                        )
                    prior_timestamp_ns = timestamp_ns
                    instrument_id = int(record.instrument_id)
                    raw_symbol = mapping.resolve(timestamp_ns, instrument_id)
                    if raw_symbol is None:
                        raw_symbol = unique_instrument_mapping[
                            descriptor.root_symbol
                        ].get(instrument_id)
                    if raw_symbol is None:
                        raise DatabentoCacheError(
                            f"databento_mapping_missing:{descriptor.root_symbol}:{instrument_id}:{timestamp_ns}"
                        )
                    resolved_contract = _resolve_contract_code(
                        raw_symbol=raw_symbol,
                        timestamp_ns=timestamp_ns,
                        candidates=contracts_by_raw[descriptor.root_symbol].get(
                            raw_symbol, []
                        ),
                        codes=raw_symbol_codes[descriptor.root_symbol],
                        cache=contract_code_cache[descriptor.root_symbol],
                    )
                    if resolved_contract is None:
                        raw_root = _raw_symbol_root(raw_symbol)
                        if raw_root is None:
                            # Parent streams contain spreads; they are retained
                            # in definitions/statistics but excluded from rolls.
                            continue
                        raise DatabentoCacheError(
                            "databento_outright_definition_missing:"
                            f"{descriptor.root_symbol}:{raw_symbol}:{instrument_id}:"
                            f"{timestamp_ns}"
                        )
                    contract_key, code = resolved_contract
                    open_nano = int(record.open)
                    high_nano = int(record.high)
                    low_nano = int(record.low)
                    close_nano = int(record.close)
                    volume = int(record.volume)
                    _validate_ohlcv(
                        open_nano, high_nano, low_nano, close_nano, volume
                    )
                    session_ordinal = session_resolver.ordinal(timestamp_ns)
                    session_date = date.fromordinal(session_ordinal)
                    ohlcv_writer.append(
                        descriptor.root_symbol,
                        session_date,
                        {
                            "timestamp_ns": timestamp_ns,
                            "session_ordinal": session_ordinal,
                            "instrument_id": instrument_id,
                            "raw_symbol_code": code,
                            "open_nano": open_nano,
                            "high_nano": high_nano,
                            "low_nano": low_nano,
                            "close_nano": close_nano,
                            "volume": volume,
                            "source_id": source_id,
                        },
                    )
                    by_contract = daily_volumes[descriptor.root_symbol].setdefault(
                        session_date, {}
                    )
                    by_contract[contract_key] = (
                        by_contract.get(contract_key, 0) + volume
                    )
                    bounds = raw_bounds[descriptor.root_symbol]
                    if bounds[0] == 0 or timestamp_ns < bounds[0]:
                        bounds[0] = timestamp_ns
                    if timestamp_ns > bounds[1]:
                        bounds[1] = timestamp_ns
                if mapping is None:
                    raise DatabentoCacheError(f"dbn_metadata_missing:{entry.filename}")
    ohlcv_writer.close()

    for source_id, descriptor in enumerate(descriptors):
        if descriptor.schema != "statistics":
            continue
        with ZipFile(descriptor.path) as archive:
            for entry in _dbn_entries(archive, descriptor.schema):
                mapping: _MappingResolver | None = None
                for record in _iter_dbn_entry(archive, entry):
                    if isinstance(record, Metadata):
                        _validate_dbn_metadata(record, descriptor)
                        mapping = _MappingResolver(
                            record, root_symbol=descriptor.root_symbol
                        )
                        continue
                    if not isinstance(record, StatMsg):
                        raise DatabentoCacheError(
                            f"unexpected_dbn_record:{entry.filename}:{type(record).__name__}"
                        )
                    records_by_schema[descriptor.schema] += 1
                    timestamp_ns = int(record.ts_event)
                    raw_symbol = (
                        mapping.resolve(timestamp_ns, int(record.instrument_id))
                        if mapping is not None
                        else None
                    )
                    reference_ns = int(record.ts_ref)
                    contract_key = ""
                    if raw_symbol:
                        lookup_ns = (
                            reference_ns
                            if 0 < reference_ns < (1 << 63) - 1
                            else timestamp_ns
                        )
                        resolved_contract = _resolve_contract_code(
                            raw_symbol=raw_symbol,
                            timestamp_ns=lookup_ns,
                            candidates=contracts_by_raw[descriptor.root_symbol].get(
                                raw_symbol, []
                            ),
                            codes=raw_symbol_codes[descriptor.root_symbol],
                            cache=contract_code_cache[descriptor.root_symbol],
                        )
                        if resolved_contract is not None:
                            contract_key = resolved_contract[0]
                    statistics_writer.append(
                        descriptor.root_symbol,
                        _datetime_from_ns(timestamp_ns).date(),
                        {
                            "timestamp_ns": timestamp_ns,
                            "reference_timestamp_ns": reference_ns,
                            "instrument_id": int(record.instrument_id),
                            "raw_symbol": raw_symbol or "",
                            "contract_key": contract_key,
                            "price_nano": int(record.price),
                            "quantity": int(record.quantity),
                            "sequence": int(record.sequence),
                            "stat_type": _enum_value(record.stat_type),
                            "update_action": _enum_value(record.update_action),
                            "stat_flags": int(record.stat_flags),
                            "source_id": source_id,
                        },
                    )
                if mapping is None:
                    raise DatabentoCacheError(f"dbn_metadata_missing:{entry.filename}")
    statistics_writer.close()

    roll_counts: dict[str, int] = {}
    for root_symbol in sorted(contracts):
        decisions = _build_roll_schedule(
            root_symbol=root_symbol,
            contracts=[
                definition
                for timeline in contracts[root_symbol].values()
                for definition in timeline
            ],
            daily_volumes=daily_volumes[root_symbol],
        )
        if not decisions:
            raise DatabentoCacheError(f"databento_roll_schedule_missing:{root_symbol}")
        roll_dir = parquet_root / "rolls" / f"root={root_symbol}"
        roll_dir.mkdir(parents=True, exist_ok=True)
        code_by_symbol = raw_symbol_codes[root_symbol]
        pq.write_table(
            pa.Table.from_pydict(
                {
                    "root_symbol": [item.root_symbol for item in decisions],
                    "trading_date": [item.trading_date for item in decisions],
                    "session_ordinal": [
                        item.trading_date.toordinal() for item in decisions
                    ],
                    "instrument_id": [item.instrument_id for item in decisions],
                    "raw_symbol": [item.raw_symbol for item in decisions],
                    "contract_key": [item.contract_key for item in decisions],
                    "raw_symbol_code": [
                        code_by_symbol[item.contract_key] for item in decisions
                    ],
                    "decision_session_date": [
                        item.decision_session_date for item in decisions
                    ],
                    "from_instrument_id": [
                        item.from_instrument_id for item in decisions
                    ],
                    "current_volume": [item.current_volume for item in decisions],
                    "candidate_volume": [item.candidate_volume for item in decisions],
                    "reason": [item.reason for item in decisions],
                    "policy_version": [item.policy_version for item in decisions],
                },
                schema=_ROLL_SCHEMA,
            ),
            roll_dir / "rolls.parquet",
            compression="zstd",
            use_dictionary=True,
        )
        roll_counts[root_symbol] = len(decisions)

    return {
        "roots": sorted(contracts),
        "records_by_schema": dict(sorted(records_by_schema.items())),
        "parquet_rows": {
            "definitions": definition_writer.rows_written,
            "ohlcv_1m": ohlcv_writer.rows_written,
            "statistics": statistics_writer.rows_written,
        },
        "raw_bounds_ns": raw_bounds,
        "raw_symbol_codes": raw_symbol_codes,
        "roll_schedule_rows": roll_counts,
        "series": {},
    }


def _build_roll_schedule(
    *,
    root_symbol: str,
    contracts: Sequence[_Instrument],
    daily_volumes: Mapping[date, Mapping[str, int]],
) -> list[_RollDecision]:
    """Choose session D from D-1 volume and definitions known by D's open."""

    sessions = sorted(daily_volumes)
    if not sessions:
        return []
    definitions_by_key: defaultdict[str, list[_Instrument]] = defaultdict(list)
    for contract in contracts:
        definitions_by_key[contract.contract_key].append(contract)
    timelines: dict[str, tuple[list[int], list[_Instrument]]] = {}
    for contract_key, definitions in definitions_by_key.items():
        definitions.sort(key=lambda item: item.definition_ts_ns)
        timelines[contract_key] = (
            [item.definition_ts_ns for item in definitions],
            definitions,
        )

    current: _Instrument | None = None
    current_key: str | None = None
    prior_session: date | None = None
    output: list[_RollDecision] = []
    for session_date in sessions:
        session_start, _ = trading_day_bounds_utc(session_date)
        session_start_ns = _datetime_to_ns(session_start)
        point_in_time: list[_Instrument] = []
        for definition_times, definitions in timelines.values():
            index = bisect_right(definition_times, session_start_ns) - 1
            if index < 0:
                continue
            contract = definitions[index]
            if contract.security_update_action.upper() == "D":
                continue
            if (
                contract.activation_ns < 0
                or contract.activation_ns <= session_start_ns
            ) and (
                contract.expiration_ns < 0
                or contract.expiration_ns > session_start_ns
            ):
                point_in_time.append(contract)
        eligible = sorted(
            point_in_time,
            key=lambda item: (
                item.expiration_ns
                if item.expiration_ns >= 0
                else (1 << 63) - 1,
                item.raw_symbol,
                item.contract_key,
            ),
        )
        if not eligible:
            prior_session = session_date
            continue
        before = current
        current_volume: int | None = None
        candidate_volume: int | None = None
        eligible_by_key = {item.contract_key: item for item in eligible}
        if current_key is None:
            current = eligible[0]
            current_key = current.contract_key
            reason = "initial_front_contract"
        elif current_key not in eligible_by_key:
            assert current is not None
            later = _later_contracts(current, eligible)
            current = later[0] if later else eligible[0]
            current_key = current.contract_key
            reason = "expiration_fallback"
        else:
            current = eligible_by_key[current_key]
            later = _later_contracts(current, eligible)
            candidate = later[0] if later else None
            prior = daily_volumes.get(prior_session, {}) if prior_session else {}
            current_volume = (
                int(prior.get(current.contract_key, 0)) if prior_session else None
            )
            candidate_volume = (
                int(prior.get(candidate.contract_key, 0))
                if prior_session and candidate is not None
                else None
            )
            if (
                candidate is not None
                and current_volume is not None
                and candidate_volume is not None
                and candidate_volume > current_volume
            ):
                current = candidate
                current_key = current.contract_key
                reason = "next_contract_volume_exceeded_current"
            else:
                reason = "kept_current_contract"
        assert current is not None
        output.append(
            _RollDecision(
                root_symbol=root_symbol,
                trading_date=session_date,
                instrument_id=current.instrument_id,
                raw_symbol=current.raw_symbol,
                contract_key=current.contract_key,
                decision_session_date=prior_session,
                from_instrument_id=before.instrument_id if before is not None else None,
                current_volume=current_volume,
                candidate_volume=candidate_volume,
                reason=reason,
            )
        )
        prior_session = session_date
    return output


def _later_contracts(
    current: _Instrument, eligible: Sequence[_Instrument]
) -> list[_Instrument]:
    current_expiration = (
        current.expiration_ns if current.expiration_ns >= 0 else (1 << 63) - 1
    )
    return [
        item
        for item in eligible
        if (item.expiration_ns if item.expiration_ns >= 0 else (1 << 63) - 1)
        > current_expiration
    ]


def _ensure_series_for_all_roots(
    version_dir: Path,
    manifest: dict[str, Any],
    unit: str,
    unit_number: int,
) -> None:
    for root_symbol in manifest.get("roots", []):
        _ensure_series(version_dir, manifest, str(root_symbol), unit, unit_number)


def _ensure_series(
    version_dir: Path,
    manifest: dict[str, Any],
    root_symbol: str,
    unit: str,
    unit_number: int,
) -> Mapping[str, Any]:
    key = f"{root_symbol}:{timeframe_key(unit, unit_number)}"
    series = manifest.setdefault("series", {})
    existing = series.get(key)
    if isinstance(existing, dict):
        series_dir = version_dir / str(existing.get("path") or "")
        if _series_files_complete(
            series_dir,
            expected_entry=existing,
            expected_source_fingerprint=str(manifest.get("source_fingerprint") or ""),
        ):
            return existing
    if (unit, int(unit_number)) != ("minute", 1):
        _ensure_series(version_dir, manifest, root_symbol, "minute", 1)
    fingerprint = _series_fingerprint(
        str(manifest["source_fingerprint"]), root_symbol, unit, unit_number
    )
    relative = (
        Path("arrays")
        / f"root={root_symbol}"
        / f"timeframe={timeframe_key(unit, unit_number)}"
        / f"fingerprint={fingerprint[:_SERIES_PATH_FINGERPRINT_LENGTH]}"
    )
    target = version_dir / relative
    # A valid manifest entry returned above. Anything already occupying this
    # shortened path is therefore stale, incomplete, or an extraordinarily
    # unlikely prefix collision; rebuild it and validate the full fingerprint
    # from metadata instead of reusing it by path alone.
    if target.exists():
        shutil.rmtree(target)
    if not target.exists():
        temporary = (
            target.parent
            / f".{fingerprint[:12]}.{uuid.uuid4().hex[:12]}.tmp"
        )
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            if (unit, int(unit_number)) == ("minute", 1):
                metadata = _build_continuous_minute_series(
                    version_dir,
                    manifest,
                    root_symbol=root_symbol,
                    target=temporary,
                    fingerprint=fingerprint,
                )
            else:
                metadata = _build_resampled_series(
                    version_dir,
                    manifest,
                    root_symbol=root_symbol,
                    unit=unit,
                    unit_number=unit_number,
                    target=temporary,
                    fingerprint=fingerprint,
                )
            _write_json(temporary / "metadata.json", metadata)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    metadata = _read_json(target / "metadata.json")
    entry = {
        "path": str(relative).replace("\\", "/"),
        "series_fingerprint": fingerprint,
        "rows": int(metadata["rows"]),
        "unit": unit,
        "unit_number": int(unit_number),
        "first_timestamp_ns": int(metadata["first_timestamp_ns"]),
        "source_end_ns": int(metadata["source_end_ns"]),
    }
    series[key] = entry
    return entry


def _build_continuous_minute_series(
    version_dir: Path,
    manifest: Mapping[str, Any],
    *,
    root_symbol: str,
    target: Path,
    fingerprint: str,
) -> dict[str, Any]:
    files = sorted(
        (version_dir / "parquet" / "ohlcv_1m" / f"root={root_symbol}").rglob(
            "*.parquet"
        )
    )
    if not files:
        raise DatabentoCacheError(f"databento_parquet_missing:{root_symbol}")
    roll_table = pq.read_table(
        version_dir
        / "parquet"
        / "rolls"
        / f"root={root_symbol}"
        / "rolls.parquet"
    )
    roll_days = roll_table["session_ordinal"].to_numpy(zero_copy_only=False)
    roll_codes = roll_table["raw_symbol_code"].to_numpy(zero_copy_only=False)
    minimum_day = int(roll_days.min())
    maximum_day = int(roll_days.max())
    lookup = np.zeros(maximum_day - minimum_day + 1, dtype=np.uint32)
    lookup[roll_days.astype(np.int64) - minimum_day] = roll_codes

    def selected_batches() -> Iterator[dict[str, np.ndarray]]:
        columns = [field.name for field in _OHLCV_SCHEMA]
        for file in files:
            parquet = pq.ParquetFile(file)
            for batch in parquet.iter_batches(
                batch_size=PARQUET_BATCH_ROWS, columns=columns, use_threads=True
            ):
                values = {
                    name: batch.column(index).to_numpy(zero_copy_only=False)
                    for index, name in enumerate(columns)
                }
                offsets = values["session_ordinal"].astype(np.int64) - minimum_day
                valid = (offsets >= 0) & (offsets < lookup.size)
                expected = np.zeros(offsets.shape, dtype=np.uint32)
                expected[valid] = lookup[offsets[valid]]
                mask = valid & (values["raw_symbol_code"] == expected)
                if mask.any():
                    yield {name: value[mask] for name, value in values.items()}

    row_count = 0
    last_timestamp: int | None = None
    for batch in selected_batches():
        timestamps = batch["timestamp_ns"]
        if timestamps.size:
            if last_timestamp is not None and int(timestamps[0]) <= last_timestamp:
                raise DatabentoCacheError(
                    f"databento_continuous_rows_not_unique:{root_symbol}"
                )
            if np.any(np.diff(timestamps.astype(np.int64)) <= 0):
                raise DatabentoCacheError(
                    f"databento_continuous_rows_not_unique:{root_symbol}"
                )
            last_timestamp = int(timestamps[-1])
            row_count += int(timestamps.size)
    if row_count == 0:
        raise DatabentoCacheError(f"databento_continuous_rows_missing:{root_symbol}")

    dtypes: dict[str, Any] = {
        "timestamp_ns": np.int64,
        "close_timestamp_ns": np.int64,
        "open_nano": np.int64,
        "high_nano": np.int64,
        "low_nano": np.int64,
        "close_nano": np.int64,
        "volume": np.uint64,
        "instrument_id": np.uint32,
        "raw_symbol_code": np.uint32,
        "session_ordinal": np.int32,
    }
    output = {
        name: np.lib.format.open_memmap(
            target / f"{name}.npy", mode="w+", dtype=dtype, shape=(row_count,)
        )
        for name, dtype in dtypes.items()
    }
    offset = 0
    for batch in selected_batches():
        count = int(batch["timestamp_ns"].size)
        stop = offset + count
        for name in (
            "timestamp_ns",
            "open_nano",
            "high_nano",
            "low_nano",
            "close_nano",
            "volume",
            "instrument_id",
            "raw_symbol_code",
            "session_ordinal",
        ):
            output[name][offset:stop] = batch[name]
        output["close_timestamp_ns"][offset:stop] = (
            batch["timestamp_ns"].astype(np.int64) + 60_000_000_000
        )
        offset = stop
    first_timestamp_ns = int(output["timestamp_ns"][0])
    source_end_ns = int(output["close_timestamp_ns"][-1])
    for array in output.values():
        array.flush()
        _close_memmap(array)
    del array
    del output
    gc.collect()
    return {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "source_fingerprint": manifest["source_fingerprint"],
        "series_fingerprint": fingerprint,
        "roll_policy_version": ROLL_POLICY_VERSION,
        "root_symbol": root_symbol,
        "unit": "minute",
        "unit_number": 1,
        "rows": row_count,
        "first_timestamp_ns": first_timestamp_ns,
        "source_end_ns": source_end_ns,
        "raw_symbols_by_code": _raw_symbols_by_code(manifest, root_symbol),
    }


def _build_resampled_series(
    version_dir: Path,
    manifest: Mapping[str, Any],
    *,
    root_symbol: str,
    unit: str,
    unit_number: int,
    target: Path,
    fingerprint: str,
) -> dict[str, Any]:
    base_entry = manifest["series"][f"{root_symbol}:1m"]
    base_dir = version_dir / str(base_entry["path"])
    arrays = {
        name: np.load(base_dir / f"{name}.npy", mmap_mode="r")
        for name in _ARRAY_COLUMNS
    }
    rows = int(arrays["timestamp_ns"].size)
    if rows == 0:
        raise DatabentoCacheError(f"databento_continuous_rows_missing:{root_symbol}")
    session_ordinals = np.asarray(arrays["session_ordinal"], dtype=np.int32)
    minimum_day = int(session_ordinals.min())
    maximum_day = int(session_ordinals.max())
    starts_by_day = np.zeros(maximum_day - minimum_day + 1, dtype=np.int64)
    ends_by_day = np.zeros(maximum_day - minimum_day + 1, dtype=np.int64)
    for ordinal in np.unique(session_ordinals):
        session_start, session_end_inclusive = trading_day_bounds_utc(
            date.fromordinal(int(ordinal))
        )
        index = int(ordinal) - minimum_day
        starts_by_day[index] = _datetime_to_ns(session_start)
        ends_by_day[index] = _datetime_to_ns(session_end_inclusive) + 1_000
    day_offsets = session_ordinals.astype(np.int64) - minimum_day
    session_starts = starts_by_day[day_offsets]
    session_ends = ends_by_day[day_offsets]
    timestamps = np.asarray(arrays["timestamp_ns"], dtype=np.int64)
    if unit == "day":
        bucket_starts = session_starts
        bucket_ends = session_ends
    else:
        duration_ns = _timeframe_seconds(unit, unit_number) * 1_000_000_000
        bucket_starts = session_starts + (
            (timestamps - session_starts) // duration_ns
        ) * duration_ns
        bucket_ends = np.minimum(bucket_starts + duration_ns, session_ends)
    instrument_ids = np.asarray(arrays["instrument_id"], dtype=np.uint32)
    raw_symbol_codes = np.asarray(arrays["raw_symbol_code"], dtype=np.uint32)
    transitions = (
        (bucket_starts[1:] != bucket_starts[:-1])
        | (raw_symbol_codes[1:] != raw_symbol_codes[:-1])
        | (instrument_ids[1:] != instrument_ids[:-1])
    )
    group_starts = np.concatenate(
        (np.array([0], dtype=np.int64), np.flatnonzero(transitions) + 1)
    )
    group_ends = np.concatenate(
        (group_starts[1:], np.array([rows], dtype=np.int64))
    )
    complete_groups = _complete_resample_group_mask(
        timestamps=timestamps,
        bucket_starts=bucket_starts,
        bucket_ends=bucket_ends,
        group_starts=group_starts,
        group_ends=group_ends,
        root_symbol=root_symbol,
    )
    output_rows = int(np.count_nonzero(complete_groups))
    if output_rows == 0:
        raise DatabentoCacheError(
            f"databento_complete_resampled_rows_missing:{root_symbol}:{unit}:{unit_number}"
        )
    dtypes: dict[str, Any] = {
        "timestamp_ns": np.int64,
        "close_timestamp_ns": np.int64,
        "open_nano": np.int64,
        "high_nano": np.int64,
        "low_nano": np.int64,
        "close_nano": np.int64,
        "volume": np.uint64,
        "instrument_id": np.uint32,
        "raw_symbol_code": np.uint32,
        "session_ordinal": np.int32,
    }
    output = {
        name: np.lib.format.open_memmap(
            target / f"{name}.npy", mode="w+", dtype=dtype, shape=(output_rows,)
        )
        for name, dtype in dtypes.items()
    }
    selected_starts = group_starts[complete_groups]
    selected_ends = group_ends[complete_groups]
    output["timestamp_ns"][:] = bucket_starts[selected_starts]
    output["close_timestamp_ns"][:] = bucket_ends[selected_starts]
    output["open_nano"][:] = np.asarray(arrays["open_nano"])[selected_starts]
    output["high_nano"][:] = np.maximum.reduceat(
        np.asarray(arrays["high_nano"]), group_starts
    )[complete_groups]
    output["low_nano"][:] = np.minimum.reduceat(
        np.asarray(arrays["low_nano"]), group_starts
    )[complete_groups]
    output["close_nano"][:] = np.asarray(arrays["close_nano"])[selected_ends - 1]
    output["volume"][:] = np.add.reduceat(
        np.asarray(arrays["volume"], dtype=np.uint64), group_starts
    )[complete_groups]
    output["instrument_id"][:] = instrument_ids[selected_starts]
    output["raw_symbol_code"][:] = raw_symbol_codes[selected_starts]
    output["session_ordinal"][:] = session_ordinals[selected_starts]
    first_timestamp_ns = int(output["timestamp_ns"][0])
    source_end_ns = int(output["close_timestamp_ns"][-1])
    for array in output.values():
        array.flush()
        _close_memmap(array)
    del array
    del output, arrays
    gc.collect()
    return {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "source_fingerprint": manifest["source_fingerprint"],
        "series_fingerprint": fingerprint,
        "roll_policy_version": ROLL_POLICY_VERSION,
        "root_symbol": root_symbol,
        "unit": unit,
        "unit_number": int(unit_number),
        "rows": output_rows,
        "first_timestamp_ns": first_timestamp_ns,
        "source_end_ns": source_end_ns,
        "raw_symbols_by_code": _raw_symbols_by_code(manifest, root_symbol),
    }


def _complete_resample_group_mask(
    *,
    timestamps: np.ndarray,
    bucket_starts: np.ndarray,
    bucket_ends: np.ndarray,
    group_starts: np.ndarray,
    group_ends: np.ndarray,
    root_symbol: str,
) -> np.ndarray:
    """Drop aggregates that omit any open-session one-minute constituent."""

    minute_ns = 60_000_000_000
    first_timestamps = timestamps[group_starts]
    last_timestamps = timestamps[group_ends - 1]
    row_counts = group_ends - group_starts
    contiguous_counts = ((last_timestamps - first_timestamps) // minute_ns) + 1
    suspect = (
        (row_counts != contiguous_counts)
        | (first_timestamps != bucket_starts[group_starts])
        | (last_timestamps + minute_ns != bucket_ends[group_starts])
    )
    if not bool(np.any(suspect)):
        return np.ones(group_starts.size, dtype=np.bool_)

    complete = np.ones(group_starts.size, dtype=np.bool_)
    for group_index in np.flatnonzero(suspect):
        start_index = int(group_starts[group_index])
        end_index = int(group_ends[group_index])
        complete[group_index] = _covers_every_open_minute(
            np.asarray(timestamps[start_index:end_index], dtype=np.int64),
            start_ns=int(bucket_starts[start_index]),
            end_ns=int(bucket_ends[start_index]),
            root_symbol=root_symbol,
        )
    return complete


def _covers_every_open_minute(
    actual_timestamps: np.ndarray,
    *,
    start_ns: int,
    end_ns: int,
    root_symbol: str,
) -> bool:
    minute_ns = 60_000_000_000
    actual_index = 0
    cursor_ns = int(start_ns)
    while cursor_ns < int(end_ns):
        cursor = _datetime_from_ns(cursor_ns)
        if futures_session_is_open(cursor, symbol=root_symbol):
            if (
                actual_index >= int(actual_timestamps.size)
                or int(actual_timestamps[actual_index]) != cursor_ns
            ):
                return False
            actual_index += 1
        cursor_ns += minute_ns
    return actual_index == int(actual_timestamps.size)
