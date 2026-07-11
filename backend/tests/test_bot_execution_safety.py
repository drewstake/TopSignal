import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import Account, BotConfig, BotDecision, BotOrderAttempt, BotRun, InstrumentMetadata, ProjectXMarketCandle
from app.services import bot_service
from app.services.bot_execution_safety import (
    InvalidBotRunTransition,
    build_action_idempotency_key,
    touch_bot_run,
    transition_bot_run,
)
from app.services.bot_service import BotRunEvaluationError, SignalResult, evaluate_bot_config, start_bot_run
from app.services.projectx_client import ProjectXClientError


USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"
CONTRACT_ID = "CON.F.US.MNQ.M26"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


class RecordingClient:
    def __init__(
        self,
        *,
        order_error: Exception | None = None,
        account_can_trade: bool = True,
        positions: list[dict] | None = None,
        open_orders: list[dict] | None = None,
        trades: list[dict] | None = None,
        orders: list[dict] | None = None,
    ):
        self.order_error = order_error
        self.account_can_trade = account_can_trade
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.trades = trades or []
        self.orders = orders or []
        self.place_order_calls: list[dict] = []

    def list_accounts(self, *, only_active_accounts=True):
        del only_active_accounts
        return [
            {
                "id": 9001,
                "name": "Practice 9001",
                "status": "ACTIVE" if self.account_can_trade else "LOCKED_OUT",
                "can_trade": self.account_can_trade,
            }
        ]

    def search_open_positions(self, *, account_id):
        del account_id
        return list(self.positions)

    def search_open_orders(self, *, account_id):
        del account_id
        return list(self.open_orders)

    def fetch_trade_history(self, **_kwargs):
        return list(self.trades)

    def search_orders(self, **_kwargs):
        return list(self.orders)

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        if self.order_error is not None:
            raise self.order_error
        return {"order_id": "provider-order-1", "raw_payload": {"accepted": True}}


def _add_account_and_config(
    db: Session,
    *,
    user_id: str = USER_A,
    account_id: int = 9001,
    enabled: bool = True,
    execution_mode: str = "dry_run",
    name: str = "Safety Bot",
) -> tuple[Account, BotConfig]:
    account = Account(
        user_id=user_id,
        provider="projectx",
        external_id=str(account_id),
        name=f"Practice {account_id}",
        account_state="ACTIVE",
        can_trade=True,
        is_visible=True,
    )
    config = BotConfig(
        user_id=user_id,
        account_id=account_id,
        name=name,
        enabled=enabled,
        execution_mode=execution_mode,
        strategy_type="sma_cross",
        strategy_params={},
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=2,
        slow_period=3,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=10,
        max_open_position=1,
        allowed_contracts=[CONTRACT_ID],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3600,
    )
    db.add_all([account, config])
    db.flush()
    return account, config


def _patch_actionable_signal(monkeypatch, *, candle_timestamp: datetime | None = None, action: str = "BUY") -> datetime:
    timestamp = candle_timestamp or (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0)

    def fake_fetch(_db, *, user_id, config, client):
        del client
        candle = ProjectXMarketCandle(
            user_id=user_id,
            contract_id=str(config.contract_id),
            symbol=config.symbol,
            live=False,
            unit=str(config.timeframe_unit),
            unit_number=int(config.timeframe_unit_number),
            candle_timestamp=timestamp,
            open_price=100,
            high_price=102,
            low_price=99,
            close_price=101,
            volume=100,
            is_partial=False,
        )
        return [candle], SignalResult(
            action=action,
            reason="characterized actionable signal",
            candle_timestamp=timestamp,
            price=101.0,
            raw_payload={"strategy_type": str(config.strategy_type)},
        )

    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", fake_fetch)
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(bot_service, "build_signal_trade_evaluation", lambda **_kwargs: None)
    return timestamp


def _risk_codes(result) -> set[str]:
    return {event.code for event in result.risk_events}


def test_repeated_actionable_candle_creates_one_attempt_and_duplicate_skip_audit(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    client = RecordingClient()

    first = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=True,
    )
    db_session.commit()
    second = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=True,
    )
    db_session.commit()

    assert first.status == "dry_run_attempt"
    assert first.order_attempt is not None
    assert second.status == "duplicate_skipped"
    assert second.order_attempt is None
    assert second.idempotency_key == first.idempotency_key
    assert second.duplicate_of_order_attempt_id == first.order_attempt.id
    assert second.decision.decision_type == "duplicate_skip"
    serialized = bot_service.serialize_evaluation(second)
    assert serialized["status"] == "duplicate_skipped"
    assert serialized["correlation_id"] == second.correlation_id
    assert serialized["idempotency_key"] == first.idempotency_key
    assert serialized["duplicate_of_order_attempt_id"] == first.order_attempt.id
    assert db_session.query(BotOrderAttempt).count() == 1
    assert [row.decision_type for row in db_session.query(BotDecision).order_by(BotDecision.id).all()] == [
        "signal",
        "duplicate_skip",
    ]
    assert client.place_order_calls == []


