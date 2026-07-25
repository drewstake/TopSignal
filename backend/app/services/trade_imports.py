from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ProjectXTradeEvent, TradeImportBatch
from .instruments import normalize_symbol_key


MAX_TRADE_IMPORT_BYTES = 10 * 1024 * 1024
MAX_TRADE_IMPORT_ROWS = 5_000
MAX_REPORTED_ROW_ERRORS = 100
MAX_SOURCE_TRADE_ID_CHARS = 255
MAX_EXCEL_ARCHIVE_ENTRIES = 10_000
MAX_EXCEL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

_TRADING_TZ = ZoneInfo("America/New_York")
_MONEY_QUANT = Decimal("0.01")
_STORAGE_QUANT = Decimal("0.000001")
_STORAGE_MAX = Decimal("999999999999.999999")
_CSV_EXTENSIONS = {".csv"}
_EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}

_COLUMN_LABELS = {
    "source_trade_id": "Id",
    "contract_name": "ContractName",
    "entered_at": "EnteredAt",
    "exited_at": "ExitedAt",
    "entry_price": "EntryPrice",
    "exit_price": "ExitPrice",
    "fees": "Fees",
    "pnl": "PnL",
    "size": "Size",
    "direction": "Type",
    "trade_day": "TradeDay",
    "duration": "TradeDuration",
    "commissions": "Commissions",
}

_COLUMN_ALIASES = {
    "source_trade_id": {
        "id",
        "tradeid",
        "tradeidentifier",
        "executionid",
        "executionidentifier",
    },
    "contract_name": {
        "contract",
        "contractname",
        "contractsymbol",
        "instrument",
        "instrumentname",
        "symbol",
    },
    "entered_at": {
        "enteredat",
        "entryat",
        "entrytime",
        "entrytimestamp",
        "openedat",
        "opentime",
        "opentimestamp",
    },
    "exited_at": {
        "exitedat",
        "exitat",
        "exittime",
        "exittimestamp",
        "closedat",
        "closetime",
        "closetimestamp",
    },
    "entry_price": {
        "entryprice",
        "averageentryprice",
        "avgentryprice",
        "openprice",
    },
    "exit_price": {
        "exitprice",
        "averageexitprice",
        "avgexitprice",
        "closeprice",
    },
    "fees": {
        "fee",
        "fees",
        "brokerfee",
        "brokerfees",
        "exchangefee",
        "exchangefees",
        "noncommissionfees",
    },
    "pnl": {
        "pnl",
        "pandl",
        "profitandloss",
        "profitloss",
        "realizedpnl",
        "grosspnl",
    },
    "size": {
        "size",
        "quantity",
        "qty",
        "contracts",
        "contractquantity",
    },
    "direction": {
        "type",
        "tradetype",
        "direction",
        "tradedirection",
        "side",
        "positionside",
    },
    "trade_day": {
        "tradeday",
        "tradedate",
        "sessiondate",
        "tradingday",
        "tradingdate",
    },
    "duration": {
        "tradeduration",
        "duration",
        "holdingtime",
        "holdduration",
    },
    "commissions": {
        "commission",
        "commissions",
        "brokercommission",
        "brokercommissions",
        "topstepcommission",
        "topstepcommissions",
    },
}

_REQUIRED_COLUMNS = (
    "source_trade_id",
    "contract_name",
    "entered_at",
    "exited_at",
    "entry_price",
    "exit_price",
    "fees",
    "pnl",
    "size",
    "direction",
    "trade_day",
    "commissions",
)

_DATETIME_FORMATS = (
    "%m/%d/%Y %H:%M:%S.%f %z",
    "%m/%d/%Y %H:%M:%S %z",
    "%m/%d/%Y %I:%M:%S.%f %p %z",
    "%m/%d/%Y %I:%M:%S %p %z",
    "%Y-%m-%d %H:%M:%S.%f %z",
    "%Y-%m-%d %H:%M:%S %z",
    "%m/%d/%Y %H:%M:%S.%f",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S.%f %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)

