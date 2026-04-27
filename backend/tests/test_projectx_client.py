from datetime import datetime, timezone
from urllib import error

import pytest

from app.services.projectx_client import (
    ProjectXClient,
    ProjectXClientError,
    _clear_token_cache,
    _extract_error_message,
    _parse_datetime,
)


def test_parse_datetime_supports_variable_fraction_precision():
    parsed = _parse_datetime("2026-02-05T19:49:57.22185+00:00")

    assert parsed == datetime(2026, 2, 5, 19, 49, 57, 221850, tzinfo=timezone.utc)


def test_parse_datetime_supports_utc_z_suffix():
    parsed = _parse_datetime("2026-02-05T19:49:57.22185Z")

    assert parsed == datetime(2026, 2, 5, 19, 49, 57, 221850, tzinfo=timezone.utc)


def test_parse_datetime_supports_offsets_without_colon():
    parsed = _parse_datetime("2026-02-05T19:49:57.22185+0000")

    assert parsed == datetime(2026, 2, 5, 19, 49, 57, 221850, tzinfo=timezone.utc)


def test_extract_error_message_formats_validation_error_maps():
    payload = {
        "success": False,
        "title": "One or more validation errors occurred.",
        "errors": {
            "contractId": ["The contractId field is required."],
            "accountId": ["The accountId field must be greater than 0."],
        },
    }

    assert _extract_error_message(payload) == (
        "contractId: The contractId field is required.; "
        "accountId: The accountId field must be greater than 0."
    )


def test_extract_error_message_reads_nested_provider_messages():
    payload = {
        "responseStatus": {
            "errorCode": "SESSION_INVALID",
            "message": "Session invalid",
        }
    }

    assert _extract_error_message(payload) == "Session invalid"


def test_extract_error_message_falls_back_to_error_code_when_message_missing():
    payload = {
        "success": False,
        "responseStatus": {
            "errorCode": 40123,
            "errorMessage": None,
        },
    }

    assert _extract_error_message(payload) == "Error code 40123"


def test_request_once_marks_success_false_payloads_as_gateway_errors(monkeypatch):
    class StubResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"success": false, "responseStatus": {"message": "Session invalid"}}'

    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    monkeypatch.setattr("app.services.projectx_client.request.urlopen", lambda *args, **kwargs: StubResponse())

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 502
    assert str(exc_info.value) == "ProjectX authentication failed: Session invalid"


def test_request_once_maps_login_key_error_code_3_to_actionable_message(monkeypatch):
    class StubResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"token": null, "success": false, "errorCode": 3, "errorMessage": null}'

    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    monkeypatch.setattr("app.services.projectx_client.request.urlopen", lambda *args, **kwargs: StubResponse())

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 502
    assert str(exc_info.value) == (
        "ProjectX authentication failed. Verify your TopstepX username and API key, "
        "and confirm ProjectX API access is active and your account is linked. "
        "(error code 3)"
    )


def test_request_once_maps_timeout_to_gateway_timeout(monkeypatch):
    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.services.projectx_client.request.urlopen", raise_timeout)

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 504
    assert str(exc_info.value) == "ProjectX request timed out. Check the ProjectX connection and try again."


def test_request_once_maps_url_timeout_reason_to_gateway_timeout(monkeypatch):
    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    def raise_url_timeout(*_args, **_kwargs):
        raise error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr("app.services.projectx_client.request.urlopen", raise_url_timeout)

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 504
    assert str(exc_info.value) == "ProjectX request timed out. Check the ProjectX connection and try again."


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


def test_search_contracts_normalizes_projectx_contract_rows():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "contracts": [
                    {
                        "id": "CON.F.US.MNQ.M26",
                        "name": "MNQM6",
                        "description": "Micro E-mini Nasdaq-100: June 2026",
                        "tickSize": 0.25,
                        "tickValue": 0.5,
                        "activeContract": True,
                        "symbolId": "F.US.MNQ",
                    }
                ]
            }

    client = StubClient()

    rows = client.search_contracts(search_text="MNQ", live=False)

    assert client.calls == [("POST", "/api/Contract/search", {"searchText": "MNQ", "live": False}, True)]
    assert rows[0]["id"] == "CON.F.US.MNQ.M26"
    assert rows[0]["tick_size"] == 0.25
    assert rows[0]["symbol_id"] == "F.US.MNQ"


def test_retrieve_bars_normalizes_and_sorts_ohlcv_rows():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "bars": [
                    {"t": "2026-04-01T10:05:00Z", "o": 102, "h": 105, "l": 101, "c": 104, "v": 20},
                    {"t": "2026-04-01T10:00:00Z", "o": 100, "h": 103, "l": 99, "c": 102, "v": 10},
                ]
            }

    client = StubClient()

    rows = client.retrieve_bars(
        contract_id="CON.F.US.MNQ.M26",
        live=False,
        start=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
        unit=2,
        unit_number=5,
        limit=500,
    )

    assert client.calls[0][1] == "/api/History/retrieveBars"
    assert client.calls[0][2]["includePartialBar"] is False
    assert [row["timestamp"] for row in rows] == [
        datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
    ]
    assert rows[1]["close"] == 104.0


def test_place_order_uses_projectx_order_place_payload():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {"orderId": 9056, "success": True}

    client = StubClient()

    response = client.place_order(
        account_id=123,
        contract_id="CON.F.US.MNQ.M26",
        order_type=2,
        side=0,
        size=1,
        custom_tag="bot-test",
    )

    assert client.calls == [
        (
            "POST",
            "/api/Order/place",
            {
                "accountId": 123,
                "contractId": "CON.F.US.MNQ.M26",
                "type": 2,
                "side": 0,
                "size": 1,
                "customTag": "bot-test",
            },
            True,
        )
    ]
    assert response["order_id"] == "9056"


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


def test_fetch_last_trade_timestamp_uses_latest_row_when_provider_returns_multiple_rows():
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
                },
                {
                    "account_id": account_id,
                    "timestamp": datetime(2026, 2, 16, 9, 30, tzinfo=timezone.utc),
                    "order_id": "A-2",
                },
                {
                    "account_id": account_id,
                    "timestamp": datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc),
                    "order_id": "A-0",
                },
            ]

    client = StubClient()
    timestamp = client.fetch_last_trade_timestamp(account_id=778, lookback_days=90)

    assert timestamp == datetime(2026, 2, 16, 9, 30, tzinfo=timezone.utc)
    assert len(client.calls) == 1
    account_id, _start, _end, limit, offset = client.calls[0]
    assert account_id == 778
    assert limit == 1
    assert offset is None


def test_access_token_cache_is_invalidated_when_api_key_changes():
    class StubClient(ProjectXClient):
        def __init__(self, api_key: str, login_calls: list[str]):
            super().__init__(base_url="https://example.test", username="demo", api_key=api_key)
            self.login_calls = login_calls

        def _request_once(self, method, path, *, payload=None, with_auth):
            assert method == "POST"
            assert path == "/api/Auth/loginKey"
            assert with_auth is False
            assert payload is not None
            self.login_calls.append(str(payload["apiKey"]))
            return {
                "token": f"token-for-{payload['apiKey']}",
                "expiresInSeconds": 3600,
            }

    login_calls: list[str] = []
    _clear_token_cache()
    try:
        first_client = StubClient(api_key="key-one", login_calls=login_calls)
        second_client = StubClient(api_key="key-two", login_calls=login_calls)

        assert first_client.get_access_token() == "token-for-key-one"
        assert second_client.get_access_token() == "token-for-key-two"
        assert login_calls == ["key-one", "key-two"]
    finally:
        _clear_token_cache()
