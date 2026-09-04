from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib import error

import pytest

from app.services.projectx_client import (
    PROJECTX_ERROR_AUTH_FAILED,
    PROJECTX_ERROR_NETWORK,
    PROJECTX_ERROR_PROVIDER_RESPONSE,
    ProjectXClient,
    ProjectXClientError,
    _clear_token_cache,
    _extract_error_message,
    _parse_datetime,
)


@pytest.mark.parametrize("value", [2, -1, 0.5, float("nan"), float("inf"), "yes", "on", "maybe", {}, []])
def test_provider_boolean_metadata_rejects_unknown_values(value):
    from app.services.projectx_client import _safe_bool

    assert _safe_bool(value) is None


@pytest.mark.parametrize("value", [True, False, 1.5, float("nan"), float("inf"), "1.5"])
def test_provider_integer_fields_do_not_coerce_unknown_values(value):
    from app.services.projectx_client import _safe_int

    assert _safe_int(value) is None


def test_token_cache_repr_omits_bearer_token():
    from app.services.projectx_client import _TokenCache

    cache = _TokenCache(token="sensitive-fixture", expires_at=datetime.now(timezone.utc))
    assert "sensitive-fixture" not in repr(cache)


def test_access_token_lookup_prunes_expired_credentials(monkeypatch):
    import app.services.projectx_client as client_module

    client = ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture")
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(client_module, "_TOKEN_CACHE_BY_KEY", {
        "expired": client_module._TokenCache("old-secret", now - timedelta(seconds=1)),
        client._token_cache_key(): client_module._TokenCache("current-fixture", now + timedelta(minutes=5)),
    })
    assert client.get_access_token() == "current-fixture"
    assert "expired" not in client_module._TOKEN_CACHE_BY_KEY


@pytest.mark.parametrize("raw, expected", [("600", 600), ("-1", 0), ("nan", None), ("inf", None), ("garbage", None)])
def test_retry_after_header_parser_is_finite(raw, expected):
    from app.services.projectx_client import _retry_after_seconds

    assert _retry_after_seconds(raw) == expected


@pytest.mark.parametrize("url", ["http://example.test", "https://user:secret@example.test", "https://example.test/?token=secret", "https://example.test/#secret", "https://", "https://example.test:bad", "https://example.test/\npath"])
def test_client_refuses_insecure_or_credential_bearing_endpoint(url):
    with pytest.raises(ProjectXClientError) as exc_info:
        ProjectXClient(base_url=url, username="fixture", api_key="fixture")
    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize("timeout", [None, 0, -1, float("nan"), float("inf"), 61, True])
def test_client_requires_a_bounded_finite_timeout(timeout):
    with pytest.raises(ProjectXClientError):
        ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture", timeout_seconds=timeout)


