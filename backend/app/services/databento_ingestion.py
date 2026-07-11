from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zipfile import BadZipFile, ZipFile, ZipInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

try:
    from databento_dbn import Compression, DBNDecoder, InstrumentDefMsg, Metadata, OHLCVMsg
except ImportError as exc:  # pragma: no cover - exercised by deployment validation
    raise RuntimeError(
        "Databento DBN support is unavailable; install backend requirements before importing data"
    ) from exc

from ..models import (
    DatabentoImportBatch,
    DatabentoImportFile,
    DatabentoInstrument,
    DatabentoOhlcv1m,
)
from .trading_day import trading_day_date


SUPPORTED_DATASET = "GLBX.MDP3"
SUPPORTED_ROOT = "MNQ"
SUPPORTED_SYMBOL = "MNQ.FUT"
MNQ_HISTORY_START_UTC = datetime(2019, 5, 5, 22, 0, tzinfo=timezone.utc)
DBN_READ_CHUNK_BYTES = 64 * 1024
_OUTRIGHT_PATTERN = re.compile(r"^([A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,4}$")


class DatabentoIngestionError(ValueError):
    """Raised when an archive is invalid, incomplete, or unsupported."""


@dataclass(frozen=True)
class DatabentoArchiveInfo:
    path: Path
    archive_sha256: str
    job_id: str
    dataset: str
    schema_name: str
    root_symbol: str
    manifest: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DatabentoImportResult:
    job_id: str
    archive_sha256: str
    schema_name: str
    records_read: int
    records_inserted: int
    files_completed: int
    skipped: bool


def inspect_databento_archive(path: str | Path) -> DatabentoArchiveInfo:
    archive_path = Path(path).expanduser().resolve()
    if not archive_path.is_file():
        raise DatabentoIngestionError(f"databento_archive_not_found:{archive_path}")
    archive_hash = _sha256_path(archive_path)
    try:
        with ZipFile(archive_path) as archive:
            metadata = _read_json_entry(archive, "metadata.json")
            manifest = _read_json_entry(archive, "manifest.json")
    except (BadZipFile, OSError) as exc:
        raise DatabentoIngestionError(
            f"invalid_databento_zip:{archive_path.name}:{exc}"
        ) from exc

    query = metadata.get("query") if isinstance(metadata.get("query"), dict) else {}
    dataset = str(query.get("dataset") or "")
    schema_name = str(query.get("schema") or "")
    symbols = query.get("symbols") if isinstance(query.get("symbols"), list) else []
    stype_in = str(query.get("stype_in") or "")
    stype_out = str(query.get("stype_out") or "")
    encoding = str(query.get("encoding") or "")
    compression = str(query.get("compression") or "")
    job_id = str(metadata.get("job_id") or manifest.get("job_id") or "").strip()

    if dataset != SUPPORTED_DATASET:
        raise DatabentoIngestionError(f"unsupported_databento_dataset:{dataset}")
    if schema_name not in {"definition", "ohlcv-1m"}:
        raise DatabentoIngestionError(f"unsupported_databento_schema:{schema_name}")
    if symbols != [SUPPORTED_SYMBOL] or stype_in != "parent" or stype_out != "instrument_id":
        raise DatabentoIngestionError(
            "unsupported_databento_symbology: expected MNQ.FUT parent-to-instrument_id"
        )
    if encoding != "dbn" or compression != "zstd":
        raise DatabentoIngestionError(
            "unsupported_databento_encoding: expected DBN with zstd compression"
        )
    if not job_id:
        raise DatabentoIngestionError("databento_job_id_missing")
    if str(manifest.get("job_id") or "") != job_id:
        raise DatabentoIngestionError("databento_job_id_mismatch")
    return DatabentoArchiveInfo(
        path=archive_path,
        archive_sha256=archive_hash,
        job_id=job_id,
        dataset=dataset,
        schema_name=schema_name,
        root_symbol=SUPPORTED_ROOT,
        manifest=manifest,
        metadata=metadata,
    )