def test_uniqueness_toctou_race_uses_savepoint_and_keeps_session_usable(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session)
    timestamp = _patch_actionable_signal(monkeypatch)
    idempotency_key = build_action_idempotency_key(
        user_id=USER_A,
        bot_config_id=int(config.id),
        candle_timestamp=timestamp,
        action="BUY",
        execution_mode="dry_run",
    )
    winner_decision = BotDecision(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        decision_type="signal",
        action="BUY",
        reason="winning request",
        candle_timestamp=timestamp,
        price=101,
        quantity=1,
        correlation_id="winner-correlation",
        idempotency_key=idempotency_key,
    )
    db_session.add(winner_decision)
    db_session.flush()
    winner_attempt = BotOrderAttempt(
        user_id=USER_A,
        bot_config_id=int(config.id),
        bot_decision_id=int(winner_decision.id),
        account_id=int(config.account_id),
        contract_id=CONTRACT_ID,
        execution_mode="dry_run",
        correlation_id="winner-correlation",
        idempotency_key=idempotency_key,
        side="BUY",
        order_type="market",
        size=1,
        status="dry_run",
    )
    db_session.add(winner_attempt)
    db_session.commit()

    original_lookup = bot_service._find_order_attempt_by_idempotency_key
    lookup_count = 0

    def miss_before_claim_then_find_winner(*args, **kwargs):
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            return None
        return original_lookup(*args, **kwargs)

    monkeypatch.setattr(bot_service, "_find_order_attempt_by_idempotency_key", miss_before_claim_then_find_winner)

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=RecordingClient(),
        dry_run=True,
    )

    assert result.status == "duplicate_skipped"
    assert result.duplicate_of_order_attempt_id == winner_attempt.id
    assert result.decision.decision_type == "duplicate_skip"
    assert lookup_count == 2
    assert db_session.execute(text("select 1")).scalar_one() == 1
    db_session.commit()
    assert db_session.query(BotOrderAttempt).count() == 1
    assert db_session.query(BotDecision).filter(BotDecision.decision_type == "duplicate_skip").count() == 1


@pytest.mark.parametrize("target_status", ["stopped", "blocked", "error"])
def test_running_run_allows_only_terminal_transitions(target_status):
    run = SimpleNamespace(
        status="running",
        stopped_at=None,
        stop_reason=None,
        last_heartbeat_at=None,
        last_error=None,
        last_evaluated_at=None,
        raw_state=None,
    )

    transition_bot_run(run, target_status, reason=f"test_{target_status}", error="provider failed")

    assert run.status == target_status
    assert run.stopped_at is not None
    assert run.last_heartbeat_at == run.stopped_at
    assert run.stop_reason == f"test_{target_status}"
    if target_status == "error":
        assert run.last_error == "provider failed"


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        ("stopped", "running"),
        ("stopped", "blocked"),
        ("blocked", "stopped"),
        ("error", "running"),
        ("running", "paused"),
    ],
)
def test_invalid_run_transitions_are_rejected(current_status, target_status):
    run = SimpleNamespace(status=current_status)

    with pytest.raises(InvalidBotRunTransition, match="Invalid bot run transition"):
        transition_bot_run(run, target_status)


def test_running_run_can_heartbeat_but_terminal_run_cannot():
    candle_timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)
    run = SimpleNamespace(
        status="running",
        last_heartbeat_at=None,
        last_evaluated_at=None,
        raw_state=None,
        stopped_at=None,
        stop_reason=None,
        last_error=None,
    )

    touch_bot_run(run, candle_timestamp=candle_timestamp)

    assert run.last_heartbeat_at == run.last_evaluated_at
    assert run.raw_state["last_closed_candle_at"] == candle_timestamp.isoformat()
    transition_bot_run(run, "stopped", reason="done")
    with pytest.raises(InvalidBotRunTransition, match="terminal state stopped"):
        touch_bot_run(run)


def test_database_uniqueness_allows_only_one_running_run_per_bot(db_session):
    _, config = _add_account_and_config(db_session)
    first = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        status="running",
        dry_run=True,
    )
    db_session.add(first)
    db_session.commit()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                BotRun(
                    user_id=USER_A,
                    bot_config_id=int(config.id),
                    account_id=int(config.account_id),
                    status="running",
                    dry_run=True,
                )
            )
            db_session.flush()

    assert db_session.execute(text("select 1")).scalar_one() == 1
    assert db_session.query(BotRun).filter(BotRun.status == "running").count() == 1