_DATE_FORMATS = (
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%m-%d-%Y",
)


class TradeImportValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_columns: Sequence[str] | None = None,
        row_errors: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.missing_columns = list(missing_columns or [])
        self.row_errors = [dict(error) for error in (row_errors or [])]

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "missing_columns": list(self.missing_columns),
            "row_errors": [dict(error) for error in self.row_errors],
        }

    def as_dict(self) -> dict[str, Any]:
        return self.as_detail()


@dataclass(frozen=True)
class _SourceRow:
    row_number: int
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _ParsedTrade:
    row_number: int
    source_trade_id: str
    contract_name: str
    symbol: str
    entered_at: datetime
    exited_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    fees: Decimal
    commissions: Decimal
    gross_pnl: Decimal
    size: Decimal
    direction: str
    closing_side: str
    trade_day: date
    duration: str | None
    raw_columns: dict[str, Any]

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees - self.commissions

    def preview_payload(self, *, duplicate: bool) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "source_trade_id": self.source_trade_id,
            "contract_name": self.contract_name,
            "symbol": self.symbol,
            "entered_at": self.entered_at,
            "exited_at": self.exited_at,
            "entry_price": _decimal_float(self.entry_price),
            "exit_price": _decimal_float(self.exit_price),
            "fees": _money_float(self.fees),
            "commissions": _money_float(self.commissions),
            "gross_pnl": _money_float(self.gross_pnl),
            "net_pnl": _money_float(self.net_pnl),
            "size": _decimal_float(self.size),
            "direction": self.direction,
            "trade_day": self.trade_day,
            "duration": self.duration,
            "status": "duplicate" if duplicate else "new",
        }


@dataclass(frozen=True)
class _ParsedFile:
    source_file_name: str
    file_sha256: str
    trades: tuple[_ParsedTrade, ...]


@dataclass(frozen=True)
class _PreparedImport:
    parsed: _ParsedFile
    duplicate_flags: tuple[bool, ...]
    existing_batch: TradeImportBatch | None


def preview_trade_import(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    prepared = _prepare_import(
        db,
        user_id=user_id,
        account_id=account_id,
        filename=filename,
        content=content,
    )
    parsed = prepared.parsed
    duplicate_rows = sum(1 for duplicate in prepared.duplicate_flags if duplicate)
    new_trades = [
        trade
        for trade, duplicate in zip(parsed.trades, prepared.duplicate_flags)
        if not duplicate
    ]

    return {
        "source_file_name": parsed.source_file_name,
        "file_sha256": parsed.file_sha256,
        "total_rows": len(parsed.trades),
        "new_rows": len(parsed.trades) - duplicate_rows,
        "duplicate_rows": duplicate_rows,
        "summary": _summarize_trades(new_trades),
        "trades": [
            trade.preview_payload(duplicate=duplicate)
            for trade, duplicate in zip(parsed.trades, prepared.duplicate_flags)
        ],
    }


def confirm_trade_import(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    filename: str,
    content: bytes,
    expected_sha256: str,
) -> dict[str, Any]:
    actual_sha256 = _content_sha256(content)
    normalized_expected = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_expected):
        raise TradeImportValidationError(
            "invalid_preview_sha256",
            "The reviewed file identity is invalid. Preview the file again before importing.",
        )
    if actual_sha256 != normalized_expected:
        raise TradeImportValidationError(
            "file_changed_since_preview",
            "The selected file changed after preview. Preview it again before importing.",
        )

    prepared = _prepare_import(
        db,
        user_id=user_id,
        account_id=account_id,
        filename=filename,
        content=content,
    )
    if prepared.parsed.file_sha256 != normalized_expected:
        raise TradeImportValidationError(
            "file_changed_since_preview",
            "The selected file changed after preview. Preview it again before importing.",
        )

    if prepared.existing_batch is not None:
        return _serialize_existing_batch(prepared.existing_batch)

    return _persist_import(
        db,
        user_id=str(user_id),
        account_id=int(account_id),
        prepared=prepared,
        allow_retry=True,
    )