def import_databento_archives(
    db: Session,
    paths: Sequence[str | Path],
    *,
    commit_batches: bool = False,
) -> list[DatabentoImportResult]:
    """Import definitions before OHLCV regardless of caller path ordering."""

    infos = [inspect_databento_archive(path) for path in paths]
    infos.sort(key=lambda item: (item.schema_name != "definition", item.job_id))
    results: list[DatabentoImportResult] = []
    for info in infos:
        results.append(
            import_databento_archive(db, info, commit_batches=commit_batches)
        )
    return results


def import_databento_archive(
    db: Session,
    archive: DatabentoArchiveInfo | str | Path,
    *,
    commit_batches: bool = False,
) -> DatabentoImportResult:
    """Stream one Databento batch ZIP into idempotent global market-data tables."""

    info = archive if isinstance(archive, DatabentoArchiveInfo) else inspect_databento_archive(archive)
    existing = db.execute(
        select(DatabentoImportBatch).where(
            DatabentoImportBatch.archive_sha256 == info.archive_sha256
        )
    ).scalar_one_or_none()
    if existing is not None and str(existing.status) == "completed":
        completed_files = _completed_file_count(db, batch_id=int(existing.id))
        if int(existing.files_completed or 0) != completed_files:
            existing.files_completed = completed_files
            db.flush()
        return DatabentoImportResult(
            job_id=str(existing.job_id),
            archive_sha256=str(existing.archive_sha256),
            schema_name=str(existing.schema_name),
            records_read=int(existing.records_read or 0),
            records_inserted=int(existing.records_inserted or 0),
            files_completed=completed_files,
            skipped=True,
        )

    batch = existing or DatabentoImportBatch(
        job_id=info.job_id,
        archive_name=info.path.name,
        archive_sha256=info.archive_sha256,
        dataset=info.dataset,
        schema_name=info.schema_name,
        root_symbol=info.root_symbol,
        status="pending",
        records_read=0,
        records_inserted=0,
        files_completed=0,
        manifest_json=info.manifest,
    )
    if existing is None:
        db.add(batch)
        db.flush()
    batch.status = "running"
    batch.error_message = None
    batch.started_at = datetime.now(timezone.utc)
    batch.completed_at = None
    if commit_batches:
        db.commit()

    try:
        with ZipFile(info.path) as zip_file:
            dbn_entries = _dbn_entries(zip_file, schema_name=info.schema_name)
            manifest_hashes = _manifest_hashes(info.manifest)
            if not dbn_entries:
                raise DatabentoIngestionError(
                    f"databento_payload_missing:{info.schema_name}"
                )
            for filename, expected_hash in manifest_hashes.items():
                try:
                    manifest_entry = zip_file.getinfo(filename)
                except KeyError as exc:
                    raise DatabentoIngestionError(
                        f"databento_manifest_payload_missing:{filename}"
                    ) from exc
                actual_hash = _sha256_zip_entry(zip_file, manifest_entry)
                if actual_hash != expected_hash:
                    raise DatabentoIngestionError(
                        f"databento_checksum_mismatch:{filename}"
                    )
            for entry in dbn_entries:
                if entry.filename not in manifest_hashes:
                    raise DatabentoIngestionError(
                        f"databento_manifest_entry_missing:{entry.filename}"
                    )

            if info.schema_name == "definition":
                _import_definition_entries(
                    db,
                    batch=batch,
                    zip_file=zip_file,
                    entries=dbn_entries,
                    hashes=manifest_hashes,
                )
            else:
                _import_ohlcv_entries(
                    db,
                    batch=batch,
                    zip_file=zip_file,
                    entries=dbn_entries,
                    hashes=manifest_hashes,
                    commit_batches=commit_batches,
                )
        batch.status = "completed"
        batch.completed_at = datetime.now(timezone.utc)
        batch.error_message = None
        db.flush()
        if commit_batches:
            db.commit()
    except Exception as exc:
        if commit_batches:
            db.rollback()
            batch = db.execute(
                select(DatabentoImportBatch).where(
                    DatabentoImportBatch.archive_sha256 == info.archive_sha256
                )
            ).scalar_one()
            batch.status = "failed"
            batch.error_message = str(exc)[:2000]
            db.commit()
        else:
            batch.status = "failed"
            batch.error_message = str(exc)[:2000]
            db.flush()
        if isinstance(exc, DatabentoIngestionError):
            raise
        raise DatabentoIngestionError(
            f"databento_import_failed:{info.path.name}:{exc}"
        ) from exc

    return DatabentoImportResult(
        job_id=info.job_id,
        archive_sha256=info.archive_sha256,
        schema_name=info.schema_name,
        records_read=int(batch.records_read or 0),
        records_inserted=int(batch.records_inserted or 0),
        files_completed=int(batch.files_completed or 0),
        skipped=False,
    )


