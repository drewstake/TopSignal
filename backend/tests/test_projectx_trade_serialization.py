from datetime import datetime, timezone
from decimal import Decimal

from app.models import ProjectXTradeEvent
from app.services.projectx_trades import _to_metric_sample, serialize_trade_event


def _event(*, pnl: Decimal | None) -> ProjectXTradeEvent:
    return ProjectXTradeEvent(
        id=1,
        account_id=13048312,
        contract_id="CON.F.US.MES.H26",
        symbol="CON.F.US.MES.H26",
        side="SELL",
        size=Decimal("3.000000"),
        price=Decimal("6858.750000"),
        trade_timestamp=datetime(2026, 2, 6, 11, 12, 9, tzinfo=timezone.utc),
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
