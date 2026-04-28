import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.projectx_hubs import ProjectXHubRunner, _append_query
from app.services.streaming_pnl_tracker import StreamingPnlTracker


def test_market_gateway_quote_dispatch_preserves_contract_id_argument():
    tracker = StreamingPnlTracker()
    runner = ProjectXHubRunner(
        tracker=tracker,
        market_hub_url="wss://example.test/hubs/market",
    )

    runner._dispatch_frame(
        "market",
        {
            "type": 1,
            "target": "GatewayQuote",
            "arguments": [
                "CON.F.US.MNQ.H26",
                {
                    "symbol": "F.US.MNQ",
                    "lastPrice": 17425.25,
                    "timestamp": "2026-03-01T12:01:00Z",
                },
            ],
        },
    )

    update = tracker.get_market_price_update(contract_id="CON.F.US.MNQ.H26")

    assert update is not None
    assert update.contract_id == "CON.F.US.MNQ.H26"
    assert update.mark_price == 17425.25


def test_append_query_normalizes_documented_https_hub_url_to_wss():
    assert (
        _append_query("https://rtc.topstepx.com/hubs/market", {"access_token": "token"})
        == "wss://rtc.topstepx.com/hubs/market?access_token=token"
    )