def _import_definition_entries(
    db: Session,
    *,
    batch: DatabentoImportBatch,
    zip_file: ZipFile,
    entries: Sequence[ZipInfo],
    hashes: dict[str, str],
) -> None:
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    pending_files: list[DatabentoImportFile] = []
    records_read = 0
    file_rows = _prepare_file_rows(
        db,
        batch=batch,
        entries=entries,
        hashes=hashes,
        schema_name="definition",
    )
    for entry in entries:
        file_row = file_rows[entry.filename]
        if str(file_row.status) == "completed":
            continue
        file_row.status = "running"
        file_count = 0
        metadata_seen = False
        for record in _iter_dbn_entry(zip_file, entry):
            if isinstance(record, Metadata):
                _validate_dbn_metadata(record, dataset=str(batch.dataset), schema_name="definition")
                metadata_seen = True
                continue
            if not isinstance(record, InstrumentDefMsg):
                raise DatabentoIngestionError(
                    f"unexpected_dbn_record:{entry.filename}:{type(record).__name__}"
                )
            file_count += 1
            values = _instrument_values(
                record,
                dataset=str(batch.dataset),
                source_hash=hashes[entry.filename],
            )
            key = (str(values["dataset"]), int(values["instrument_id"]))
            prior = latest.get(key)
            if prior is None or values["definition_ts"] >= prior["definition_ts"]:
                latest[key] = values
        if not metadata_seen:
            raise DatabentoIngestionError(f"dbn_metadata_missing:{entry.filename}")
        file_row.records_read = file_count
        file_row.records_inserted = 0
        file_row.error_message = None
        pending_files.append(file_row)
        records_read += file_count

    inserted = _upsert_instruments(db, list(latest.values()))
    for file_row in pending_files:
        file_row.status = "completed"
        file_row.completed_at = datetime.now(timezone.utc)
    batch.records_read = int(batch.records_read or 0) + records_read
    batch.records_inserted = int(batch.records_inserted or 0) + inserted
    db.flush()
    batch.files_completed = _completed_file_count(db, batch_id=int(batch.id))
    db.flush()


