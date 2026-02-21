from datetime import datetime, timezone

from app.services.projectx_client import ProjectXClient, _parse_datetime


def test_parse_datetime_supports_variable_fraction_precision():
    parsed = _parse_datetime("2026-02-05T19:49:57.22185+00:00")

    assert parsed == datetime(2026, 2, 5, 19, 49, 57, 221850, tzinfo=timezone.utc)


def test_parse_datetime_supports_utc_z_suffix():
    parsed = _parse_datetime("2026-02-05T19:49:57.22185Z")

    assert parsed == datetime(2026, 2, 5, 19, 49, 57, 221850, tzinfo=timezone.utc)


def test_parse_datetime_supports_offsets_without_colon():
    parsed = _parse_datetime("2026-02-05T19:49:57.22185+0000")

    assert parsed == datetime(2026, 2, 5, 19, 49, 57, 221850, tzinfo=timezone.utc)


def test_fetch_trade_history_skips_voided_rows():
    class StubClient(ProjectXClient):
        def __init__(self, payload):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.payload = payload

        def _request(self, *args, **kwargs):
            return self.payload

    payload = {
        "trades": [
            {
                "id": 1,
                "accountId": 123,
                "contractId": "CON.F.US.MGC.Z25",
                "creationTimestamp": "2025-10-20T06:45:01.197595+00:00",
                "price": 4245.8,
                "profitAndLoss": 825.0,
                "fees": 9.3,
                "side": 0,
                "size": 15,
                "voided": False,
                "orderId": 1759109439,
            },
            {
                "id": 2,
                "accountId": 123,
                "contractId": "CON.F.US.MGC.Z25",
                "creationTimestamp": "2025-10-20T07:00:15.643821+00:00",
                "price": 4251.3,
                "profitAndLoss": -30.0,
                "fees": 9.3,
                "side": 0,
                "size": 15,
                "voided": True,
                "orderId": 1759115612,
            },
            {
                "id": 3,
                "accountId": 123,
                "contractId": "CON.F.US.ENQ.Z25",
                "creationTimestamp": "2025-10-20T14:45:51.313552+00:00",
                "price": 25306.25,
                "profitAndLoss": -6255.0,
                "fees": 4.2,
                "side": 0,
                "size": 3,
                "voided": "true",
                "orderId": 1760022835,
            },
        ]
    }

    client = StubClient(payload)

    rows = client.fetch_trade_history(account_id=123, start=datetime(2025, 10, 20, tzinfo=timezone.utc))

    assert len(rows) == 1
    assert rows[0]["source_trade_id"] == "1"
