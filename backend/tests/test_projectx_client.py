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


def test_list_accounts_uses_search_endpoint_with_only_active_accounts_true():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "accounts": [
                    {"id": 5, "name": "ACTIVE_5", "balance": 50000, "canTrade": True},
                    {"id": 6, "name": "NO_TRADE", "balance": 25000, "canTrade": False},
                ]
            }

    client = StubClient()

    rows = client.list_accounts(only_active_accounts=True)

    assert client.calls == [("POST", "/api/Account/search", {"onlyActiveAccounts": True}, True)]
    assert rows == [
        {
            "id": 5,
            "name": "ACTIVE_5",
            "balance": 50000.0,
            "status": "ACTIVE",
            "can_trade": True,
            "is_visible": None,
        }
    ]


def test_list_accounts_can_request_all_accounts():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "accounts": [
                    {"id": 6, "name": "NO_TRADE", "balance": 25000, "canTrade": False},
                    {"id": 5, "name": "ACTIVE_5", "balance": 50000, "canTrade": True},
                ]
            }

    client = StubClient()
    rows = client.list_accounts(only_active_accounts=False)

    assert client.calls == [("POST", "/api/Account/search", {"onlyActiveAccounts": False}, True)]
    assert rows == [
        {
            "id": 5,
            "name": "ACTIVE_5",
            "balance": 50000.0,
            "status": "ACTIVE",
            "can_trade": True,
            "is_visible": None,
        },
        {
            "id": 6,
            "name": "NO_TRADE",
            "balance": 25000.0,
            "status": "LOCKED_OUT",
            "can_trade": False,
            "is_visible": None,
        },
    ]


def test_list_accounts_marks_hidden_when_is_visible_false():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, method, path, *, payload=None, with_auth):
            return {
                "accounts": [
                    {"id": 9, "name": "HIDDEN_9", "balance": 15000, "canTrade": True, "isVisible": False},
                ]
            }

    client = StubClient()

    rows_all = client.list_accounts(only_active_accounts=False)
    rows_active = client.list_accounts(only_active_accounts=True)

    assert rows_all == [
        {
            "id": 9,
            "name": "HIDDEN_9",
            "balance": 15000.0,
            "status": "HIDDEN",
            "can_trade": True,
            "is_visible": False,
        }
    ]
    assert rows_active == []


def test_fetch_last_trade_timestamp_returns_latest_value():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def fetch_trade_history(self, account_id, start, end=None, *, limit=None, offset=None):
            self.calls.append((account_id, start, end, limit, offset))
            return [
                {
                    "account_id": account_id,
                    "timestamp": datetime(2026, 2, 15, 18, 45, tzinfo=timezone.utc),
                    "order_id": "A-1",
                }
            ]

    client = StubClient()
    timestamp = client.fetch_last_trade_timestamp(account_id=777, lookback_days=90)

    assert timestamp == datetime(2026, 2, 15, 18, 45, tzinfo=timezone.utc)
    assert len(client.calls) == 1
    account_id, _start, _end, limit, offset = client.calls[0]
    assert account_id == 777
    assert limit == 1
    assert offset is None