def _import_ohlcv_entries(
    db: Session,
    *,
    batch: DatabentoImportBatch,
    zip_file: ZipFile,
    entries: Sequence[ZipInfo],
    hashes: dict[str, str],
    commit_batches: bool,
) -> None:
    instruments = {
        int(row.instrument_id): row
        for row in db.execute(
            select(DatabentoInstrument).where(
                DatabentoInstrument.dataset == str(batch.dataset)
            )
        ).scalars()
    }
    if not instruments:
        raise DatabentoIngestionError(
            "databento_definitions_required: import the MNQ definition archive first"
        )
    bind = db.get_bind()
    batch_size = 50_000 if bind is not None and bind.dialect.name == "postgresql" else 500

    for entry in entries:
        file_row = _get_or_create_file(
            db,
            batch=batch,
            filename=entry.filename,
            file_sha256=hashes[entry.filename],
            schema_name="ohlcv-1m",
        )
        if str(file_row.status) == "completed":
            continue
        file_row.status = "running"
        file_row.error_message = None
        if commit_batches:
            db.commit()
        rows: list[dict[str, Any]] = []
        file_read = 0
        # A failed file can have committed row checkpoints. Preserve that
        # progress while replaying the file; duplicate rows are ignored.
        file_inserted = int(file_row.records_inserted or 0)
        metadata_seen = False
        for record in _iter_dbn_entry(zip_file, entry):
            if isinstance(record, Metadata):
                _validate_dbn_metadata(record, dataset=str(batch.dataset), schema_name="ohlcv-1m")
                metadata_seen = True
                continue
            if not isinstance(record, OHLCVMsg):
                raise DatabentoIngestionError(
                    f"unexpected_dbn_record:{entry.filename}:{type(record).__name__}"
                )
            file_read += 1
            instrument = instruments.get(int(record.instrument_id))
            if instrument is None:
                raise DatabentoIngestionError(
                    f"databento_instrument_mapping_missing:{int(record.instrument_id)}"
                )
            if str(instrument.instrument_class) != "F" or str(instrument.root_symbol) != SUPPORTED_ROOT:
                continue
            timestamp = _datetime_from_unix_nanos(int(record.ts_event))
            if timestamp < MNQ_HISTORY_START_UTC:
                raise DatabentoIngestionError(
                    f"mnq_prelaunch_record_rejected:{timestamp.isoformat()}"
                )
            values = {
                "dataset": str(batch.dataset),
                "instrument_id": int(record.instrument_id),
                "ts_event": timestamp,
                "trading_date": trading_day_date(timestamp),
                "open_nano": int(record.open),
                "high_nano": int(record.high),
                "low_nano": int(record.low),
                "close_nano": int(record.close),
                "volume": int(record.volume),
                "source_file_sha256": hashes[entry.filename],
            }
            _validate_ohlcv_values(values)
            rows.append(values)
            if len(rows) >= batch_size:
                inserted = _insert_ohlcv_rows(db, rows)
                file_inserted += inserted
                rows.clear()
                if commit_batches:
                    _checkpoint_ohlcv_import(
                        db,
                        batch=batch,
                        file_row=file_row,
                        records_read=file_read,
                        records_inserted=file_inserted,
                    )
        if rows:
            file_inserted += _insert_ohlcv_rows(db, rows)
        if not metadata_seen:
            raise DatabentoIngestionError(f"dbn_metadata_missing:{entry.filename}")
        file_inserted = int(
            db.scalar(
                select(func.count())
                .select_from(DatabentoOhlcv1m)
                .where(
                    DatabentoOhlcv1m.source_file_sha256 == hashes[entry.filename]
                )
            )
            or 0
        )
        file_row.records_read = file_read
        file_row.records_inserted = file_inserted
        file_row.status = "completed"
        file_row.completed_at = datetime.now(timezone.utc)
        batch.records_read = int(batch.records_read or 0) + file_read
        batch.records_inserted = int(batch.records_inserted or 0) + file_inserted
        db.flush()
        if commit_batches:
            db.commit()
            batch = db.get(DatabentoImportBatch, int(batch.id))
            file_row = db.get(DatabentoImportFile, int(file_row.id))
    batch.files_completed = _completed_file_count(db, batch_id=int(batch.id))


def _checkpoint_ohlcv_import(
    db: Session,
    *,
    batch: DatabentoImportBatch,
    file_row: DatabentoImportFile,
    records_read: int,
    records_inserted: int,
) -> None:
    # Checkpoints make an interrupted multi-million-row import cheap to retry.
    # Counts are replaced (not accumulated) until the file is completed.
    file_row.records_read = records_read
    file_row.records_inserted = records_inserted
    db.flush()
    db.commit()


