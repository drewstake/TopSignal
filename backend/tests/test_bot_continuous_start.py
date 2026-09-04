import os

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.bot_schemas import BotStartIn
from app.bot_worker import BotWorkerRuntime, BotWorkerSettings
from app.services.bot_service import AccountEmergencyLatchActiveError


def _disabled_runtime():
    return BotWorkerRuntime(
        session_factory=lambda: None,
        client_factory=lambda *_args, **_kwargs: None,
        settings=BotWorkerSettings(enabled=False),
    )


def test_continuous_start_rejects_before_loading_or_arming_bot(monkeypatch):
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(main_module, "_bot_worker_runtime", _disabled_runtime())
    monkeypatch.setattr(
        main_module,
        "get_bot_config",
        lambda *_args, **_kwargs: pytest.fail("config must not be loaded after admission failure"),
    )
    monkeypatch.setattr(
        main_module,
        "start_bot_run",
        lambda *_args, **_kwargs: pytest.fail("run must not be armed after admission failure"),
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module.start_trading_bot(
            1,
            BotStartIn(dry_run=True, continuous=True),
            db=object(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "bot_worker_disabled"


def test_start_contract_rejects_unsupported_controls_before_arming(monkeypatch):
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(
        main_module,
        "start_bot_run",
        lambda *_args, **_kwargs: pytest.fail("run must not be armed"),
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module.start_trading_bot(
            1,
            BotStartIn(dry_run=True, continuous=True, poll_interval_seconds=2),
            db=object(),
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "per_run_poll_interval_is_not_supported"


def test_start_contract_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        BotStartIn.model_validate({"dry_run": True, "mystery_mode": True})


def test_continuous_live_start_rejects_when_worker_live_gate_is_disabled(
    monkeypatch,
):
    runtime = BotWorkerRuntime(
        session_factory=lambda: None,
        client_factory=lambda *_args, **_kwargs: None,
        settings=BotWorkerSettings(enabled=True),
    )
    monkeypatch.setenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "false")
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(main_module, "_bot_worker_runtime", runtime)
    monkeypatch.setattr(
        main_module,
        "get_bot_config",
        lambda *_args, **_kwargs: pytest.fail("config must not be loaded after admission failure"),
    )
    monkeypatch.setattr(
        main_module,
        "start_bot_run",
        lambda *_args, **_kwargs: pytest.fail("run must not be armed after admission failure"),
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module.start_trading_bot(
            1,
            BotStartIn(dry_run=False, continuous=True, confirm_live_order_routing=True),
            db=object(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "bot_worker_live_execution_disabled"


def test_evaluate_contract_rejects_continuous_controls_before_loading_bot(monkeypatch):
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(
        main_module,
        "get_bot_config",
        lambda *_args, **_kwargs: pytest.fail("config must not be loaded"),
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module.evaluate_trading_bot(
            1,
            BotStartIn(dry_run=True, continuous=True),
            db=object(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "continuous_mode_requires_start_endpoint"


def test_start_route_returns_stable_conflict_for_unresolved_emergency_latch(monkeypatch):
    class Db:
        def rollback(self):
            pass

    config = type("Config", (), {"id": 7, "account_id": 101})()
    account = type("Account", (), {"trade_data_source": "projectx"})()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(main_module, "get_bot_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        main_module,
        "_require_owned_projectx_account",
        lambda *_args, **_kwargs: account,
    )
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: object())

    def blocked(*_args, **_kwargs):
        raise AccountEmergencyLatchActiveError(action_id=19, status="unconfirmed")

    monkeypatch.setattr(main_module, "start_bot_run", blocked)

    with pytest.raises(HTTPException) as exc_info:
        main_module.start_trading_bot(
            7,
            BotStartIn(dry_run=True, continuous=False),
            db=Db(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "account_emergency_flatten_unresolved",
        "account_emergency_action_id": 19,
        "account_emergency_status": "unconfirmed",
    }
