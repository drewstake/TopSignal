from __future__ import annotations

import csv
import hashlib
import io
import os
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import Account, ProjectXTradeEvent, TradeImportBatch
from app.services.projectx_trades import (
    get_trade_event_pnl_calendar,
    store_trade_events,
    summarize_trade_events,
)
from app.services.trade_imports import (
    TradeImportValidationError,
    confirm_trade_import,
    preview_trade_import,
)


USER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"
ACCOUNT_ID = 7301

TOPSTEP_COLUMNS = [
    "Id",
    "ContractName",
    "EnteredAt",
    "ExitedAt",
    "EntryPrice",
    "ExitPrice",
    "Fees",
    "PnL",
    "Size",
    "Type",
    "TradeDay",
    "TradeDuration",
    "Commissions",
]


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Account.__table__,
            TradeImportBatch.__table__,
            ProjectXTradeEvent.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    session.add(
        Account(
            id=ACCOUNT_ID,
            user_id=USER_ID,
            provider="projectx",
            external_id=str(ACCOUNT_ID),
            name="Topstep Live",
            account_state="ACTIVE",
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[
                ProjectXTradeEvent.__table__,
                TradeImportBatch.__table__,
                Account.__table__,
            ],
        )
        engine.dispose()


def _trade_row(**overrides: str) -> dict[str, str]:
    row = {
        "Id": "2815118967",
        "ContractName": "MNQU6",
        "EnteredAt": "07/02/2026 10:10:08 -04:00",
        "ExitedAt": "07/02/2026 10:10:48 -04:00",
        "EntryPrice": "30182.500000000",
        "ExitPrice": "30148.750000000",
        "Fees": "2.22000",
        "PnL": "202.500000000",
        "Size": "3",
        "Type": "Short",
        "TradeDay": "07/02/2026 00:00:00 -05:00",
        "TradeDuration": "00:00:39.6715820",
        "Commissions": "1.50000",
    }
    row.update(overrides)
    return row


def _csv_bytes(
    rows: list[dict[str, str]],
    *,
    columns: list[tuple[str, str]] | None = None,
    bom: bool = True,
) -> bytes:
    mapped_columns = columns or [(column, column) for column in TOPSTEP_COLUMNS]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[output_name for output_name, _ in mapped_columns],
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                output_name: row[canonical_name]
                for output_name, canonical_name in mapped_columns
            }
        )
    encoding = "utf-8-sig" if bom else "utf-8"
    return output.getvalue().encode(encoding)