def _insert_ohlcv_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    table = DatabentoOhlcv1m.__table__
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect == "postgresql":
        return _copy_postgresql_ohlcv_rows(db, rows)
    elif dialect == "sqlite":
        statement = sqlite_insert(table).values(rows).on_conflict_do_nothing(
            index_elements=["dataset", "instrument_id", "ts_event"]
        )
    else:  # pragma: no cover - TopSignal supports PostgreSQL and SQLite tests
        inserted = 0
        for row in rows:
            exists = db.get(
                DatabentoOhlcv1m,
                (row["dataset"], row["instrument_id"], row["ts_event"]),
            )
            if exists is None:
                db.add(DatabentoOhlcv1m(**row))
                inserted += 1
        db.flush()
        return inserted
    result = db.execute(statement)
    return max(0, int(result.rowcount or 0))


def _copy_postgresql_ohlcv_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    """Use bounded COPY staging, then merge idempotently into the indexed table."""

    sqlalchemy_connection = db.connection()
    raw_connection = sqlalchemy_connection.connection.driver_connection
    stage_name = "topsignal_databento_ohlcv_stage"
    columns = (
        "dataset",
        "instrument_id",
        "ts_event",
        "trading_date",
        "open_nano",
        "high_nano",
        "low_nano",
        "close_nano",
        "volume",
        "source_file_sha256",
    )
    with raw_connection.cursor() as cursor:
        cursor.execute(
            f"""
            create temporary table {stage_name} (
              dataset text not null,
              instrument_id bigint not null,
              ts_event timestamptz not null,
              trading_date date not null,
              open_nano bigint not null,
              high_nano bigint not null,
              low_nano bigint not null,
              close_nano bigint not null,
              volume bigint not null,
              source_file_sha256 text not null
            ) on commit drop
            """
        )
        with cursor.copy(
            f"copy {stage_name} ({', '.join(columns)}) from stdin"
        ) as copy:
            for row in rows:
                copy.write_row(tuple(row[column] for column in columns))
        cursor.execute(
            f"""
            insert into databento_ohlcv_1m ({', '.join(columns)})
            select {', '.join(columns)} from {stage_name}
            on conflict (dataset, instrument_id, ts_event) do nothing
            """
        )
        inserted = max(0, int(cursor.rowcount or 0))
        cursor.execute(f"drop table {stage_name}")
    return inserted