def test_database_constraint_rejects_fractional_bot_contract_quantities(db_session):
    _, config = _add_account_and_config(db_session)
    db_session.commit()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            config.order_size = 1.5
            db_session.flush()


def test_provider_candle_failure_on_start_persists_error_run_and_no_running_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False)
    db_session.commit()

    def fail_fetch(*_args, **_kwargs):
        raise RuntimeError("provider candle timeout")

    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", fail_fetch)

    with pytest.raises(BotRunEvaluationError, match="provider candle timeout") as exc_info:
        start_bot_run(
            db_session,
            user_id=USER_A,
            bot_config_id=int(config.id),
            client=RecordingClient(),
            dry_run=True,
        )
    assert isinstance(exc_info.value.cause, RuntimeError)
    db_session.commit()
    db_session.expire_all()

    runs = db_session.query(BotRun).all()
    assert len(runs) == 1
    assert runs[0].status == "error"
    assert runs[0].stop_reason == "evaluation_failed"
    assert "provider candle timeout" in str(runs[0].last_error)
    assert runs[0].raw_state["phase"] == "error"
    assert db_session.query(BotRun).filter(BotRun.status == "running").count() == 0
    assert db_session.query(BotOrderAttempt).count() == 0
    assert db_session.get(BotConfig, config.id).enabled is False


def test_provider_order_failure_preserves_error_attempt_and_terminal_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(order_error=RuntimeError("provider order unavailable"))

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )
    db_session.commit()
    db_session.expire_all()

    attempt = db_session.query(BotOrderAttempt).one()
    run = db_session.query(BotRun).one()
    assert result.status == "error"
    assert attempt.status == "error"
    assert attempt.execution_mode == "live"
    assert attempt.idempotency_key == result.idempotency_key
    assert "provider order unavailable" in str(attempt.rejection_reason)
    assert run.status == "error"
    assert run.stop_reason == "provider_order_submission_failed"
    assert "provider order unavailable" in str(run.last_error)
    assert run.stopped_at is not None
    assert db_session.query(BotRun).filter(BotRun.status == "running").count() == 0
    assert db_session.get(BotConfig, config.id).enabled is False
    assert len(client.place_order_calls) == 1


def test_live_risk_uses_authoritative_provider_position_instead_of_signal_default(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        positions=[
            {
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "signed_size": 1.0,
            }
        ]
    )

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert {"max_contracts", "max_open_position"} <= _risk_codes(result)
    assert client.place_order_calls == []


def test_live_risk_fails_closed_when_provider_preflight_is_unavailable(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    class UnavailableClient(RecordingClient):
        def search_open_positions(self, *, account_id):
            del account_id
            raise ProjectXClientError("position endpoint unavailable")

    client = UnavailableClient()
    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert "live_preflight_unavailable" in _risk_codes(result)
    assert client.place_order_calls == []


def test_flat_account_blocks_opposite_side_signal_while_order_is_working(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch, action="SELL")
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        open_orders=[
            {
                "order_id": "working-buy",
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "status": 1,
                "signed_size": 1.0,
            }
        ]
    )

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert "working_order_direction_conflict" in _risk_codes(result)
    assert client.place_order_calls == []


def test_live_risk_blocks_while_provider_order_is_working(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        open_orders=[
            {
                "order_id": "provider-working",
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "status": 1,
                "signed_size": 1.0,
            }
        ]
    )

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert "working_order_direction_conflict" in _risk_codes(result)
    assert client.place_order_calls == []


def test_reducing_exit_fails_closed_when_same_side_working_order_could_reverse_position(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch, action="SELL")
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        positions=[
            {
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "signed_size": 1.0,
            }
        ],
        open_orders=[
            {
                "order_id": "protective-sell",
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "status": 1,
                "signed_size": -1.0,
            }
        ],
    )

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert "working_order_direction_conflict" in _risk_codes(result)
    assert client.place_order_calls == []


def test_recent_submission_blocks_sibling_bot_during_provider_settlement(db_session, monkeypatch):
    _, first_config = _add_account_and_config(db_session, execution_mode="live", name="First live bot")
    second_config = BotConfig(
        user_id=USER_A,
        account_id=9001,
        name="Second live bot",
        enabled=True,
        execution_mode="live",
        strategy_type="sma_cross",
        strategy_params={},
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=2,
        slow_period=3,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=10,
        max_open_position=1,
        allowed_contracts=[CONTRACT_ID],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3600,
    )
    db_session.add(second_config)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient()

    first = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=first_config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )
    db_session.commit()
    second = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=second_config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert first.status == "submitted"
    assert second.status == "risk_blocked"
    assert "recent_live_submission_settling" in _risk_codes(second)
    assert len(client.place_order_calls) == 1