def test_transport_redirect_does_not_forward_bearer_or_mutation(monkeypatch):
    import app.services.projectx_client as client_module
    from email.message import Message
    from urllib.response import addinfourl

    seen = []
    original_build = client_module.request.build_opener

    class FixtureHttpsHandler(client_module.request.HTTPSHandler):
        def https_open(self, req):
            seen.append((req.full_url, req.get_header("Authorization")))
            headers = Message()
            headers["Location"] = "https://other.example.test/steal"
            response = addinfourl(BytesIO(b"redirect"), headers, req.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(client_module.request, "build_opener", lambda *handlers: original_build(FixtureHttpsHandler(), *handlers))
    client = ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture")
    monkeypatch.setattr(client, "_get_access_token", lambda: "fixture-bearer")
    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Order/place", payload={"fixture": True}, with_auth=True)
    assert seen == [("https://example.test/api/Order/place", "Bearer fixture-bearer")]
    assert exc_info.value.status_code == 302
    assert exc_info.value.submission_outcome_unknown is True


@pytest.mark.parametrize("payload", [b"x" * 33, b"\xff"])
def test_oversized_or_invalid_utf8_submission_response_stays_ambiguous(monkeypatch, payload):
    import app.services.projectx_client as client_module

    monkeypatch.setattr(client_module, "_MAX_RESPONSE_BYTES", 32)
    monkeypatch.setattr(client_module, "_open_projectx_request", lambda *_args, **_kwargs: BytesIO(payload))
    client = ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture")
    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Order/place", payload=None, with_auth=False)
    assert exc_info.value.submission_outcome_unknown is True


def test_interrupted_submission_response_stays_ambiguous(monkeypatch):
    import app.services.projectx_client as client_module
    from http.client import IncompleteRead

    def fail(*_args, **_kwargs):
        raise IncompleteRead(b"fixture", 10)

    monkeypatch.setattr(client_module, "_open_projectx_request", fail)
    client = ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture")
    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Order/place", payload=None, with_auth=False)
    assert exc_info.value.submission_outcome_unknown is True


def test_strict_trade_history_requires_explicit_pnl_field():
    class StubClient(ProjectXClient):
        def _request(self, *_args, **_kwargs):
            return {"trades": [{"accountId": 123, "fees": 1, "voided": False,
                                "creationTimestamp": "2026-07-10T10:00:00Z"}]}

    client = StubClient(base_url="https://example.test", username="fixture", api_key="fixture")
    with pytest.raises(ProjectXClientError, match="P&L field"):
        client.fetch_trade_history(account_id=123, start=datetime(2026, 7, 10, tzinfo=timezone.utc), require_valid_collection=True)


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

        def read(self, *_args):
            return b'{"success": false, "responseStatus": {"message": "Session invalid"}}'

    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    monkeypatch.setattr("app.services.projectx_client._open_projectx_request", lambda *args, **kwargs: StubResponse())

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 502
    assert exc_info.value.reason_code == PROJECTX_ERROR_AUTH_FAILED
    assert str(exc_info.value) == "ProjectX authentication failed: Session invalid"


def test_request_once_maps_login_key_error_code_3_to_actionable_message(monkeypatch):
    class StubResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, *_args):
            return b'{"token": null, "success": false, "errorCode": 3, "errorMessage": null}'

    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    monkeypatch.setattr("app.services.projectx_client._open_projectx_request", lambda *args, **kwargs: StubResponse())

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 502
    assert exc_info.value.reason_code == PROJECTX_ERROR_AUTH_FAILED
    assert str(exc_info.value) == (
        "ProjectX authentication failed. Verify your TopstepX username and API key, "
        "and confirm ProjectX API access is active and your account is linked. "
        "(error code 3)"
    )


def test_request_once_maps_timeout_to_gateway_timeout(monkeypatch):
    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.services.projectx_client._open_projectx_request", raise_timeout)

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 504
    assert exc_info.value.reason_code == PROJECTX_ERROR_NETWORK
    assert exc_info.value.submission_outcome_unknown is False
    assert str(exc_info.value) == "ProjectX request timed out. Check the ProjectX connection and try again."


def test_request_once_maps_url_timeout_reason_to_gateway_timeout(monkeypatch):
    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    def raise_url_timeout(*_args, **_kwargs):
        raise error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr("app.services.projectx_client._open_projectx_request", raise_url_timeout)

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", "/api/Auth/loginKey", payload=None, with_auth=False)

    assert exc_info.value.status_code == 504
    assert exc_info.value.reason_code == PROJECTX_ERROR_NETWORK
    assert str(exc_info.value) == "ProjectX request timed out. Check the ProjectX connection and try again."


@pytest.mark.parametrize(
    ("path", "status_code", "outcome_unknown", "reason_code"),
    [
        ("/api/Order/place", 503, True, PROJECTX_ERROR_NETWORK),
        ("/api/Order/place", 408, True, PROJECTX_ERROR_NETWORK),
        ("/api/Order/cancel", 503, True, PROJECTX_ERROR_NETWORK),
        ("/api/Position/closeContract", 408, True, PROJECTX_ERROR_NETWORK),
        ("/api/Order/place", 400, False, PROJECTX_ERROR_PROVIDER_RESPONSE),
        ("/api/Trade/search", 503, False, PROJECTX_ERROR_NETWORK),
    ],
)
def test_request_once_only_marks_order_submission_5xx_as_ambiguous(
    monkeypatch,
    path,
    status_code,
    outcome_unknown,
    reason_code,
):
    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")

    def raise_http_error(*_args, **_kwargs):
        raise error.HTTPError(
            "https://example.test",
            status_code,
            "provider error",
            hdrs=None,
            fp=BytesIO(b'{"message":"provider error"}'),
        )

    monkeypatch.setattr("app.services.projectx_client._open_projectx_request", raise_http_error)

    with pytest.raises(ProjectXClientError) as exc_info:
        client._request_once("POST", path, payload={}, with_auth=False)

    assert exc_info.value.submission_outcome_unknown is outcome_unknown
    assert exc_info.value.reason_code == reason_code


