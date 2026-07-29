from __future__ import annotations

import logging
import os
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.services import projectx_hubs as hubs_module
from app.services import projectx_streaming_runtime as runtime_module
from app.services import projectx_trades as trades_module
from app.services.bot_service import BotRunEvaluationError
from app.services.projectx_client import (
    PROJECTX_ERROR_AUTH_FAILED,
    PROJECTX_ERROR_CONFIGURATION,
    PROJECTX_ERROR_NETWORK,
    PROJECTX_ERROR_PROVIDER_RESPONSE,
    ProjectXClientError,
)
from app.services.projectx_hubs import ProjectXHubRunner
from app.services.projectx_streaming_runtime import StreamingRuntime
from app.services.streaming_pnl_tracker import StreamingPnlTracker


SECRET_SENTINEL = "provider-secret-sentinel-never-log-or-return"


def _matching_record(caplog, message: str):
    return next(record for record in caplog.records if record.getMessage() == message)


def test_trade_provider_fetch_failure_logs_only_safe_classification(monkeypatch, caplog):
    def fail_client_factory():
        raise ProjectXClientError(
            f"{SECRET_SENTINEL}: {{'access_token': 'secret'}}",
            status_code=401,
            reason_code=PROJECTX_ERROR_AUTH_FAILED,
        )

    monkeypatch.setattr(trades_module, "_mark_trade_day_partial", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.ERROR, logger=trades_module.logger.name)

    with pytest.raises(ProjectXClientError):
        trades_module._sync_trade_day_from_provider(
            object(),
            client_factory=fail_client_factory,
            user_id="fixture-user",
            account_id=101,
            trade_day=date(2026, 7, 29),
            allow_complete=True,
        )

    record = _matching_record(caplog, "projectx_trade_day_sync_failed")
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == PROJECTX_ERROR_AUTH_FAILED
    assert record.error_type == "ProjectXClientError"
    assert record.phase == "provider_fetch"
    assert record.status_code == 401
    assert record.exc_info is None


def test_trade_persist_failure_logs_only_safe_classification(monkeypatch, caplog):
    fake_db = SimpleNamespace(rollback=lambda: None)
    monkeypatch.setattr(
        trades_module,
        "_fetch_trade_day_all_pages",
        lambda *_args, **_kwargs: trades_module._DayFetchResult(
            events=[],
            page_count=1,
            is_truncated=False,
            truncation_count=0,
        ),
    )
    monkeypatch.setattr(
        trades_module,
        "store_trade_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"{SECRET_SENTINEL}: full provider payload")
        ),
    )
    monkeypatch.setattr(trades_module, "_mark_trade_day_partial", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.ERROR, logger=trades_module.logger.name)

    with pytest.raises(RuntimeError):
        trades_module._sync_trade_day_from_provider(
            fake_db,
            client_factory=lambda: object(),
            user_id="fixture-user",
            account_id=102,
            trade_day=date(2026, 7, 29),
            allow_complete=True,
        )

    record = _matching_record(caplog, "projectx_trade_day_sync_failed")
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == "projectx_trade_sync_internal_error"
    assert record.error_type == "RuntimeError"
    assert record.phase == "local_persist"
    assert record.status_code is None
    assert record.exc_info is None


def test_hub_dispatch_failure_does_not_log_or_retain_payload_exception(monkeypatch, caplog):
    class FailingTracker(StreamingPnlTracker):
        def ingest_market_event(self, payload):
            raise RuntimeError(f"{SECRET_SENTINEL}: {payload!r}")

    runner = ProjectXHubRunner(
        tracker=FailingTracker(),
        client_factory=lambda: (_ for _ in ()).throw(AssertionError("unused")),
        market_hub_url="wss://example.test/hubs/market",
        user_hub_url="",
    )
    caplog.set_level(logging.ERROR, logger=hubs_module.logger.name)

    runner._dispatch_payload("market", {"apiKey": SECRET_SENTINEL})

    record = _matching_record(caplog, "projectx_hub_dispatch_failed")
    health = runner.dispatch_health()["market"]
    assert SECRET_SENTINEL not in caplog.text
    assert SECRET_SENTINEL not in str(health)
    assert health["last_error"] == "RuntimeError"
    assert record.reason_code == "projectx_hub_dispatch_error"
    assert record.error_type == "RuntimeError"
    assert record.stream_kind == "market"
    assert record.exc_info is None


def test_streaming_runtime_crash_does_not_log_exception_text(monkeypatch, caplog):
    runtime = StreamingRuntime(tracker=object(), runner=object())  # type: ignore[arg-type]

    async def fail_runtime():
        raise RuntimeError(f"{SECRET_SENTINEL}: bearer payload")

    monkeypatch.setattr(runtime, "_run_until_stopped", fail_runtime)
    caplog.set_level(logging.ERROR, logger=runtime_module.logger.name)

    runtime._run_thread()

    record = _matching_record(caplog, "projectx_streaming_runtime_crashed")
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == "projectx_streaming_runtime_error"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