def test_live_risk_uses_authoritative_provider_daily_pnl(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        trades=[
            {
                "account_id": 9001,
                "timestamp": datetime.now(timezone.utc),
                "pnl": -300.0,
                "fees": 0.0,
                "voided": False,
                "raw_payload": {"voided": False},
            }
        ]
    )

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert "max_daily_loss" in _risk_codes(result)
    assert client.place_order_calls == []


def test_reconcile_unresolved_attempt_uses_deterministic_custom_tag(db_session):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    decision = BotDecision(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        contract_id=CONTRACT_ID,
        decision_type="signal",
        action="BUY",
        reason="test",
        candle_timestamp=datetime.now(timezone.utc),
        quantity=1,
    )
    db_session.add(decision)
    db_session.flush()
    attempt = BotOrderAttempt(
        user_id=USER_A,
        bot_config_id=int(config.id),
        bot_decision_id=int(decision.id),
        account_id=9001,
        contract_id=CONTRACT_ID,
        execution_mode="live",
        side="BUY",
        order_type="market",
        size=1,
        status="pending",
        raw_request={"customTag": "topsignal-1-reconcile"},
    )
    db_session.add(attempt)
    db_session.commit()
    client = RecordingClient(
        orders=[
            {
                "order_id": "provider-456",
                "account_id": 9001,
                "status": 2,
                "custom_tag": "topsignal-1-reconcile",
                "raw_payload": {"id": 456, "customTag": "topsignal-1-reconcile"},
            }
        ]
    )

    unresolved_count, error = bot_service.reconcile_unresolved_order_attempts(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client=client,
    )

    assert error is None
    assert unresolved_count == 0
    assert attempt.status == "submitted"
    assert attempt.provider_order_id == "provider-456"
    assert attempt.raw_response["reconciled"] is True


@pytest.mark.parametrize("provider_status", [1, 6])
def test_reconciliation_keeps_open_or_pending_provider_order_unresolved(db_session, provider_status):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    attempt = BotOrderAttempt(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        contract_id=CONTRACT_ID,
        execution_mode="live",
        side="BUY",
        order_type="market",
        size=1,
        status="submission_unknown",
        raw_request={"customTag": "topsignal-working-order"},
    )
    db_session.add(attempt)
    db_session.commit()
    client = RecordingClient(
        orders=[
            {
                "order_id": "provider-working",
                "account_id": 9001,
                "status": provider_status,
                "custom_tag": "topsignal-working-order",
                "raw_payload": {"id": "provider-working", "status": provider_status},
            }
        ]
    )

    unresolved_count, error = bot_service.reconcile_unresolved_order_attempts(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client=client,
    )

    assert error is None
    assert unresolved_count == 1
    assert attempt.status == "submission_unknown"
    assert attempt.provider_order_id == "provider-working"
    assert "remains blocked" in attempt.rejection_reason


def test_strategy_brackets_include_required_projectx_order_types(db_session):
    db_session.add(InstrumentMetadata(symbol="MNQ", tick_size=0.25, tick_value=0.5))
    db_session.flush()

    payload = bot_service._strategy_bracket_payloads(
        db_session,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        action="BUY",
        entry_price=100,
        decision_payload={"stop_loss": 99, "take_profit": 102},
    )

    assert payload == {
        "stopLossBracket": {"ticks": 4, "type": 4},
        "takeProfitBracket": {"ticks": 8, "type": 1},
    }


def test_network_timeout_marks_submission_unknown_for_reconciliation():
    attempt = SimpleNamespace(
        raw_request={
            "accountId": 9001,
            "contractId": CONTRACT_ID,
            "type": 2,
            "side": 0,
            "size": 1,
            "customTag": "topsignal-timeout",
        },
        status="pending",
        provider_order_id=None,
        rejection_reason=None,
        raw_response=None,
    )
    client = RecordingClient(
        order_error=ProjectXClientError(
            "request timed out",
            status_code=504,
            submission_outcome_unknown=True,
        )
    )

    bot_service._submit_order_attempt(client=client, order_attempt=attempt)

    assert attempt.status == "submission_unknown"
    assert "requires reconciliation" in attempt.rejection_reason
    assert attempt.provider_order_id is None


