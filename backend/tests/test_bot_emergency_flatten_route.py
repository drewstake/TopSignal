import json
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.bot_schemas import (
    AccountEmergencyFlattenOut,
    BotEmergencyFlattenIn,
    BotEmergencyFlattenOut,
)
from app.services.bot_risk import RiskBlock


class _Db:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _serialized_run():
    return {
        "id": 9,
        "bot_config_id": 2,
        "account_id": 3,
        "status": "stopped",
        "dry_run": False,
        "started_at": "2026-09-03T10:00:00Z",
        "stopped_at": "2026-09-03T10:01:00Z",
        "stop_reason": "manual_emergency_flatten",
        "last_heartbeat_at": None,
        "last_error": None,
        "last_evaluated_at": None,
    }


def test_emergency_flatten_contract_requires_literal_true_and_forbids_extras():
    with pytest.raises(ValidationError):
        BotEmergencyFlattenIn.model_validate({"confirm_broker_flatten": False})
    with pytest.raises(ValidationError):
        BotEmergencyFlattenIn.model_validate({"confirm_broker_flatten": 1})
    with pytest.raises(ValidationError):
        BotEmergencyFlattenIn.model_validate(
            {"confirm_broker_flatten": True, "confirm_live_order_routing": True}
        )


def test_emergency_flatten_success_returns_verified_contract(monkeypatch):
    db = _Db()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(main_module, "serialize_bot_run", lambda _run: _serialized_run())
    monkeypatch.setattr(
        main_module,
        "emergency_flatten_bot_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=object(),
            confirmed_flat=True,
            status="confirmed_account_flat",
            risk_block=None,
            audit={"scope": "entire_account", "confirmed_flat": True},
        ),
    )

    result = main_module.emergency_flatten_trading_bot(
        2,
        BotEmergencyFlattenIn(confirm_broker_flatten=True),
        db=db,
    )

    assert isinstance(result, BotEmergencyFlattenOut)
    assert result.confirmed_flat is True
    assert result.status == "confirmed_account_flat"
    assert db.commits == 1
    assert db.rollbacks == 0


def test_emergency_flatten_unverified_commits_audit_then_returns_409(monkeypatch):
    db = _Db()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(main_module, "serialize_bot_run", lambda _run: _serialized_run())
    monkeypatch.setattr(
        main_module,
        "emergency_flatten_bot_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=object(),
            confirmed_flat=False,
            status="unconfirmed",
            risk_block=RiskBlock(
                code="emergency_flatten_verification_failed",
                message="Broker state could not be verified flat.",
                severity="critical",
            ),
            audit={"scope": "entire_account", "confirmed_flat": False},
        ),
    )

    response = main_module.emergency_flatten_trading_bot(
        2,
        BotEmergencyFlattenIn(confirm_broker_flatten=True),
        db=db,
    )

    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["confirmed_flat"] is False
    assert payload["status"] == "unconfirmed"
    assert payload["risk_block"]["severity"] == "critical"
    assert payload["audit"]["scope"] == "entire_account"
    assert db.commits == 1
    assert db.rollbacks == 0


def test_account_emergency_flatten_does_not_require_a_bot_config(monkeypatch):
    db = _Db()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(
        main_module,
        "emergency_flatten_account",
        lambda *_args, **_kwargs: SimpleNamespace(
            account_id=9001,
            audit_id=17,
            confirmed_flat=True,
            status="confirmed_account_flat",
            risk_block=None,
            audit={"scope": "entire_account", "account_bot_config_ids": []},
            disabled_bot_config_ids=(),
            stopped_bot_run_ids=(),
        ),
    )

    result = main_module.emergency_flatten_projectx_account(
        9001,
        BotEmergencyFlattenIn(confirm_broker_flatten=True),
        db=db,
    )

    assert isinstance(result, AccountEmergencyFlattenOut)
    assert result.account_id == 9001
    assert result.audit_id == 17
    assert result.confirmed_flat is True
    assert result.disabled_bot_config_ids == []
    assert result.stopped_bot_run_ids == []
    assert db.commits == 1
    assert db.rollbacks == 0


def test_account_emergency_flatten_unconfirmed_returns_structured_409(monkeypatch):
    db = _Db()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(
        main_module,
        "emergency_flatten_account",
        lambda *_args, **_kwargs: SimpleNamespace(
            account_id=9001,
            audit_id=18,
            confirmed_flat=False,
            status="unconfirmed",
            risk_block=RiskBlock(
                code="broker_account_flatten_unconfirmed",
                message="Broker state could not be verified flat.",
                severity="critical",
            ),
            audit={"scope": "entire_account", "confirmed_flat": False},
            disabled_bot_config_ids=(2, 3),
            stopped_bot_run_ids=(8,),
        ),
    )

    response = main_module.emergency_flatten_projectx_account(
        9001,
        BotEmergencyFlattenIn(confirm_broker_flatten=True),
        db=db,
    )

    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["account_id"] == 9001
    assert payload["audit_id"] == 18
    assert payload["status"] == "unconfirmed"
    assert payload["risk_block"]["severity"] == "critical"
    assert payload["disabled_bot_config_ids"] == [2, 3]
    assert payload["stopped_bot_run_ids"] == [8]
    assert db.commits == 1
    assert db.rollbacks == 0