def _xlsx_bytes(rows: list[dict[str, str]]) -> bytes:
    """Build a minimal standards-compliant XLSX without a test-only dependency."""

    values = [TOPSTEP_COLUMNS, *[[row[column] for column in TOPSTEP_COLUMNS] for row in rows]]
    sheet_rows: list[str] = []
    for row_index, row in enumerate(values, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            reference = f"{_excel_column(column_index)}{row_index}"
            cells.append(
                f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Trades" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    output = io.BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def _excel_column(index: int) -> str:
    output = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _preview(db_session, content: bytes, *, filename: str = "trades_export.csv"):
    return preview_trade_import(
        db_session,
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
        filename=filename,
        content=content,
    )


def _confirm(db_session, content: bytes, *, filename: str = "trades_export.csv"):
    preview = _preview(db_session, content, filename=filename)
    result = confirm_trade_import(
        db_session,
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
        filename=filename,
        content=content,
        expected_sha256=preview["file_sha256"],
    )
    return preview, result


def _iso_datetime(value: str | datetime) -> str:
    return value.isoformat() if isinstance(value, datetime) else value


def _iso_date(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else value


def test_successful_csv_preview_and_confirm_persist_trade_and_import_metadata(db_session):
    content = _csv_bytes([_trade_row()])

    preview, confirmed = _confirm(db_session, content)

    assert preview["source_file_name"] == "trades_export.csv"
    assert preview["file_sha256"] == hashlib.sha256(content).hexdigest()
    assert preview["total_rows"] == 1
    assert preview["new_rows"] == 1
    assert preview["duplicate_rows"] == 0
    assert preview["summary"] == {
        "gross_pnl": 202.5,
        "fees": 2.22,
        "commissions": 1.5,
        "net_pnl": 198.78,
        "wins": 1,
        "losses": 0,
        "breakeven": 0,
    }

    trade = preview["trades"][0]
    assert trade["row_number"] == 2
    assert trade["source_trade_id"] == "2815118967"
    assert trade["contract_name"] == "MNQU6"
    assert trade["symbol"] == "MNQ"
    assert _iso_datetime(trade["entered_at"]) == "2026-07-02T14:10:08+00:00"
    assert _iso_datetime(trade["exited_at"]) == "2026-07-02T14:10:48+00:00"
    assert trade["entry_price"] == 30182.5
    assert trade["exit_price"] == 30148.75
    assert trade["gross_pnl"] == 202.5
    assert trade["net_pnl"] == 198.78
    assert trade["direction"].upper() == "SHORT"
    assert _iso_date(trade["trade_day"]) == "2026-07-02"
    assert trade["status"] == "new"

    assert confirmed["source_file_name"] == "trades_export.csv"
    assert confirmed["total_rows"] == 1
    assert confirmed["inserted_rows"] == 1
    assert confirmed["duplicate_rows"] == 0
    assert confirmed["import_id"] > 0
    imported_at = confirmed["imported_at"]
    if isinstance(imported_at, str):
        imported_at = datetime.fromisoformat(imported_at)
    assert imported_at.tzinfo is not None

    batch = db_session.query(TradeImportBatch).one()
    assert batch.id == confirmed["import_id"]
    assert batch.user_id == USER_ID
    assert batch.account_id == ACCOUNT_ID
    assert batch.source_file_name == "trades_export.csv"
    assert batch.file_sha256 == preview["file_sha256"]
    assert batch.imported_at is not None

    stored = db_session.query(ProjectXTradeEvent).one()
    assert stored.user_id == USER_ID
    assert stored.account_id == ACCOUNT_ID
    assert stored.source_trade_id == "2815118967"
    assert stored.order_id == "2815118967"
    assert stored.contract_id == "MNQU6"
    assert stored.symbol == "MNQ"
    # ProjectX events store the closing execution side.
    assert stored.side == "BUY"
    assert float(stored.size) == 3.0
    assert float(stored.entry_price) == 30182.5
    assert float(stored.price) == 30148.75
    assert float(stored.pnl) == 202.5
    assert float(stored.fees) == 2.22
    assert float(stored.commissions) == 1.5
    assert stored.fee_scope == "round_turn"
    assert stored.trade_date == date(2026, 7, 2)
    assert stored.import_batch_id == batch.id

    summary = summarize_trade_events(
        db_session,
        account_id=ACCOUNT_ID,
        user_id=USER_ID,
    )
    assert summary["avg_win_duration_minutes"] == 0.67


def test_same_file_and_overlapping_files_do_not_duplicate_or_overwrite_trades(db_session):
    first_row = _trade_row()
    overlapping_row = _trade_row(
        Id="2815266522",
        EnteredAt="07/02/2026 10:11:40 -04:00",
        ExitedAt="07/02/2026 10:18:09 -04:00",
        EntryPrice="30180.250000000",
        ExitPrice="30114.250000000",
        Fees="0.74000",
        PnL="132.000000000",
        Size="1",
        Commissions="0.50000",
    )
    third_row = _trade_row(
        Id="2815247404",
        EnteredAt="07/02/2026 10:12:00 -04:00",
        ExitedAt="07/02/2026 10:19:00 -04:00",
        EntryPrice="30180.250000000",
        ExitPrice="30147.000000000",
        Fees="2.96000",
        PnL="266.000000000",
        Size="4",
        Commissions="2.00000",
    )
    first_content = _csv_bytes([first_row, overlapping_row])
    first_preview, first_confirm = _confirm(db_session, first_content, filename="july-1.csv")

    repeated_preview = _preview(db_session, first_content, filename="renamed-copy.csv")
    repeated_confirm = confirm_trade_import(
        db_session,
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
        filename="renamed-copy.csv",
        content=first_content,
        expected_sha256=repeated_preview["file_sha256"],
    )

    assert first_preview["new_rows"] == 2
    assert repeated_preview["new_rows"] == 0
    assert repeated_preview["duplicate_rows"] == 2
    assert repeated_preview["summary"]["net_pnl"] == 0.0
    assert repeated_preview["summary"]["wins"] == 0
    assert [trade["status"] for trade in repeated_preview["trades"]] == [
        "duplicate",
        "duplicate",
    ]
    assert repeated_confirm["import_id"] == first_confirm["import_id"]
    assert db_session.query(ProjectXTradeEvent).count() == 2
    assert db_session.query(TradeImportBatch).count() == 1

    original_overlap = (
        db_session.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.source_trade_id == "2815266522")
        .one()
    )
    original_batch_id = original_overlap.import_batch_id
    original_created_at = original_overlap.created_at

    overlap_content = _csv_bytes([overlapping_row, third_row])
    overlap_preview, overlap_confirm = _confirm(
        db_session,
        overlap_content,
        filename="july-2.csv",
    )

    assert overlap_preview["new_rows"] == 1
    assert overlap_preview["duplicate_rows"] == 1
    assert overlap_preview["summary"] == {
        "gross_pnl": 266.0,
        "fees": 2.96,
        "commissions": 2.0,
        "net_pnl": 261.04,
        "wins": 1,
        "losses": 0,
        "breakeven": 0,
    }
    assert [trade["status"] for trade in overlap_preview["trades"]] == [
        "duplicate",
        "new",
    ]
    assert overlap_confirm["inserted_rows"] == 1
    assert overlap_confirm["duplicate_rows"] == 1
    assert db_session.query(ProjectXTradeEvent).count() == 3
    assert db_session.query(TradeImportBatch).count() == 2

    unchanged_overlap = (
        db_session.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.source_trade_id == "2815266522")
        .one()
    )
    assert unchanged_overlap.import_batch_id == original_batch_id
    assert unchanged_overlap.created_at == original_created_at
    assert float(unchanged_overlap.pnl) == 132.0


def test_file_with_trade_already_stored_in_database_skips_it_without_mutation(db_session):
    existing = ProjectXTradeEvent(
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
        contract_id="CON.F.US.MNQ.U26",
        symbol="MNQ",
        side="SELL",
        size=1,
        price=30000,
        trade_timestamp=datetime(2026, 7, 2, 14, 10, 48, tzinfo=timezone.utc),
        fees=0.37,
        fee_scope="per_side",
        pnl=999,
        order_id="provider-order",
        source_trade_id="2815118967",
        raw_payload={"source": "projectx"},
    )
    db_session.add(existing)
    db_session.commit()
    existing_id = int(existing.id)
    existing_created_at = existing.created_at

    content = _csv_bytes([_trade_row()])
    preview, confirmed = _confirm(db_session, content)

    assert preview["new_rows"] == 0
    assert preview["duplicate_rows"] == 1
    assert preview["summary"]["net_pnl"] == 0.0
    assert preview["trades"][0]["status"] == "duplicate"
    assert confirmed["inserted_rows"] == 0
    assert confirmed["duplicate_rows"] == 1
    assert db_session.query(ProjectXTradeEvent).count() == 1

    unchanged = db_session.query(ProjectXTradeEvent).one()
    assert unchanged.id == existing_id
    assert unchanged.created_at == existing_created_at
    assert unchanged.order_id == "provider-order"
    assert unchanged.import_batch_id is None
    assert float(unchanged.pnl) == 999.0
    assert unchanged.raw_payload == {"source": "projectx"}


def test_existing_order_and_exit_timestamp_are_also_treated_as_a_duplicate(db_session):
    db_session.add(
        ProjectXTradeEvent(
            user_id=USER_ID,
            account_id=ACCOUNT_ID,
            contract_id="CON.F.US.MNQ.U26",
            symbol="MNQ",
            side="BUY",
            size=3,
            price=30148.75,
            trade_timestamp=datetime(2026, 7, 2, 14, 10, 48, tzinfo=timezone.utc),
            fees=1.11,
            fee_scope="per_side",
            pnl=202.5,
            order_id="2815118967",
            source_trade_id="provider-source-id",
            raw_payload={"source": "projectx"},
        )
    )
    db_session.commit()
    content = _csv_bytes([_trade_row()])

    preview, confirmed = _confirm(db_session, content)

    assert preview["new_rows"] == 0
    assert preview["duplicate_rows"] == 1
    assert preview["trades"][0]["status"] == "duplicate"
    assert confirmed["inserted_rows"] == 0
    assert confirmed["duplicate_rows"] == 1
    assert db_session.query(ProjectXTradeEvent).count() == 1


def test_provider_sync_cannot_overwrite_a_confirmed_import(db_session):
    content = _csv_bytes([_trade_row()])
    _, confirmed = _confirm(db_session, content)
    original = db_session.query(ProjectXTradeEvent).one()
    original_payload = dict(original.raw_payload)

    inserted = store_trade_events(
        db_session,
        [
            {
                "account_id": ACCOUNT_ID,
                "contract_id": "CHANGED",
                "symbol": "ES",
                "side": "SELL",
                "size": 99,
                "price": 1,
                "timestamp": datetime(2026, 7, 2, 14, 10, 48, tzinfo=timezone.utc),
                "fees": 99,
                "pnl": -999,
                "order_id": "provider-order",
                "source_trade_id": "2815118967",
                "raw_payload": {"source": "projectx"},
            }
        ],
        user_id=USER_ID,
    )
    db_session.commit()

    stored = db_session.query(ProjectXTradeEvent).one()
    assert inserted == 0
    assert stored.import_batch_id == confirmed["import_id"]
    assert stored.contract_id == "MNQU6"
    assert stored.order_id == "2815118967"
    assert float(stored.pnl) == 202.5
    assert float(stored.fees) == 2.22
    assert float(stored.commissions) == 1.5
    assert stored.trade_date == date(2026, 7, 2)
    assert stored.raw_payload == original_payload


def test_fee_net_and_multiple_trades_same_day_flow_into_pnl_calendar(db_session):
    loss = _trade_row(
        Id="2815118968",
        EnteredAt="07/02/2026 11:00:00 -04:00",
        ExitedAt="07/02/2026 11:01:00 -04:00",
        EntryPrice="30100.000000000",
        ExitPrice="30095.000000000",
        Fees="0.74000",
        PnL="-10.000000000",
        Size="1",
        Type="Long",
        TradeDuration="00:01:00.0000000",
        Commissions="0.50000",
    )
    content = _csv_bytes([_trade_row(), loss])

    preview, _ = _confirm(db_session, content)

    assert preview["summary"] == {
        "gross_pnl": 192.5,
        "fees": 2.96,
        "commissions": 2.0,
        "net_pnl": 187.54,
        "wins": 1,
        "losses": 1,
        "breakeven": 0,
    }
    assert db_session.query(ProjectXTradeEvent).count() == 2

    calendar = get_trade_event_pnl_calendar(
        db_session,
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
    )
    assert calendar == [
        {
            "date": "2026-07-02",
            "trade_count": 2,
            "win_count": 1,
            "loss_count": 1,
            "breakeven_count": 0,
            "gross_pnl": 192.5,
            "non_commission_fees": 2.96,
            "commissions": 2.0,
            "fees": 4.96,
            "net_pnl": 187.54,
        }
    ]


def test_identical_economic_values_with_distinct_topstep_ids_are_both_imported(db_session):
    # The real Topstep sample contains this shape: every economic field can be
    # identical even though each row represents a legitimate, distinct trade.
    first = _trade_row(Id="2815502338")
    second = _trade_row(Id="2815502261")
    content = _csv_bytes([first, second])

    preview, confirmed = _confirm(db_session, content)

    assert preview["new_rows"] == 2
    assert confirmed["inserted_rows"] == 2
    assert {
        row.source_trade_id
        for row in db_session.query(ProjectXTradeEvent).all()
    } == {"2815502338", "2815502261"}


def test_duplicate_identity_is_scoped_to_authenticated_user_and_account(db_session):
    db_session.add(
        ProjectXTradeEvent(
            user_id=OTHER_USER_ID,
            account_id=ACCOUNT_ID,
            contract_id="MNQU6",
            symbol="MNQ",
            side="BUY",
            size=3,
            price=30148.75,
            trade_timestamp=datetime(2026, 7, 2, 14, 10, 48, tzinfo=timezone.utc),
            fees=2.22,
            commissions=1.5,
            fee_scope="round_turn",
            pnl=202.5,
            order_id="2815118967",
            source_trade_id="2815118967",
        )
    )
    db_session.commit()

    content = _csv_bytes([_trade_row()])
    preview, confirmed = _confirm(db_session, content)

    assert preview["new_rows"] == 1
    assert preview["duplicate_rows"] == 0
    assert confirmed["inserted_rows"] == 1
    assert db_session.query(ProjectXTradeEvent).count() == 2


def test_parser_accepts_reordered_friendly_column_aliases(db_session):
    aliases = [
        ("Commission", "Commissions"),
        ("Trade Duration", "TradeDuration"),
        ("Trade Day", "TradeDay"),
        ("Direction", "Type"),
        ("Quantity", "Size"),
        ("P&L", "PnL"),
        ("Fee", "Fees"),
        ("Exit Price", "ExitPrice"),
        ("Entry Price", "EntryPrice"),
        ("Exited At", "ExitedAt"),
        ("Entered At", "EnteredAt"),
        ("Contract Name", "ContractName"),
        ("Trade Id", "Id"),
    ]
    content = _csv_bytes([_trade_row()], columns=aliases, bom=False)

    preview = _preview(db_session, content, filename="friendly-columns.CSV")

    assert preview["total_rows"] == 1
    assert preview["new_rows"] == 1
    assert preview["trades"][0]["source_trade_id"] == "2815118967"
    assert preview["trades"][0]["net_pnl"] == 198.78


def test_parser_accepts_xlsx_workbook(db_session):
    content = _xlsx_bytes([_trade_row()])

    preview = _preview(db_session, content, filename="trades_export.XLSX")

    assert preview["source_file_name"] == "trades_export.XLSX"
    assert preview["file_sha256"] == hashlib.sha256(content).hexdigest()
    assert preview["total_rows"] == 1
    assert preview["new_rows"] == 1
    assert preview["summary"]["net_pnl"] == 198.78


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("trades.txt", b"not a supported trade export"),
        ("trades.xlsx", b"not an xlsx zip archive"),
        ("trades.csv", b"\xff\xfe\x00\x01"),
        ("trades.csv", b""),
    ],
)
def test_invalid_files_are_rejected_without_database_writes(
    db_session,
    filename,
    content,
):
    with pytest.raises(TradeImportValidationError):
        _preview(db_session, content, filename=filename)

    assert db_session.query(ProjectXTradeEvent).count() == 0
    assert db_session.query(TradeImportBatch).count() == 0