def test_fetch_trade_history_retains_voided_rows_for_local_tombstones():
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

    assert [row["source_trade_id"] for row in rows] == ["1", "2", "3"]
    assert [row["voided"] for row in rows] == [False, True, True]


def test_strict_trade_history_rejects_missing_timestamp():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, *_args, **_kwargs):
            return {
                "trades": [
                    {
                        "accountId": 123,
                        "fees": 1.25,
                        "profitAndLoss": -10,
                        "voided": False,
                    }
                ]
            }

    with pytest.raises(ProjectXClientError, match="valid timestamp"):
        StubClient().fetch_trade_history(
            account_id=123,
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
            require_valid_collection=True,
        )


@pytest.mark.parametrize("overrides", [{"voided": None}, {"voided": "maybe"}, {"voided": 2}, {"accountId": 456}])
def test_strict_trade_history_rejects_uncertain_void_or_wrong_account(overrides):
    class StubClient(ProjectXClient):
        def _request(self, *_args, **_kwargs):
            return {"trades": [{
                "accountId": 123,
                "fees": 1.25,
                "profitAndLoss": -10,
                "voided": False,
                "creationTimestamp": "2026-07-10T10:00:00Z",
                **overrides,
            }]}

    client = StubClient(base_url="https://example.test", username="fixture", api_key="fixture")
    with pytest.raises(ProjectXClientError):
        client.fetch_trade_history(
            account_id=123,
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
            require_valid_collection=True,
        )


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


def test_retrieve_bars_infers_an_unmarked_intraday_tail_is_partial():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, method, path, *, payload=None, with_auth):
            return {
                "bars": [
                    {
                        "t": "2026-04-27T00:00:00Z",
                        "o": 100,
                        "h": 102,
                        "l": 99,
                        "c": 101,
                        "v": 10,
                    }
                ]
            }

    client = StubClient()
    request = {
        "contract_id": "CON.F.US.MNQ.M26",
        "live": False,
        "start": datetime(2026, 4, 27, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 4, 27, 3, 39, tzinfo=timezone.utc),
        "unit": 3,
        "unit_number": 4,
        "limit": 500,
    }

    assert client.retrieve_bars(**request, include_partial_bar=False) == []
    included = client.retrieve_bars(**request, include_partial_bar=True)
    assert len(included) == 1
    assert included[0]["is_partial"] is True

    closed_request = {**request, "end": datetime(2026, 4, 27, 4, 0, tzinfo=timezone.utc)}
    closed = client.retrieve_bars(**closed_request, include_partial_bar=False)
    assert len(closed) == 1
    assert closed[0]["is_partial"] is False


def test_retrieve_bars_does_not_trust_false_partial_marker_before_nominal_close():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, method, path, *, payload=None, with_auth):
            return {
                "bars": [
                    {
                        "t": "2026-04-27T00:00:00Z",
                        "o": 100,
                        "h": 102,
                        "l": 99,
                        "c": 101,
                        "v": 10,
                        "isPartial": False,
                    }
                ]
            }

    client = StubClient()
    request = {
        "contract_id": "CON.F.US.MNQ.M26",
        "live": False,
        "start": datetime(2026, 4, 27, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 4, 27, 3, 59, tzinfo=timezone.utc),
        "unit": 3,
        "unit_number": 4,
        "limit": 500,
    }

    assert client.retrieve_bars(**request, include_partial_bar=False) == []
    included = client.retrieve_bars(**request, include_partial_bar=True)
    assert len(included) == 1
    assert included[0]["is_partial"] is True