def test_success_response_without_provider_order_id_is_not_marked_submitted():
    attempt = SimpleNamespace(
        raw_request={
            "accountId": 9001,
            "contractId": CONTRACT_ID,
            "type": 2,
            "side": 0,
            "size": 1,
            "customTag": "topsignal-missing-id",
        },
        status="pending",
        provider_order_id=None,
        rejection_reason=None,
        raw_response=None,
    )

    class MissingIdClient:
        def place_order(self, **_kwargs):
            return {"order_id": None, "raw_payload": {"success": True}}

    bot_service._submit_order_attempt(client=MissingIdClient(), order_attempt=attempt)

    assert attempt.status == "submission_unknown"
    assert "without a provider order ID" in attempt.rejection_reason


def test_higher_timeframe_staleness_is_measured_from_bar_close(db_session):
    account, config = _add_account_and_config(db_session)
    config.max_data_staleness_seconds = 60
    candle = ProjectXMarketCandle(
        user_id=USER_A,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        unit="hour",
        unit_number=4,
        candle_timestamp=datetime.now(timezone.utc) - timedelta(hours=4, seconds=30),
        open_price=100,
        high_price=102,
        low_price=99,
        close_price=101,
        volume=100,
        is_partial=False,
    )

    blocks = bot_service.evaluate_risk_gates(
        db_session,
        user_id=USER_A,
        config=config,
        account=account,
        latest_candle=candle,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        action="BUY",
        requested_order_size=1,
        dry_run=True,
        confirm_live_order_routing=False,
    )

    assert "stale_market_data" not in {block.code for block in blocks}


def test_risk_blocked_signal_creates_no_order_attempt(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=RecordingClient(),
        dry_run=True,
    )
    db_session.commit()

    assert result.status == "risk_blocked"
    assert "bot_disabled" in _risk_codes(result)
    assert result.order_attempt is None
    assert db_session.query(BotOrderAttempt).count() == 0


def test_dry_run_is_default_even_for_live_config_and_never_submits(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    client = RecordingClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=None,
        confirm_live_order_routing=True,
    )
    db_session.commit()

    assert result.status == "dry_run_attempt"
    assert result.order_attempt is not None
    assert result.order_attempt.execution_mode == "dry_run"
    assert result.order_attempt.status == "dry_run"
    assert client.place_order_calls == []


def test_dry_config_cannot_be_overridden_to_live(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="dry_run")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"live_execution_not_configured"}
    assert db_session.query(BotOrderAttempt).count() == 0
    assert client.place_order_calls == []


def test_live_execution_requires_explicit_confirmation(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=False,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"live_order_confirmation_missing"}
    assert db_session.query(BotOrderAttempt).count() == 0
    assert client.place_order_calls == []


def test_live_execution_is_blocked_under_test_runtime(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: True)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"live_execution_disabled_in_tests"}
    assert db_session.query(BotOrderAttempt).count() == 0
    assert client.place_order_calls == []


def test_live_funded_account_restriction_is_preserved(db_session, monkeypatch):
    account, config = _add_account_and_config(db_session, execution_mode="live")
    account.name = "Live Funded 9001"
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"live_funded_api_blocked"}
    assert db_session.query(BotOrderAttempt).count() == 0
    assert client.place_order_calls == []


def test_bot_evaluation_and_idempotency_are_user_scoped(db_session, monkeypatch):
    _, config_a = _add_account_and_config(
        db_session,
        user_id=USER_A,
        account_id=9001,
        name="User A Bot",
    )
    _, config_b = _add_account_and_config(
        db_session,
        user_id=USER_B,
        account_id=9001,
        name="User B Bot",
    )
    db_session.commit()
    _patch_actionable_signal(monkeypatch)

    result_a = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config_a,
        account=None,
        client=RecordingClient(),
        dry_run=True,
    )
    db_session.commit()

    assert (
        bot_service._find_order_attempt_by_idempotency_key(
            db_session,
            user_id=USER_B,
            bot_config_id=int(config_a.id),
            idempotency_key=result_a.idempotency_key,
        )
        is None
    )
    with pytest.raises(LookupError, match="bot_config_not_found"):
        evaluate_bot_config(
            db_session,
            user_id=USER_B,
            config=config_a,
            account=None,
            client=RecordingClient(),
            dry_run=True,
        )

    result_b = evaluate_bot_config(
        db_session,
        user_id=USER_B,
        config=config_b,
        account=None,
        client=RecordingClient(),
        dry_run=True,
    )
    db_session.commit()

    assert result_a.status == result_b.status == "dry_run_attempt"
    assert result_a.idempotency_key != result_b.idempotency_key
    assert db_session.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == USER_A).count() == 1
    assert db_session.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == USER_B).count() == 1