def test_streaming_lifecycle_persist_failure_does_not_log_sensitive_context(monkeypatch, caplog):
    class FakeSession:
        def commit(self):
            raise AssertionError("commit must not follow a failed write")

        def rollback(self):
            return None

        def close(self):
            return None

    lifecycle = SimpleNamespace(
        user_id=SECRET_SENTINEL,
        account_id=201,
        contract_id=SECRET_SENTINEL,
        symbol="MNQ",
        opened_at=None,
        closed_at=None,
        mae_usd=None,
        mfe_usd=None,
        realized_pnl_usd=None,
        side=None,
        max_qty=None,
        avg_entry_at_open=None,
        mae_points=None,
        mfe_points=None,
        mae_timestamp=None,
        mfe_timestamp=None,
    )

    monkeypatch.setattr(runtime_module, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        runtime_module,
        "save_position_lifecycle_mae_mfe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"{SECRET_SENTINEL}: encrypted provider blob")
        ),
    )
    caplog.set_level(logging.ERROR, logger=runtime_module.logger.name)

    runtime_module._persist_closed_lifecycle(lifecycle)

    record = _matching_record(caplog, "projectx_streaming_lifecycle_persist_failed")
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == "projectx_streaming_persist_error"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


def test_trade_cache_fallback_internal_failure_does_not_log_exception_text(monkeypatch, caplog):
    fake_db = SimpleNamespace(rollback=lambda: None)
    monkeypatch.setattr(main_module, "get_projectx_account_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main_module,
        "ensure_trade_cache_for_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"{SECRET_SENTINEL}: provider response")
        ),
    )
    caplog.set_level(logging.ERROR, logger=main_module.logger.name)

    main_module._ensure_trade_cache_or_fallback(
        fake_db,
        user_id=SECRET_SENTINEL,
        account_id=301,
        start=None,
        end=None,
        refresh=False,
    )

    record = _matching_record(caplog, "projectx_trade_cache_sync_failed_using_local")
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == "projectx_trade_cache_sync_internal_error"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


def test_trade_cache_provider_fallback_log_uses_safe_reason_code(monkeypatch, caplog):
    fake_db = SimpleNamespace(rollback=lambda: None)
    monkeypatch.setattr(main_module, "get_projectx_account_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_has_imported_trade_history", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        main_module,
        "ensure_trade_cache_for_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProjectXClientError(
                f"{SECRET_SENTINEL}: provider response",
                status_code=504,
                reason_code=PROJECTX_ERROR_NETWORK,
            )
        ),
    )
    caplog.set_level(logging.WARNING, logger=main_module.logger.name)

    main_module._ensure_trade_cache_or_fallback(
        fake_db,
        user_id=SECRET_SENTINEL,
        account_id=302,
        start=None,
        end=None,
        refresh=False,
    )

    record = _matching_record(caplog, "projectx_trade_cache_sync_failed_using_local")
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == PROJECTX_ERROR_NETWORK
    assert record.status_code == 504
    assert record.exc_info is None


def test_backtest_stream_internal_failure_does_not_log_exception_text(caplog):
    caplog.set_level(logging.ERROR, logger=main_module.logger.name)

    response = main_module._backtest_stream_error(
        RuntimeError(f"{SECRET_SENTINEL}: provider-derived payload"),
    )

    record = _matching_record(caplog, "backtest_stream_failed")
    assert response == {"status": 500, "detail": "Backtest failed."}
    assert SECRET_SENTINEL not in caplog.text
    assert record.reason_code == "backtest_internal_error"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


@pytest.mark.parametrize(
    ("status_code", "reason_code", "expected_http_status"),
    [
        (None, PROJECTX_ERROR_CONFIGURATION, 500),
        (401, PROJECTX_ERROR_AUTH_FAILED, 502),
        (422, PROJECTX_ERROR_PROVIDER_RESPONSE, 502),
        (504, PROJECTX_ERROR_NETWORK, 504),
    ],
)
def test_projectx_http_error_mapping_never_returns_provider_text(
    status_code,
    reason_code,
    expected_http_status,
):
    error = ProjectXClientError(
        f"{SECRET_SENTINEL}: upstream body",
        status_code=status_code,
        reason_code=reason_code,
        submission_outcome_unknown=True,
    )

    http_error = main_module._to_http_exception(error)

    assert isinstance(http_error, HTTPException)
    assert http_error.status_code == expected_http_status
    assert http_error.detail["code"] == reason_code
    assert http_error.detail["submission_outcome_unknown"] is True
    assert SECRET_SENTINEL not in str(http_error.detail)


def test_bot_provider_error_response_keeps_safe_submission_state(monkeypatch):
    provider_error = ProjectXClientError(
        f"{SECRET_SENTINEL}: order request and response",
        status_code=504,
        reason_code=PROJECTX_ERROR_NETWORK,
        submission_outcome_unknown=True,
    )
    evaluation_error = BotRunEvaluationError(
        cause=provider_error,
        run=SimpleNamespace(),
        correlation_id="safe-correlation-id",
    )
    fake_db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "fixture-user")
    monkeypatch.setattr(
        main_module,
        "get_bot_config",
        lambda *_args, **_kwargs: SimpleNamespace(account_id=401),
    )
    monkeypatch.setattr(
        main_module,
        "_require_owned_projectx_account",
        lambda *_args, **_kwargs: SimpleNamespace(trade_data_source="projectx"),
    )
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        main_module,
        "start_bot_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(evaluation_error),
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module.start_trading_bot(bot_config_id=1, db=fake_db)

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {
        "code": PROJECTX_ERROR_NETWORK,
        "message": "ProjectX is temporarily unreachable. Try refreshing accounts again.",
        "submission_outcome_unknown": True,
        "status": "error",
        "correlation_id": "safe-correlation-id",
    }
    assert SECRET_SENTINEL not in str(exc_info.value.detail)