def test_retrieve_bars_does_not_close_a_future_bar_from_a_future_request_end():
    starts_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, method, path, *, payload=None, with_auth):
            return {
                "bars": [
                    {
                        "t": starts_at.isoformat(),
                        "o": 100,
                        "h": 102,
                        "l": 99,
                        "c": 101,
                        "v": 10,
                        "isPartial": False,
                    }
                ]
            }

    client = StubClient()
    request = {
        "contract_id": "CON.F.US.MNQ.M26",
        "live": False,
        "start": starts_at,
        "end": starts_at + timedelta(hours=1),
        "unit": 2,
        "unit_number": 5,
        "limit": 500,
    }

    assert client.retrieve_bars(**request, include_partial_bar=False) == []
    included = client.retrieve_bars(**request, include_partial_bar=True)
    assert included[0]["is_partial"] is True


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"c": "bad"}, "non-numeric"),
        ({"c": float("nan")}, "non-finite"),
        ({"c": 0}, "non-positive"),
        ({"h": 100, "c": 101}, "invalid OHLC envelope"),
        ({"v": -1}, "negative volume"),
        ({"v": True}, "non-numeric"),
        ({"isPartial": "maybe"}, "invalid market bar partial status"),
        ({"isPartial": None}, "invalid market bar partial status"),
    ],
)
def test_retrieve_bars_rejects_malformed_ohlcv(overrides, message):
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, method, path, *, payload=None, with_auth):
            bar = {
                "t": "2026-04-27T00:00:00Z",
                "o": 100,
                "h": 102,
                "l": 99,
                "c": 101,
                "v": 10,
            }
            bar.update(overrides)
            return {"bars": [bar]}

    with pytest.raises(ProjectXClientError, match=message):
        StubClient().retrieve_bars(
            contract_id="CON.F.US.MNQ.M26",
            live=False,
            start=datetime(2026, 4, 27, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 27, 0, 5, tzinfo=timezone.utc),
            unit=2,
            unit_number=5,
            limit=500,
        )


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


def test_place_order_uses_projectx_sell_market_payload_with_brackets():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {"orderId": 9057, "success": True}

    client = StubClient()

    response = client.place_order(
        account_id=123,
        contract_id="CON.F.US.MNQ.M26",
        order_type=2,
        side=1,
        size=2,
        stop_loss_bracket={"ticks": 4, "type": 4},
        take_profit_bracket={"ticks": 8, "type": 1},
        custom_tag="bot-test-sell",
    )

    assert client.calls == [
        (
            "POST",
            "/api/Order/place",
            {
                "accountId": 123,
                "contractId": "CON.F.US.MNQ.M26",
                "type": 2,
                "side": 1,
                "size": 2,
                "customTag": "bot-test-sell",
                "stopLossBracket": {"ticks": 4, "type": 4},
                "takeProfitBracket": {"ticks": 8, "type": 1},
            },
            True,
        )
    ]
    assert response["order_id"] == "9057"


def test_cancel_order_sends_numeric_order_id_and_requires_success_acknowledgement():
    class StubClient(ProjectXClient):
        def __init__(self, response):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.response = response
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return self.response

    client = StubClient({"success": True})
    response = client.cancel_order(account_id=123, order_id="789")

    assert client.calls == [
        ("POST", "/api/Order/cancel", {"accountId": 123, "orderId": 789}, True)
    ]
    assert response["success"] is True

    with pytest.raises(ProjectXClientError, match="positive integer order ID"):
        client.cancel_order(account_id=123, order_id="opaque-id")

    ambiguous = StubClient({})
    with pytest.raises(ProjectXClientError) as exc_info:
        ambiguous.cancel_order(account_id=123, order_id=789)
    assert exc_info.value.submission_outcome_unknown is True


