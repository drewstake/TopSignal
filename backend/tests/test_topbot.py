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
    assert config.trading_start_time == "09:35"
    assert config.trading_end_time == "15:30"
    from app.services.topbot_strategy import DEFAULTS
    assert config.strategy_params == DEFAULTS
    assert "ema_period" not in config.strategy_params
    assert "source_strategies" not in config.strategy_params
    assert _is_contract_allowed(config, contract_id="CON.F.US.MNQ.U26", symbol="F.US.MNQ")
    assert _is_contract_allowed(config, contract_id="CON.F.US.MNQ.Z26", symbol="F.US.MNQ")
    assert not _is_contract_allowed(config, contract_id="CON.F.US.NQ.U26", symbol="F.US.NQ")
    config.contract_id = "CON.F.US.ES.U26"
    config.order_size = 8
    config.strategy_params = {"minimum_score": 10}
    db.commit()

    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.Z26")
    assert config.id == original_id
    assert config.contract_id == "CON.F.US.MNQ.Z26"
    assert config.order_size == config.max_contracts == 1
    assert config.strategy_params == DEFAULTS
    assert "minimum_score" not in config.strategy_params
    assert config.execution_mode == "dry_run"
    assert config.enabled is False
    assert db.query(BotConfig).count() == 1


def test_restart_preference_survives_new_run_preparation(db):
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    config.strategy_params = {**config.strategy_params, "practice_auto_resume": True}
    db.commit()
    updated = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.Z26")
    assert updated.strategy_params["practice_auto_resume"] is True
    other = prepare_topbot(db, user_id=USER, account_id=102, dry_run=True, contract_id="CON.F.US.MNQ.Z26")
    assert other.strategy_params["practice_auto_resume"] is False


def test_account_and_user_isolation(db):
    first = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    second = prepare_topbot(db, user_id=USER, account_id=102, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    assert first.id != second.id
    assert first.name != second.name
    with pytest.raises(LookupError):
        prepare_topbot(db, user_id="another-user", account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    assert first.execution_mode == "dry_run"


@pytest.mark.parametrize("dry_run", [True, False])
def test_saved_bot_cannot_restart_even_with_old_strategy_params(db, dry_run):
    from app.services.bot_service import start_bot_run
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    config.strategy_params = {"revision": "mnq_bayesian_payoff_v1"}
    db.commit()
    with pytest.raises(ValueError, match="no strategy configured"):
        start_bot_run(db, user_id=USER, bot_config_id=config.id, client=object(), dry_run=dry_run,
                      continuous=True, confirm_live_order_routing=not dry_run)
    assert config.enabled is False
    assert db.query(BotRun).count() == 0


def test_blank_strategy_rejects_entries_but_preserves_protective_exit_path():
    from app.services.bot_service import SignalResult, _strategy_protection_block
    config = BotConfig(strategy_type="topbot_adaptive", strategy_params={"revision": "mnq_bayesian_payoff_v1"})
    for action in ("BUY", "SELL"):
        signal = SignalResult(action, "old model", None, 25000, {"live_routing_allowed": True})
        block = _strategy_protection_block(None, config=config, signal=signal, contract_id="CON.F.US.MNQ.U26", symbol="MNQ")
        assert block.code == "strategy_not_configured"
    exit_signal = SignalResult("SELL", "protective exit", None, 25000, {"signal_category": "exit"})
    assert _strategy_protection_block(None, config=config, signal=exit_signal, contract_id="CON.F.US.MNQ.U26", symbol="MNQ") is None


@pytest.mark.parametrize("running_row", [False, True])
def test_never_overwrites_an_active_bot_or_changes_its_mode(db, running_row):
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.U26")
    if running_row:
        db.add(BotRun(user_id=USER, bot_config_id=config.id, account_id=101, status="running", dry_run=True))
    else:
        config.enabled = True
    db.commit()
    with pytest.raises(ValueError, match="Stop automation"):
        prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id="CON.F.US.MNQ.Z26")
    assert config.execution_mode == "dry_run"


def test_start_schema_requires_explicit_live_confirmation_and_forbids_tuning():
    assert TopBotStartIn().dry_run is True
    for values in ({"dry_run": False}, {"dry_run": "false"}, {"order_size": 5}, {"contract_id": "ES"}):
        with pytest.raises(ValidationError):
            TopBotStartIn.model_validate(values)
    assert TopBotStartIn(dry_run=False, confirm_live_order_routing=True).dry_run is False


@pytest.mark.parametrize("dry_run", [True, False])
def test_account_start_rejects_blank_strategy_before_provider_or_config_changes(monkeypatch, dry_run):
    from app.services import topbot_strategy
    monkeypatch.setattr(topbot_strategy, "CONFIGURED", False)
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: USER)
    monkeypatch.setattr(main, "_require_owned_projectx_account", lambda *a, **k: type("Account", (), {"trade_data_source": "projectx"})())
    monkeypatch.setattr(main, "_validate_bot_start_admission", lambda *a: None)
    monkeypatch.setattr(main, "_projectx_client_for_user", lambda *a, **k: pytest.fail("no provider access"))
    monkeypatch.setattr(main, "prepare_topbot", lambda *a, **k: pytest.fail("no config writes"))
    with pytest.raises(HTTPException) as error:
        main.start_account_topbot(101, TopBotStartIn(dry_run=dry_run, confirm_live_order_routing=not dry_run),
                                 db=type("Db", (), {"rollback": lambda self: None})())
    assert error.value.status_code == 409
    assert "no strategy configured" in error.value.detail


def test_unavailable_worker_does_not_prepare_a_config(monkeypatch):
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: USER)
    monkeypatch.setattr(main, "_bot_worker_runtime", None)
    monkeypatch.setattr(main, "prepare_topbot", lambda *args, **kwargs: pytest.fail("must not prepare"))
    with pytest.raises(HTTPException) as error:
        main.start_account_topbot(101, TopBotStartIn(), db=object())
    assert error.value.status_code == 503


def test_live_preset_route_requires_worker_before_provider_or_prepare_config(monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ENABLED", "false")
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: USER)
    monkeypatch.setattr(main, "_validate_bot_start_admission", lambda *a: None)
    monkeypatch.setattr(main, "_require_owned_projectx_account", lambda *a, **k: type("Account", (), {"trade_data_source": "projectx"})())
    monkeypatch.setattr(main, "_projectx_client_for_user", lambda *a, **k: pytest.fail("no provider client"))
    monkeypatch.setattr(main, "prepare_topbot", lambda *a, **k: pytest.fail("no config changes"))
    with pytest.raises(HTTPException) as error:
        main.start_account_topbot(101, TopBotStartIn(dry_run=False, confirm_live_order_routing=True),
                                 db=type("Db", (), {"rollback": lambda self: None})())
    assert error.value.status_code == 409
    assert "requires the live worker" in error.value.detail


def test_contract_resolution_ignores_other_instruments_and_expired_deliveries():
    class Client:
        def search_contracts(self, **kwargs):
            assert kwargs == {"search_text": "MNQ", "live": False}
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
