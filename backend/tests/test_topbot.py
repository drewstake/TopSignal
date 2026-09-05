import os

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main
from app.bot_schemas import TopBotStartIn
from app.db import Base
from app.models import Account, BotConfig, BotRun
from app.services.bot_service import _is_contract_allowed
from app.services.topbot import prepare_topbot, resolve_topbot_contract

USER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Account.__table__, BotConfig.__table__, BotRun.__table__])
    with Session(engine) as session:
        for account_id in (101, 102):
            session.add(Account(user_id=USER, provider="projectx", external_id=str(account_id),
                                name=f"Practice {account_id}", can_trade=True, is_visible=True))
        session.commit()
        yield session
    engine.dispose()


def test_preset_is_mnq_only_and_reused_with_code_defaults(db):
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    original_id = config.id
    assert config.enabled is False
    assert config.execution_mode == "dry_run"
    assert config.strategy_type == "topbot_adaptive"
    assert config.strategy_params["revision"] == "mnq_ema_vwap_pullback_v5_bracket_exits"
    assert config.strategy_params["exit_policy"] == "bracket_only"
    assert config.strategy_params["directional_bias"] == "long"
    assert config.strategy_params["short_trend_ema_period"] == 50
    assert config.strategy_params["stop_points"] == config.strategy_params["target_points"] == 50
    assert "source_strategies" not in config.strategy_params
    assert _is_contract_allowed(config, contract_id="CON.F.US.MNQ.U26", symbol="F.US.MNQ")
    assert _is_contract_allowed(config, contract_id="CON.F.US.MNQ.Z26", symbol="F.US.MNQ")
    assert not _is_contract_allowed(config, contract_id="CON.F.US.NQ.U26", symbol="F.US.NQ")
    config.contract_id = "CON.F.US.ES.U26"
    config.order_size = 8
    config.strategy_params = {"minimum_score": 10}
    db.commit()

    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=False, contract_id="CON.F.US.MNQ.Z26")
    assert config.id == original_id
    assert config.contract_id == "CON.F.US.MNQ.Z26"
    assert config.order_size == config.max_contracts == 1
    assert config.strategy_params["ema_period"] == 20
    assert "minimum_score" not in config.strategy_params
    assert config.execution_mode == "live"
    assert config.enabled is False
    assert db.query(BotConfig).count() == 1


def test_account_and_user_isolation(db):
    first = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    second = prepare_topbot(db, user_id=USER, account_id=102, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    assert first.id != second.id
    assert first.name != second.name
    with pytest.raises(LookupError):
        prepare_topbot(db, user_id="another-user", account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    assert first.execution_mode == "dry_run"


@pytest.mark.parametrize("running_row", [False, True])
def test_never_overwrites_an_active_bot_or_changes_its_mode(db, running_row):
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    if running_row:
        db.add(BotRun(user_id=USER, bot_config_id=config.id, account_id=101, status="running", dry_run=True))
    else:
        config.enabled = True
    db.commit()
    with pytest.raises(ValueError, match="Stop automation"):
        prepare_topbot(db, user_id=USER, account_id=101, dry_run=False, contract_id="CON.F.US.MNQ.Z26")
    assert config.execution_mode == "dry_run"


def test_start_schema_requires_explicit_live_confirmation_and_forbids_tuning():
    assert TopBotStartIn().dry_run is True
    for values in ({"dry_run": False}, {"dry_run": "false"}, {"order_size": 5}, {"contract_id": "ES"}):
        with pytest.raises(ValidationError):
            TopBotStartIn.model_validate(values)
    assert TopBotStartIn(dry_run=False, confirm_live_order_routing=True).dry_run is False


def test_account_start_reuses_existing_continuous_execution_checks(monkeypatch):
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: USER)
    calls = []
    monkeypatch.setattr(main, "_require_owned_projectx_account", lambda *args, **kwargs: type("Account", (), {"trade_data_source": "projectx"})())
    monkeypatch.setattr(main, "_projectx_client_for_user", lambda *args, **kwargs: object())
    monkeypatch.setattr(main, "resolve_topbot_contract", lambda client: "CON.F.US.MNQ.U26")
    monkeypatch.setattr(main, "_validate_bot_start_admission", lambda db, body: calls.append("admission"))

    def prepare(db, **kwargs):
        calls.append(("prepare", kwargs))
        return type("Config", (), {"id": 42})()

    def start(config_id, body, db):
        calls.append(("start", config_id, body))
        return {"status": "held"}

    monkeypatch.setattr(main, "prepare_topbot", prepare)
    monkeypatch.setattr(main, "start_trading_bot", start)
    assert main.start_account_topbot(101, TopBotStartIn(), db=type("Db", (), {"commit": lambda self: None})()) == {"status": "held"}
    assert calls[0] == "admission"
    assert calls[1][1] == {"user_id": USER, "account_id": 101, "dry_run": True, "contract_id": "CON.F.US.MNQ.U26"}
    assert calls[2][1] == 42
    assert calls[2][2].continuous is True
    assert calls[2][2].confirm_live_order_routing is False


def test_unavailable_worker_does_not_prepare_a_config(monkeypatch):
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: USER)
    monkeypatch.setattr(main, "_bot_worker_runtime", None)
    monkeypatch.setattr(main, "prepare_topbot", lambda *args, **kwargs: pytest.fail("must not prepare"))
    with pytest.raises(HTTPException) as error:
        main.start_account_topbot(101, TopBotStartIn(), db=object())
    assert error.value.status_code == 503


def test_contract_resolution_ignores_other_instruments_and_expired_deliveries():
    class Client:
        def search_contracts(self, **kwargs):
            assert kwargs == {"search_text": "F.US.MNQ", "live": False}
            return [
                {"id": "CON.F.US.NQ.U26", "active_contract": True},
                {"id": "CON.F.US.MNQ.M26", "active_contract": False},
                {"id": "CON.F.US.MNQ.U26", "active_contract": True},
            ]
    assert resolve_topbot_contract(Client()) == "CON.F.US.MNQ.U26"


def test_contract_resolution_fails_closed_without_an_active_mnq():
    class Client:
        def search_contracts(self, **kwargs):
            return [{"id": "CON.F.US.MNQ.M26", "active_contract": False}]
    with pytest.raises(ValueError, match="No active MNQ"):
        resolve_topbot_contract(Client())