def test_missing_required_columns_names_them_clearly(db_session):
    columns = [
        (column, column)
        for column in TOPSTEP_COLUMNS
        if column not in {"Id", "PnL"}
    ]
    content = _csv_bytes([_trade_row()], columns=columns)

    with pytest.raises(TradeImportValidationError) as exc_info:
        _preview(db_session, content)

    message = str(exc_info.value).lower()
    assert "missing" in message
    assert "id" in message
    assert "pnl" in message or "p&l" in message
    assert db_session.query(ProjectXTradeEvent).count() == 0
    assert db_session.query(TradeImportBatch).count() == 0


def test_invalid_row_reports_row_number_and_field(db_session):
    content = _csv_bytes([_trade_row(PnL="not-a-number")])

    with pytest.raises(TradeImportValidationError) as exc_info:
        _preview(db_session, content)

    message = str(exc_info.value).lower()
    assert "row 2" in message
    assert "pnl" in message or "p&l" in message
    assert db_session.query(ProjectXTradeEvent).count() == 0


def test_oversized_or_control_character_trade_ids_are_rejected(db_session):
    for source_trade_id in ("x" * 256, "unsafe\x00id"):
        content = _csv_bytes([_trade_row(Id=source_trade_id)])

        with pytest.raises(TradeImportValidationError) as exc_info:
            _preview(db_session, content)

        assert exc_info.value.code == "invalid_rows"
        assert exc_info.value.row_errors[0]["field"] == "Id"