def _prepare_import(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    filename: str,
    content: bytes,
) -> _PreparedImport:
    parsed = _parse_file(filename=filename, content=content)
    return _prepare_parsed_import(
        db,
        user_id=str(user_id),
        account_id=int(account_id),
        parsed=parsed,
    )


def _prepare_parsed_import(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    parsed: _ParsedFile,
) -> _PreparedImport:
    source_ids = [trade.source_trade_id for trade in parsed.trades]
    existing_source_ids = _load_existing_source_ids(
        db,
        user_id=user_id,
        account_id=account_id,
        source_trade_ids=source_ids,
    )
    existing_fallback_keys = _load_existing_fallback_keys(
        db,
        user_id=user_id,
        account_id=account_id,
        trades=parsed.trades,
    )

    duplicate_flags: list[bool] = []
    seen_in_file: set[str] = set()
    for trade in parsed.trades:
        source_trade_id = trade.source_trade_id
        fallback_key = (source_trade_id, _as_utc(trade.exited_at))
        duplicate = (
            source_trade_id in existing_source_ids
            or fallback_key in existing_fallback_keys
            or source_trade_id in seen_in_file
        )
        duplicate_flags.append(duplicate)
        seen_in_file.add(source_trade_id)

    existing_batch = (
        db.query(TradeImportBatch)
        .filter(TradeImportBatch.user_id == user_id)
        .filter(TradeImportBatch.account_id == account_id)
        .filter(TradeImportBatch.file_sha256 == parsed.file_sha256)
        .first()
    )
    return _PreparedImport(
        parsed=parsed,
        duplicate_flags=tuple(duplicate_flags),
        existing_batch=existing_batch,
    )


def _persist_import(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    prepared: _PreparedImport,
    allow_retry: bool,
) -> dict[str, Any]:
    parsed = prepared.parsed
    imported_at = datetime.now(timezone.utc)
    batch = TradeImportBatch(
        user_id=user_id,
        account_id=account_id,
        source_file_name=parsed.source_file_name,
        file_sha256=parsed.file_sha256,
        imported_at=imported_at,
        total_rows=len(parsed.trades),
        inserted_rows=0,
        duplicate_rows=0,
    )

    try:
        db.add(batch)
        db.flush()

        inserted_rows = 0
        duplicate_rows = 0
        for trade, duplicate in zip(parsed.trades, prepared.duplicate_flags):
            if duplicate:
                duplicate_rows += 1
                continue

            db.add(
                ProjectXTradeEvent(
                    user_id=user_id,
                    account_id=account_id,
                    contract_id=trade.contract_name,
                    symbol=trade.symbol,
                    side=trade.closing_side,
                    size=trade.size,
                    price=trade.exit_price,
                    trade_timestamp=trade.exited_at,
                    fees=trade.fees,
                    commissions=trade.commissions,
                    fee_scope="round_turn",
                    pnl=trade.gross_pnl,
                    trade_date=trade.trade_day,
                    entry_timestamp=trade.entered_at,
                    entry_price=trade.entry_price,
                    order_id=trade.source_trade_id,
                    source_trade_id=trade.source_trade_id,
                    status="IMPORTED",
                    raw_payload={
                        "source": "topstep_trade_export",
                        "source_file_name": parsed.source_file_name,
                        "file_sha256": parsed.file_sha256,
                        "row_number": trade.row_number,
                        "columns": trade.raw_columns,
                    },
                    import_batch_id=int(batch.id),
                )
            )
            inserted_rows += 1

        batch.inserted_rows = inserted_rows
        batch.duplicate_rows = duplicate_rows
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        existing_batch = (
            db.query(TradeImportBatch)
            .filter(TradeImportBatch.user_id == user_id)
            .filter(TradeImportBatch.account_id == account_id)
            .filter(TradeImportBatch.file_sha256 == parsed.file_sha256)
            .first()
        )
        if existing_batch is not None:
            return _serialize_existing_batch(existing_batch)

        if allow_retry:
            retried = _prepare_parsed_import(
                db,
                user_id=user_id,
                account_id=account_id,
                parsed=parsed,
            )
            return _persist_import(
                db,
                user_id=user_id,
                account_id=account_id,
                prepared=retried,
                allow_retry=False,
            )

        raise TradeImportValidationError(
            "import_conflict",
            "The trade file conflicted with another import. Preview it again before retrying.",
        ) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "import_id": int(batch.id),
        "source_file_name": parsed.source_file_name,
        "imported_at": imported_at,
        "total_rows": len(parsed.trades),
        "inserted_rows": int(batch.inserted_rows),
        "duplicate_rows": int(batch.duplicate_rows),
    }