def test_close_position_uses_full_contract_endpoint_and_requires_success_acknowledgement():
    class StubClient(ProjectXClient):
        def __init__(self, response):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.response = response
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return self.response

    client = StubClient({"success": True})
    response = client.close_position(
        account_id=123,
        contract_id="CON.F.US.MNQ.M26",
    )

    assert client.calls == [
        (
            "POST",
            "/api/Position/closeContract",
            {"accountId": 123, "contractId": "CON.F.US.MNQ.M26"},
            True,
        )
    ]
    assert response["success"] is True

    ambiguous = StubClient({"success": True, "unexpected": "shape"})
    assert ambiguous.close_position(account_id=123, contract_id="CON.F.US.MNQ.M26")["success"] is True


def test_search_open_positions_returns_authoritative_signed_sizes():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "positions": [
                    {
                        "id": 11,
                        "accountId": 123,
                        "contractId": "CON.F.US.MNQ.M26",
                        "creationTimestamp": "2026-07-10T14:00:00Z",
                        "type": 1,
                        "size": 2,
                        "averagePrice": 20100.25,
                        "unrealizedPnl": -37.5,
                    },
                    {
                        "id": 12,
                        "accountId": 123,
                        "contractId": "CON.F.US.MES.M26",
                        "creationTimestamp": "2026-07-10T14:01:00Z",
                        "type": 2,
                        "size": 1,
                        "averagePrice": 6000,
                    },
                ]
            }

    client = StubClient()
    rows = client.search_open_positions(account_id=123)

    assert client.calls == [("POST", "/api/Position/searchOpen", {"accountId": 123}, True)]
    assert [row["signed_size"] for row in rows] == [2.0, -1.0]
    assert [row["unrealized_pnl"] for row in rows] == [-37.5, None]


def test_search_open_positions_rejects_malformed_collection_instead_of_assuming_flat():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, *_args, **_kwargs):
            return {"success": True}

    with pytest.raises(ProjectXClientError, match="invalid response collection"):
        StubClient().search_open_positions(account_id=123)


def test_search_orders_normalizes_custom_tag_for_reconciliation():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "orders": [
                    {
                        "id": 456,
                        "accountId": 123,
                        "contractId": "CON.F.US.MNQ.M26",
                        "creationTimestamp": "2026-07-10T14:00:00Z",
                        "updateTimestamp": "2026-07-10T14:00:01Z",
                        "status": 2,
                        "customTag": "topsignal-1-abc",
                    }
                ]
            }

    client = StubClient()
    start = datetime(2026, 7, 10, 13, 55, tzinfo=timezone.utc)
    end = datetime(2026, 7, 10, 14, 5, tzinfo=timezone.utc)
    rows = client.search_orders(account_id=123, start=start, end=end)

    assert client.calls == [
        (
            "POST",
            "/api/Order/search",
            {
                "accountId": 123,
                "startTimestamp": "2026-07-10T13:55:00Z",
                "endTimestamp": "2026-07-10T14:05:00Z",
            },
            True,
        )
    ]
    assert rows[0]["order_id"] == "456"
    assert rows[0]["custom_tag"] == "topsignal-1-abc"


def test_search_orders_rejects_wrong_account_rows():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, *_args, **_kwargs):
            return {"orders": [{"id": 456, "accountId": 999}]}

    with pytest.raises(ProjectXClientError, match="wrong account"):
        StubClient().search_orders(
            account_id=123,
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )


def test_search_open_orders_requires_authoritative_working_order_rows():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "orders": [
                    {
                        "id": 789,
                        "accountId": 123,
                        "contractId": "CON.F.US.MNQ.M26",
                        "status": 1,
                        "side": 0,
                        "size": 1,
                        "customTag": "topsignal-working",
                    }
                ]
            }

    client = StubClient()

    rows = client.search_open_orders(account_id=123)

    assert client.calls == [("POST", "/api/Order/searchOpen", {"accountId": 123}, True)]
    assert rows == [
        {
            "order_id": "789",
            "account_id": 123,
            "contract_id": "CON.F.US.MNQ.M26",
            "status": 1,
            "order_type": None,
            "side": 0,
            "size": 1.0,
            "signed_size": 1.0,
            "custom_tag": "topsignal-working",
            "parent_order_id": None,
            "raw_payload": {
                "id": 789,
                "accountId": 123,
                "contractId": "CON.F.US.MNQ.M26",
                "status": 1,
                "side": 0,
                "size": 1,
                "customTag": "topsignal-working",
            },
        }
    ]


