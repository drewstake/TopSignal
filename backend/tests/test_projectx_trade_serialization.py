from datetime import datetime, timezone
from decimal import Decimal

from app.models import ProjectXTradeEvent
from app.services.projectx_trades import TradeExecutionLifecycle, _to_metric_sample, serialize_trade_event


def _event(*, pnl: Decimal | None, trade_timestamp: datetime | None = None, symbol: str = "CON.F.US.MES.H26") -> ProjectXTradeEvent:
    return ProjectXTradeEvent(
        id=1,
        account_id=13048312,
        contract_id=symbol,
        symbol=symbol,
        side="SELL",
        size=Decimal("3.000000"),
        price=Decimal("6858.750000"),
        trade_timestamp=trade_timestamp or datetime(2026, 2, 6, 11, 12, 9, tzinfo=timezone.utc),
        fees=Decimal("1.110000"),
        pnl=pnl,
        order_id="2397509693",
        source_trade_id="2074009852",
    )


def test_serialize_trade_event_uses_round_trip_fees_for_closed_rows():
    payload = serialize_trade_event(_event(pnl=Decimal("1312.500000")))

    assert payload["fees"] == 2.22
    assert payload["source_trade_id"] == "2074009852"


def test_serialize_trade_event_keeps_fill_fees_for_open_rows():
    payload = serialize_trade_event(_event(pnl=None))

    assert payload["fees"] == 1.11
    assert payload["source_trade_id"] == "2074009852"


def test_to_metric_sample_uses_round_trip_fees_for_closed_rows():
    sample = _to_metric_sample(_event(pnl=Decimal("1312.500000")))

    assert sample.fees == 2.22
    assert sample.pnl == 1312.5


def test_to_metric_sample_keeps_fill_fees_for_open_rows():
    sample = _to_metric_sample(_event(pnl=None))

    assert sample.fees == 1.11
    assert sample.pnl is None


def test_serialize_trade_event_adds_topstep_micro_commission_after_april_12_2026():
    payload = serialize_trade_event(
        _event(
            pnl=Decimal("1312.500000"),
            trade_timestamp=datetime(2026, 4, 13, 11, 12, 9, tzinfo=timezone.utc),
        )
    )

    assert payload["fees"] == 3.72


def test_serialize_trade_event_adds_topstep_non_micro_commission_after_april_12_2026():
    payload = serialize_trade_event(
        _event(
            pnl=Decimal("1312.500000"),
            trade_timestamp=datetime(2026, 4, 13, 11, 12, 9, tzinfo=timezone.utc),
            symbol="CON.F.US.NQ.H26",
        )
    )

    assert payload["fees"] == 5.22


def test_serialize_trade_event_includes_lifecycle_fields_when_provided():
    entry_time = datetime(2026, 2, 6, 11, 10, 9, tzinfo=timezone.utc)
    exit_time = datetime(2026, 2, 6, 11, 12, 9, tzinfo=timezone.utc)
    payload = serialize_trade_event(
        _event(pnl=Decimal("1312.500000")),
        lifecycle=TradeExecutionLifecycle(
            entry_timestamp=entry_time,
            exit_timestamp=exit_time,
            duration_minutes=2.0,
            entry_price=6850.25,
            exit_price=6858.75,
        ),
    )

    assert payload["entry_time"] == entry_time
    assert payload["exit_time"] == exit_time
    assert payload["duration_minutes"] == 2.0
    assert payload["entry_price"] == 6850.25
    assert payload["exit_price"] == 6858.75