def _serialize_existing_batch(batch: TradeImportBatch) -> dict[str, Any]:
    imported_at = batch.imported_at
    if imported_at.tzinfo is None:
        imported_at = imported_at.replace(tzinfo=timezone.utc)
    else:
        imported_at = imported_at.astimezone(timezone.utc)
    return {
        "import_id": int(batch.id),
        "source_file_name": str(batch.source_file_name),
        "imported_at": imported_at,
        "total_rows": int(batch.total_rows),
        "inserted_rows": int(batch.inserted_rows),
        "duplicate_rows": int(batch.duplicate_rows),
    }


def _parse_file(*, filename: str, content: bytes) -> _ParsedFile:
    source_file_name = _normalize_source_file_name(filename)
    content_bytes = _validate_content(content)
    extension = Path(source_file_name).suffix.lower()

    if extension in _CSV_EXTENSIONS:
        headers, rows = _read_csv_rows(content_bytes)
    elif extension in _EXCEL_EXTENSIONS:
        headers, rows = _read_excel_rows(content_bytes)
    else:
        raise TradeImportValidationError(
            "unsupported_file_type",
            "Unsupported trade file type. Upload a CSV or XLSX file.",
        )

    column_indexes = _resolve_columns(headers)
    parsed_trades: list[_ParsedTrade] = []
    row_errors: list[dict[str, Any]] = []

    for source_row in rows:
        if len(source_row.values) != len(headers):
            row_errors.append(
                {
                    "row_number": source_row.row_number,
                    "field": "row",
                    "message": (
                        f"Row {source_row.row_number} has {len(source_row.values)} values; "
                        f"expected {len(headers)}."
                    ),
                }
            )
            continue

        trade, errors = _parse_trade_row(
            source_row,
            headers=headers,
            column_indexes=column_indexes,
        )
        if errors:
            row_errors.extend(errors)
        elif trade is not None:
            parsed_trades.append(trade)

        if len(row_errors) >= MAX_REPORTED_ROW_ERRORS:
            break

    if row_errors:
        reported = row_errors[:MAX_REPORTED_ROW_ERRORS]
        first = reported[0]
        message = str(first.get("message") or "The trade file contains invalid rows.")
        if len(row_errors) > 1:
            message = f"{message} {len(row_errors) - 1} additional row error(s) were found."
        raise TradeImportValidationError(
            "invalid_rows",
            message,
            row_errors=reported,
        )

    if not parsed_trades:
        raise TradeImportValidationError(
            "no_trade_rows",
            "The trade file does not contain any trade rows.",
        )

    if len(parsed_trades) > MAX_TRADE_IMPORT_ROWS:
        raise TradeImportValidationError(
            "too_many_rows",
            f"The trade file exceeds the {MAX_TRADE_IMPORT_ROWS:,}-row import limit.",
        )

    return _ParsedFile(
        source_file_name=source_file_name,
        file_sha256=hashlib.sha256(content_bytes).hexdigest(),
        trades=tuple(parsed_trades),
    )