@pytest.mark.parametrize(
    ("field", "kwargs", "message"),
    [
        ("order_type", {"order_type": 999}, "Unsupported ProjectX order type."),
        ("side", {"side": 3}, "Unsupported ProjectX order side."),
        ("size", {"size": 1.5}, "ProjectX order size must be a positive whole number."),
        ("size", {"size": 0}, "ProjectX order size must be a positive whole number."),
        ("size", {"size": float("inf")}, "ProjectX order size must be a positive whole number."),
        ("size", {"size": 10_001}, "ProjectX order size must be a positive whole number."),
    ],
)
def test_place_order_validates_projectx_order_enums_and_size(field, kwargs, message):
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {"orderId": 9058, "success": True}

    client = StubClient()
    base_kwargs = {
        "account_id": 123,
        "contract_id": "CON.F.US.MNQ.M26",
        "order_type": 2,
        "side": 0,
        "size": 1,
    }
    base_kwargs.update(kwargs)

    with pytest.raises(ProjectXClientError) as exc_info:
        client.place_order(**base_kwargs)

    assert field in kwargs
    assert str(exc_info.value) == message
    assert client.calls == []


def test_list_accounts_uses_search_endpoint_with_only_active_accounts_true():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")
            self.calls = []

        def _request(self, method, path, *, payload=None, with_auth):
            self.calls.append((method, path, payload, with_auth))
            return {
                "accounts": [
                    {
                        "id": 5,
                        "name": "ACTIVE_5",
                        "balance": 50000,
                        "canTrade": True,
                        "isVisible": True,
                        "isSimulated": True,
                    },
                    {"id": 6, "name": "NO_TRADE", "balance": 25000, "canTrade": False, "isVisible": True},
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
            "is_visible": True,
            "simulated": True,
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
                    {"id": 6, "name": "NO_TRADE", "balance": 25000, "canTrade": False, "isVisible": True},
                    {"id": 5, "name": "ACTIVE_5", "balance": 50000, "canTrade": True, "isVisible": True},
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
            "is_visible": True,
            "simulated": None,
        },
        {
            "id": 6,
            "name": "NO_TRADE",
            "balance": 25000.0,
            "status": "LOCKED_OUT",
            "can_trade": False,
            "is_visible": True,
            "simulated": None,
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
            "simulated": None,
        }
    ]
    assert rows_active == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"accounts": ["invalid"]},
        {"accounts": [{"canTrade": True, "isVisible": True}]},
        {"accounts": [{"id": "invalid", "canTrade": True, "isVisible": True}]},
        {"accounts": [{"id": 123, "isVisible": True}]},
        {"accounts": [{"id": 123, "canTrade": True}]},
    ],
)
def test_list_accounts_rejects_malformed_authoritative_payload(payload):
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, *_args, **_kwargs):
            return payload

    with pytest.raises(ProjectXClientError):
        StubClient().list_accounts(only_active_accounts=False)


def test_list_accounts_treats_non_finite_balance_as_unavailable():
    class StubClient(ProjectXClient):
        def __init__(self):
            super().__init__(base_url="https://example.test", username="demo", api_key="demo")

        def _request(self, *_args, **_kwargs):
            return {
                "accounts": [
                    {"id": 123, "canTrade": True, "isVisible": True, "balance": float("inf")}
                ]
            }

    assert StubClient().list_accounts(only_active_accounts=False)[0]["balance"] is None


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


def test_fetch_last_trade_timestamp_ignores_voided_execution(monkeypatch):
    client = ProjectXClient(base_url="https://example.test", username="demo", api_key="demo")
    older = datetime(2026, 7, 9, tzinfo=timezone.utc)
    newer_voided = datetime(2026, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(
        client,
        "fetch_trade_history",
        lambda **_kwargs: [
            {"timestamp": older, "voided": False},
            {"timestamp": newer_voided, "voided": True},
        ],
    )

    assert client.fetch_last_trade_timestamp(123) == older


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