def test_nul_in_an_extra_raw_column_is_rejected_before_database_insert(db_session):
    content = _csv_bytes(
        [_trade_row(Notes="unsafe\x00note")],
        columns=[
            *((column, column) for column in TOPSTEP_COLUMNS),
            ("Notes", "Notes"),
        ],
    )

    with pytest.raises(TradeImportValidationError) as exc_info:
        _preview(db_session, content)

    assert exc_info.value.code == "invalid_rows"
    assert exc_info.value.row_errors[0]["field"] == "Notes"


@pytest.mark.parametrize("field", ["EntryPrice", "PnL", "Fees", "Commissions", "Size"])
def test_numeric_values_outside_database_range_are_rejected(db_session, field):
    content = _csv_bytes([_trade_row(**{field: "1e10000"})])

    with pytest.raises(TradeImportValidationError) as exc_info:
        _preview(db_session, content)

    assert exc_info.value.code == "invalid_rows"
    assert exc_info.value.row_errors[0]["field"] == field
    assert "supported numeric range" in exc_info.value.row_errors[0]["message"]


def test_confirm_rejects_content_that_differs_from_reviewed_sha256(db_session):
    reviewed_content = _csv_bytes([_trade_row()])
    changed_content = _csv_bytes([_trade_row(PnL="999.00")])
    reviewed = _preview(db_session, reviewed_content)

    with pytest.raises(TradeImportValidationError):
        confirm_trade_import(
            db_session,
            user_id=USER_ID,
            account_id=ACCOUNT_ID,
            filename="trades_export.csv",
            content=changed_content,
            expected_sha256=reviewed["file_sha256"],
        )

    assert db_session.query(ProjectXTradeEvent).count() == 0
    assert db_session.query(TradeImportBatch).count() == 0