def _upsert_instruments(db: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    table = DatabentoInstrument.__table__
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect == "postgresql":
        insert = postgresql_insert(table).values(rows)
    elif dialect == "sqlite":
        insert = sqlite_insert(table).values(rows)
    else:  # pragma: no cover
        inserted = 0
        for values in rows:
            key = (values["dataset"], values["instrument_id"])
            target = db.get(DatabentoInstrument, key)
            if target is None:
                db.add(DatabentoInstrument(**values))
                inserted += 1
            elif values["definition_ts"] >= target.definition_ts:
                for name, value in values.items():
                    setattr(target, name, value)
        db.flush()
        return inserted
    excluded = insert.excluded
    statement = insert.on_conflict_do_update(
        index_elements=["dataset", "instrument_id"],
        set_={
            "raw_symbol": excluded.raw_symbol,
            "root_symbol": excluded.root_symbol,
            "instrument_class": excluded.instrument_class,
            "security_type": excluded.security_type,
            "activation": excluded.activation,
            "expiration": excluded.expiration,
            "min_price_increment_nano": excluded.min_price_increment_nano,
            "unit_of_measure_qty_nano": excluded.unit_of_measure_qty_nano,
            "definition_ts": excluded.definition_ts,
            "source_file_sha256": excluded.source_file_sha256,
        },
        where=excluded.definition_ts >= table.c.definition_ts,
    )
    before = _instrument_count(db, dataset=str(rows[0]["dataset"]))
    db.execute(statement)
    after = _instrument_count(db, dataset=str(rows[0]["dataset"]))
    return max(0, after - before)


def _instrument_values(
    record: InstrumentDefMsg,
    *,
    dataset: str,
    source_hash: str,
) -> dict[str, Any]:
    raw_symbol = str(record.raw_symbol).strip().upper()
    instrument_class = _enum_value(record.instrument_class)
    match = _OUTRIGHT_PATTERN.fullmatch(raw_symbol) if instrument_class == "F" else None
    root = match.group(1) if match is not None else raw_symbol.split("-", 1)[0]
    if not root.startswith(SUPPORTED_ROOT):
        raise DatabentoIngestionError(f"unexpected_definition_symbol:{raw_symbol}")
    return {
        "dataset": dataset,
        "instrument_id": int(record.instrument_id),
        "raw_symbol": raw_symbol,
        "root_symbol": SUPPORTED_ROOT,
        "instrument_class": instrument_class,
        "security_type": str(record.security_type or ""),
        "activation": _nullable_datetime_from_unix_nanos(int(record.activation)),
        "expiration": _nullable_datetime_from_unix_nanos(int(record.expiration)),
        "min_price_increment_nano": _nullable_fixed_int(record.min_price_increment),
        "unit_of_measure_qty_nano": _nullable_fixed_int(record.unit_of_measure_qty),
        "definition_ts": _datetime_from_unix_nanos(int(record.ts_recv)),
        "source_file_sha256": source_hash,
    }


def _validate_ohlcv_values(values: dict[str, Any]) -> None:
    open_price = int(values["open_nano"])
    high = int(values["high_nano"])
    low = int(values["low_nano"])
    close = int(values["close_nano"])
    volume = int(values["volume"])
    if min(open_price, high, low, close) <= 0:
        raise DatabentoIngestionError("databento_nonpositive_ohlcv_price")
    if high < max(open_price, low, close) or low > min(open_price, high, close):
        raise DatabentoIngestionError("databento_invalid_ohlcv_envelope")
    if volume < 0:
        raise DatabentoIngestionError("databento_negative_ohlcv_volume")


def _validate_dbn_metadata(metadata: Metadata, *, dataset: str, schema_name: str) -> None:
    actual_dataset = str(metadata.dataset)
    actual_schema = _schema_value(metadata.schema)
    if actual_dataset != dataset or actual_schema != schema_name:
        raise DatabentoIngestionError(
            f"dbn_metadata_mismatch:{actual_dataset}:{actual_schema}"
        )


def _iter_dbn_entry(zip_file: ZipFile, entry: ZipInfo) -> Iterator[Any]:
    decoder = DBNDecoder(compression=Compression.ZSTD)
    try:
        with zip_file.open(entry, "r") as source:
            while True:
                chunk = source.read(DBN_READ_CHUNK_BYTES)
                if not chunk:
                    break
                for record in decoder.write_and_decode(chunk):
                    yield record
            for record in decoder.decode():
                yield record
        if decoder.buffer():
            raise DatabentoIngestionError(f"truncated_dbn_payload:{entry.filename}")
    except DatabentoIngestionError:
        raise
    except Exception as exc:
        raise DatabentoIngestionError(
            f"invalid_dbn_zstd_payload:{entry.filename}:{exc}"
        ) from exc


def _get_or_create_file(
    db: Session,
    *,
    batch: DatabentoImportBatch,
    filename: str,
    file_sha256: str,
    schema_name: str,
) -> DatabentoImportFile:
    row = db.execute(
        select(DatabentoImportFile).where(
            DatabentoImportFile.batch_id == int(batch.id),
            DatabentoImportFile.filename == filename,
        )
    ).scalar_one_or_none()
    if row is None:
        row = DatabentoImportFile(
            batch_id=int(batch.id),
            filename=filename,
            file_sha256=file_sha256,
            schema_name=schema_name,
            status="pending",
            records_read=0,
            records_inserted=0,
        )
        db.add(row)
        db.flush()
    elif str(row.file_sha256) != file_sha256:
        raise DatabentoIngestionError(f"databento_file_identity_changed:{filename}")
    return row


def _prepare_file_rows(
    db: Session,
    *,
    batch: DatabentoImportBatch,
    entries: Sequence[ZipInfo],
    hashes: Mapping[str, str],
    schema_name: str,
) -> dict[str, DatabentoImportFile]:
    """Load/create a split archive's file ledger in two database round trips."""

    rows = {
        str(row.filename): row
        for row in db.execute(
            select(DatabentoImportFile).where(
                DatabentoImportFile.batch_id == int(batch.id)
            )
        ).scalars()
    }
    missing: list[DatabentoImportFile] = []
    for entry in entries:
        expected_hash = hashes[entry.filename]
        row = rows.get(entry.filename)
        if row is not None:
            if str(row.file_sha256) != expected_hash:
                raise DatabentoIngestionError(
                    f"databento_file_identity_changed:{entry.filename}"
                )
            continue
        row = DatabentoImportFile(
            batch_id=int(batch.id),
            filename=entry.filename,
            file_sha256=expected_hash,
            schema_name=schema_name,
            status="pending",
            records_read=0,
            records_inserted=0,
        )
        rows[entry.filename] = row
        missing.append(row)
    if missing:
        db.add_all(missing)
        db.flush()
    return rows


def _completed_file_count(db: Session, *, batch_id: int) -> int:
    return sum(
        1
        for status in db.execute(
            select(DatabentoImportFile.status).where(
                DatabentoImportFile.batch_id == batch_id
            )
        ).scalars()
        if str(status) == "completed"
    )


def _instrument_count(db: Session, *, dataset: str) -> int:
    return sum(
        1
        for _ in db.execute(
            select(DatabentoInstrument.instrument_id).where(
                DatabentoInstrument.dataset == dataset
            )
        )
    )


def _dbn_entries(zip_file: ZipFile, *, schema_name: str) -> list[ZipInfo]:
    suffix = f".{schema_name}.dbn.zst"
    entries = [entry for entry in zip_file.infolist() if entry.filename.endswith(suffix)]
    return sorted(entries, key=lambda entry: entry.filename)


def _manifest_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        value = str(item.get("hash") or "")
        if value.startswith("sha256:"):
            value = value.removeprefix("sha256:")
        if filename and re.fullmatch(r"[0-9a-f]{64}", value):
            output[filename] = value
    return output


def _read_json_entry(zip_file: ZipFile, filename: str) -> dict[str, Any]:
    try:
        with zip_file.open(filename, "r") as source:
            value = json.load(source)
    except KeyError as exc:
        raise DatabentoIngestionError(f"databento_archive_entry_missing:{filename}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatabentoIngestionError(f"invalid_databento_json:{filename}") from exc
    if not isinstance(value, dict):
        raise DatabentoIngestionError(f"invalid_databento_json_object:{filename}")
    return value


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_zip_entry(zip_file: ZipFile, entry: ZipInfo) -> str:
    digest = hashlib.sha256()
    with zip_file.open(entry, "r") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _schema_value(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "name", None) or getattr(value, "value", None) or str(value)
    normalized = str(raw).lower().replace("_", "-")
    return {"ohlcv-1-m": "ohlcv-1m", "ohlcv1m": "ohlcv-1m"}.get(normalized, normalized)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _datetime_from_unix_nanos(value: int) -> datetime:
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=nanos // 1_000
    )


def _nullable_datetime_from_unix_nanos(value: int) -> datetime | None:
    # Databento uses the signed-int sentinel for unavailable fixed-width fields.
    if value in {0, (1 << 63) - 1}:
        return None
    return _datetime_from_unix_nanos(value)


def _nullable_fixed_int(value: Any) -> int | None:
    parsed = int(value)
    return None if parsed == (1 << 63) - 1 else parsed