def _read_csv_rows(content: bytes) -> tuple[tuple[str, ...], tuple[_SourceRow, ...]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TradeImportValidationError(
            "invalid_csv_encoding",
            "The CSV file must use UTF-8 encoding.",
        ) from exc

    try:
        dialect = _sniff_csv_dialect(text)
        reader = csv.reader(io.StringIO(text, newline=""), dialect=dialect)
        return _collect_tabular_rows(reader)
    except csv.Error as exc:
        raise TradeImportValidationError(
            "invalid_csv",
            "The CSV file could not be parsed.",
        ) from exc


def _sniff_csv_dialect(text: str) -> type[csv.Dialect] | csv.Dialect:
    sample = text[:16_384]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _read_excel_rows(content: bytes) -> tuple[tuple[str, ...], tuple[_SourceRow, ...]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise TradeImportValidationError(
            "excel_support_unavailable",
            "Excel import support is unavailable on this server.",
        ) from exc

    _validate_excel_archive(content)

    try:
        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=True,
        )
    except Exception as exc:
        raise TradeImportValidationError(
            "invalid_excel",
            "The XLSX file could not be parsed.",
        ) from exc

    try:
        worksheet = workbook.active
        return _collect_tabular_rows(
            (tuple(cell for cell in row) for row in worksheet.iter_rows(values_only=True))
        )
    finally:
        workbook.close()


def _validate_excel_archive(content: bytes) -> None:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            uncompressed_bytes = sum(entry.file_size for entry in entries)
    except (BadZipFile, OSError) as exc:
        raise TradeImportValidationError(
            "invalid_excel",
            "The XLSX file could not be parsed.",
        ) from exc

    if (
        len(entries) > MAX_EXCEL_ARCHIVE_ENTRIES
        or uncompressed_bytes > MAX_EXCEL_UNCOMPRESSED_BYTES
    ):
        raise TradeImportValidationError(
            "excel_archive_too_large",
            "The XLSX file expands beyond the safe import limit.",
        )


def _collect_tabular_rows(
    rows: Iterable[Sequence[Any]],
) -> tuple[tuple[str, ...], tuple[_SourceRow, ...]]:
    headers: tuple[str, ...] | None = None
    output: list[_SourceRow] = []

    for row_number, raw_row in enumerate(rows, start=1):
        values = tuple(raw_row)
        if _row_is_blank(values):
            continue

        if headers is None:
            headers = tuple(_header_text(value) for value in values)
            continue

        output.append(_SourceRow(row_number=row_number, values=values))
        if len(output) > MAX_TRADE_IMPORT_ROWS:
            raise TradeImportValidationError(
                "too_many_rows",
                f"The trade file exceeds the {MAX_TRADE_IMPORT_ROWS:,}-row import limit.",
            )

    if headers is None:
        raise TradeImportValidationError(
            "empty_file",
            "The trade file is empty.",
        )
    if not any(header.strip() for header in headers):
        raise TradeImportValidationError(
            "missing_header",
            "The trade file does not contain a header row.",
        )
    if any("\x00" in header for header in headers):
        raise TradeImportValidationError(
            "invalid_header",
            "Trade file headers cannot contain NUL characters.",
        )

    return headers, tuple(output)


def _resolve_columns(headers: Sequence[str]) -> dict[str, int]:
    matches: dict[str, int] = {}
    ambiguous: set[str] = set()

    for index, header in enumerate(headers):
        normalized = _normalize_header(header)
        if not normalized:
            continue
        for canonical, aliases in _COLUMN_ALIASES.items():
            if normalized not in aliases:
                continue
            if canonical in matches:
                ambiguous.add(canonical)
            else:
                matches[canonical] = index
            break

    if ambiguous:
        labels = [_COLUMN_LABELS[column] for column in sorted(ambiguous)]
        raise TradeImportValidationError(
            "ambiguous_columns",
            f"Multiple columns map to the same trade field: {', '.join(labels)}.",
        )

    missing = [
        _COLUMN_LABELS[column]
        for column in _REQUIRED_COLUMNS
        if column not in matches
    ]
    if missing:
        raise TradeImportValidationError(
            "missing_columns",
            f"Missing required columns: {', '.join(missing)}.",
            missing_columns=missing,
        )

    return matches


def _parse_trade_row(
    source_row: _SourceRow,
    *,
    headers: Sequence[str],
    column_indexes: dict[str, int],
) -> tuple[_ParsedTrade | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []

    for index, value in enumerate(source_row.values):
        if isinstance(value, str) and "\x00" in value:
            field = headers[index] or f"Column {index + 1}"
            errors.append(
                {
                    "row_number": source_row.row_number,
                    "field": field,
                    "message": (
                        f"Row {source_row.row_number}, {field}: "
                        "cannot contain NUL characters."
                    ),
                }
            )
    if errors:
        return None, errors

    def raw(field: str) -> Any:
        index = column_indexes.get(field)
        return None if index is None else source_row.values[index]

    def capture(field: str, parser: Callable[[Any], Any]) -> Any:
        try:
            return parser(raw(field))
        except ValueError as exc:
            label = _COLUMN_LABELS[field]
            errors.append(
                {
                    "row_number": source_row.row_number,
                    "field": label,
                    "message": f"Row {source_row.row_number}, {label}: {exc}",
                }
            )
            return None

    source_trade_id = capture("source_trade_id", _parse_identifier)
    contract_name = capture("contract_name", _parse_contract_name)
    entered_at = capture("entered_at", _parse_datetime_value)
    exited_at = capture("exited_at", _parse_datetime_value)
    entry_price = capture("entry_price", lambda value: _parse_decimal(value, positive=True))
    exit_price = capture("exit_price", lambda value: _parse_decimal(value, positive=True))
    fees = capture("fees", lambda value: _parse_decimal(value, nonnegative=True))
    gross_pnl = capture("pnl", _parse_decimal)
    size = capture("size", lambda value: _parse_decimal(value, positive=True))
    direction_result = capture("direction", _parse_direction)
    commissions = capture(
        "commissions",
        lambda value: _parse_decimal(value, nonnegative=True),
    )

    if isinstance(entered_at, datetime) and isinstance(exited_at, datetime) and entered_at > exited_at:
        errors.append(
            {
                "row_number": source_row.row_number,
                "field": _COLUMN_LABELS["exited_at"],
                "message": (
                    f"Row {source_row.row_number}, {_COLUMN_LABELS['exited_at']}: "
                    "must be on or after EnteredAt."
                ),
            }
        )

    trade_day = capture("trade_day", _parse_trade_day)

    duration = _optional_display_text(raw("duration")) if "duration" in column_indexes else None
    if duration is None and isinstance(entered_at, datetime) and isinstance(exited_at, datetime):
        duration = _format_duration(exited_at - entered_at)

    if errors:
        return None, errors

    assert isinstance(source_trade_id, str)
    assert isinstance(contract_name, str)
    assert isinstance(entered_at, datetime)
    assert isinstance(exited_at, datetime)
    assert isinstance(entry_price, Decimal)
    assert isinstance(exit_price, Decimal)
    assert isinstance(fees, Decimal)
    assert isinstance(commissions, Decimal)
    assert isinstance(gross_pnl, Decimal)
    assert isinstance(size, Decimal)
    assert isinstance(direction_result, tuple)
    assert trade_day is not None

    direction, closing_side = direction_result
    symbol = normalize_symbol_key(contract_name) or contract_name.upper()
    raw_columns = {
        str(header): _json_safe_value(value)
        for header, value in zip(headers, source_row.values)
        if str(header).strip()
    }

    return (
        _ParsedTrade(
            row_number=source_row.row_number,
            source_trade_id=source_trade_id,
            contract_name=contract_name,
            symbol=symbol,
            entered_at=entered_at,
            exited_at=exited_at,
            entry_price=entry_price,
            exit_price=exit_price,
            fees=fees,
            commissions=commissions,
            gross_pnl=gross_pnl,
            size=size,
            direction=direction,
            closing_side=closing_side,
            trade_day=trade_day,
            duration=duration,
            raw_columns=raw_columns,
        ),
        [],
    )


def _parse_identifier(value: Any) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError("is required.")
    if isinstance(value, int):
        text = str(value)
        return _validated_identifier(text)
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("must be a whole identifier.")
        return _validated_identifier(str(int(value)))
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("must be a whole identifier.")
        return _validated_identifier(str(int(value)))

    text = str(value).strip()
    if not text:
        raise ValueError("is required.")
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return _validated_identifier(text)


def _validated_identifier(value: str) -> str:
    if len(value) > MAX_SOURCE_TRADE_ID_CHARS:
        raise ValueError(
            f"must be {MAX_SOURCE_TRADE_ID_CHARS} characters or fewer."
        )
    if _has_control_character(value):
        raise ValueError("cannot contain control characters.")
    return value


def _parse_contract_name(value: Any) -> str:
    text = _required_text(value)
    if len(text) > 200:
        raise ValueError("must be 200 characters or fewer.")
    if _has_control_character(text):
        raise ValueError("cannot contain control characters.")
    return text.upper()


def _parse_decimal(
    value: Any,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("is required.")

    if isinstance(value, Decimal):
        numeric = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("must be a finite number.")
        numeric = Decimal(str(value))
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("is required.")
        is_parenthesized = text.startswith("(") and text.endswith(")")
        if is_parenthesized:
            text = text[1:-1]
        text = text.replace(",", "").replace("$", "").strip()
        try:
            numeric = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("must be a number.") from exc
        if is_parenthesized:
            numeric = -numeric

    if not numeric.is_finite():
        raise ValueError("must be a finite number.")

    try:
        normalized = numeric.quantize(_STORAGE_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("is outside the supported numeric range.") from exc
    if abs(normalized) > _STORAGE_MAX:
        raise ValueError("is outside the supported numeric range.")
    if positive and normalized <= 0:
        raise ValueError("must be greater than zero.")
    if nonnegative and normalized < 0:
        raise ValueError("must be zero or greater.")
    return normalized


def _parse_datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = _required_text(value)
        iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(iso_text)
        except ValueError:
            parsed = _parse_datetime_with_formats(text)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TRADING_TZ)
    return parsed.astimezone(timezone.utc)


def _parse_datetime_with_formats(value: str) -> datetime:
    for date_format in _DATETIME_FORMATS:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    raise ValueError("must be a valid date and time.")


def _parse_trade_day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = _required_text(value)
    leading_date = text.split(maxsplit=1)[0]
    for candidate in (leading_date, text):
        for date_format in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, date_format).date()
            except ValueError:
                continue

    try:
        parsed = _parse_datetime_value(text)
    except ValueError as exc:
        raise ValueError("must be a valid trade date.") from exc
    return parsed.astimezone(_TRADING_TZ).date()


def _parse_direction(value: Any) -> tuple[str, str]:
    normalized = re.sub(r"[^A-Z]", "", _required_text(value).upper())
    if normalized in {"LONG", "L", "BUY"}:
        # ProjectX closed-trade rows store the closing execution side.
        return "Long", "SELL"
    if normalized in {"SHORT", "S", "SELL"}:
        return "Short", "BUY"
    raise ValueError("must be Long or Short.")


def _summarize_trades(trades: Sequence[_ParsedTrade]) -> dict[str, Any]:
    gross_pnl = sum((trade.gross_pnl for trade in trades), Decimal("0"))
    fees = sum((trade.fees for trade in trades), Decimal("0"))
    commissions = sum((trade.commissions for trade in trades), Decimal("0"))
    net_values = [trade.net_pnl for trade in trades]
    return {
        "gross_pnl": _money_float(gross_pnl),
        "fees": _money_float(fees),
        "commissions": _money_float(commissions),
        "net_pnl": _money_float(gross_pnl - fees - commissions),
        "wins": sum(1 for value in net_values if value > 0),
        "losses": sum(1 for value in net_values if value < 0),
        "breakeven": sum(1 for value in net_values if value == 0),
    }


def _load_existing_source_ids(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    source_trade_ids: Sequence[str],
) -> set[str]:
    unique_ids = list(dict.fromkeys(source_trade_ids))
    existing: set[str] = set()
    chunk_size = 500
    for start in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[start : start + chunk_size]
        rows = (
            db.query(ProjectXTradeEvent.source_trade_id)
            .filter(ProjectXTradeEvent.user_id == user_id)
            .filter(ProjectXTradeEvent.account_id == account_id)
            .filter(ProjectXTradeEvent.source_trade_id.in_(chunk))
            .all()
        )
        existing.update(
            str(row.source_trade_id)
            for row in rows
            if row.source_trade_id is not None
        )
    return existing


def _load_existing_fallback_keys(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    trades: Sequence[_ParsedTrade],
) -> set[tuple[str, datetime]]:
    order_ids = list(dict.fromkeys(trade.source_trade_id for trade in trades))
    existing: set[tuple[str, datetime]] = set()
    chunk_size = 500
    for start in range(0, len(order_ids), chunk_size):
        chunk = order_ids[start : start + chunk_size]
        rows = (
            db.query(
                ProjectXTradeEvent.order_id,
                ProjectXTradeEvent.trade_timestamp,
            )
            .filter(ProjectXTradeEvent.user_id == user_id)
            .filter(ProjectXTradeEvent.account_id == account_id)
            .filter(ProjectXTradeEvent.order_id.in_(chunk))
            .all()
        )
        existing.update(
            (str(row.order_id), _as_utc(row.trade_timestamp))
            for row in rows
            if row.order_id is not None and row.trade_timestamp is not None
        )
    return existing


def _normalize_source_file_name(filename: str) -> str:
    text = str(filename or "").strip()
    source_file_name = re.split(r"[\\/]", text)[-1].strip()
    if not source_file_name:
        raise TradeImportValidationError(
            "missing_filename",
            "The trade file name is required.",
        )
    if len(source_file_name) > 255:
        raise TradeImportValidationError(
            "filename_too_long",
            "The trade file name must be 255 characters or fewer.",
        )
    if _has_control_character(source_file_name):
        raise TradeImportValidationError(
            "invalid_filename",
            "The trade file name cannot contain control characters.",
        )
    return source_file_name


def _validate_content(content: bytes) -> bytes:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TradeImportValidationError(
            "invalid_file_content",
            "The uploaded trade file is invalid.",
        )
    content_bytes = bytes(content)
    if not content_bytes:
        raise TradeImportValidationError(
            "empty_file",
            "The trade file is empty.",
        )
    if len(content_bytes) > MAX_TRADE_IMPORT_BYTES:
        raise TradeImportValidationError(
            "file_too_large",
            f"The trade file exceeds the {MAX_TRADE_IMPORT_BYTES // (1024 * 1024)} MB limit.",
        )
    return content_bytes


def _content_sha256(content: bytes) -> str:
    return hashlib.sha256(_validate_content(content)).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_header(value: Any) -> str:
    text = _header_text(value).lstrip("\ufeff").strip().lower()
    text = text.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def _header_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _required_text(value: Any) -> str:
    if value is None:
        raise ValueError("is required.")
    text = str(value).strip()
    if not text:
        raise ValueError("is required.")
    return text


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _optional_display_text(value: Any) -> str | None:
    if _value_is_blank(value):
        return None
    if isinstance(value, timedelta):
        total_seconds = value.total_seconds()
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{seconds:09.6f}"
    if isinstance(value, time):
        return value.isoformat()
    return str(value).strip()


def _format_duration(value: timedelta) -> str:
    total_microseconds = max(
        0,
        value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds,
    )
    total_seconds, microseconds = divmod(total_microseconds, 1_000_000)
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{microseconds:06d}0"


def _row_is_blank(values: Sequence[Any]) -> bool:
    return all(_value_is_blank(value) for value in values)


def _value_is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return str(value)


def _decimal_float(value: Decimal) -> float:
    return float(value)


def _money_float(value: Decimal) -> float:
    try:
        rounded = value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        rounded = Decimal("0")
    return float(rounded)
