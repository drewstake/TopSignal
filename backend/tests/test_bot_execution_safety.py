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
from app.bot_schemas import BotConfigUpdateIn
from app.models import (
    Account,
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRun,
    InstrumentMetadata,
    ProjectXMarketCandle,
    ProjectXTradeEvent,
)
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
    trade_data_source: str = "projectx",
) -> tuple[Account, BotConfig]:
    account = Account(
        user_id=user_id,
        provider="projectx",
        external_id=str(account_id),
        name=f"Practice {account_id}",
        trade_data_source=trade_data_source,
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


def _patch_actionable_signal(
    monkeypatch,
    *,
    candle_timestamp: datetime | None = None,
    action: str = "BUY",
    raw_payload: dict | None = None,
) -> datetime:
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
            raw_payload={"strategy_type": str(config.strategy_type), **(raw_payload or {})},
        )

    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", fake_fetch)
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(bot_service, "build_signal_trade_evaluation", lambda **_kwargs: None)
    return timestamp


def _risk_codes(result) -> set[str]:
    return {event.code for event in result.risk_events}


def test_csv_import_account_is_rejected_before_any_projectx_bot_call(db_session):
    _, config = _add_account_and_config(
        db_session,
        trade_data_source="csv_import",
    )
    db_session.commit()

    class NoProviderCalls:
        def __getattr__(self, name):
            raise AssertionError(f"ProjectX method {name} must not be called")

    with pytest.raises(
        ValueError,
        match="csv_import_accounts_cannot_run_bots",
    ):
        evaluate_bot_config(
            db_session,
            user_id=USER_A,
            config=config,
            account=None,
            client=NoProviderCalls(),
            dry_run=False,
            confirm_live_order_routing=True,
        )


def test_enabled_bot_cannot_be_retargeted_to_another_account(db_session):
    _, config = _add_account_and_config(db_session, enabled=True)
    db_session.add(
        Account(
            user_id=USER_A,
            provider="projectx",
            external_id="9002",
            name="Practice 9002",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
    )
    db_session.commit()

    payload = BotConfigUpdateIn(account_id=9002)
    assert "enabled" not in payload.model_fields_set
    with pytest.raises(ValueError, match="bot_account_change_requires_disabled_stopped_bot"):
        bot_service.update_bot_config(
            db_session,
            user_id=USER_A,
            bot_config_id=int(config.id),
            payload=payload,
        )

    assert int(config.account_id) == 9001


def test_running_disabled_bot_cannot_be_retargeted_to_another_account(db_session):
    _, config = _add_account_and_config(db_session, enabled=False)
    db_session.add_all(
        [
            Account(
                user_id=USER_A,
                provider="projectx",
                external_id="9002",
                name="Practice 9002",
                account_state="ACTIVE",
                can_trade=True,
                is_visible=True,
            ),
            BotRun(
                user_id=USER_A,
                bot_config_id=int(config.id),
                account_id=9001,
                status="running",
                dry_run=True,
                started_at=datetime.now(timezone.utc),
                last_heartbeat_at=datetime.now(timezone.utc),
            ),
        ]
    )
    db_session.commit()

    payload = BotConfigUpdateIn(account_id=9002)
    assert "enabled" not in payload.model_fields_set
    with pytest.raises(ValueError, match="bot_account_change_requires_disabled_stopped_bot"):
        bot_service.update_bot_config(
            db_session,
            user_id=USER_A,
            bot_config_id=int(config.id),
            payload=payload,
        )

    assert int(config.account_id) == 9001


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


def test_evaluation_releases_initial_locks_before_candle_provider_io(db_session):
    _, config = _add_account_and_config(db_session)
    db_session.commit()
    provider_checks: list[str] = []
    last_closed = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=5)

    class CandleClient(RecordingClient):
        def search_contracts(self, *, search_text, live):
            del search_text, live
            assert db_session.in_transaction() is False
            provider_checks.append("contract")
            return [
                {
                    "id": CONTRACT_ID,
                    "symbol_id": "F.US.MNQ",
                    "tick_size": 0.25,
                    "tick_value": 0.5,
                    "active_contract": True,
                }
            ]

        def retrieve_bars(self, **_kwargs):
            assert db_session.in_transaction() is False
            provider_checks.append("candles")
            return [
                {
                    "timestamp": last_closed - timedelta(minutes=5 * (24 - index)),
                    "open": 100 + index,
                    "high": 101 + index,
                    "low": 99 + index,
                    "close": 100 + index,
                    "volume": 100,
                    "is_partial": False,
                }
                for index in range(25)
            ]

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=CandleClient(),
        dry_run=True,
    )

    assert result.status in {"held", "dry_run_attempt"}
    assert "candles" in provider_checks
    assert provider_checks.count("contract") >= 1


def test_only_final_live_preflight_runs_under_the_account_lock(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    transaction_states: list[bool] = []

    class TransactionRecordingClient(RecordingClient):
        def list_accounts(self, *, only_active_accounts=True):
            transaction_states.append(db_session.in_transaction())
            return super().list_accounts(only_active_accounts=only_active_accounts)

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=TransactionRecordingClient(),
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "submitted"
    assert transaction_states == [True]


def test_config_change_after_audit_commit_blocks_stale_live_signal(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config_id = int(config.id)
    original_max_contracts = float(config.max_contracts)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    original_require_config = bot_service._require_bot_config
    locked_reload_count = 0

    def mutate_before_final_reload(db, **kwargs):
        nonlocal locked_reload_count
        if kwargs.get("lock_for_update"):
            locked_reload_count += 1
        # Initial lock, post-market revalidation, then final post-audit lock.
        if locked_reload_count == 3:
            with Session(bind=db.get_bind(), expire_on_commit=False) as concurrent_db:
                concurrent_config = (
                    concurrent_db.query(BotConfig)
                    .filter(BotConfig.user_id == USER_A)
                    .filter(BotConfig.id == config_id)
                    .one()
                )
                concurrent_config.max_contracts = original_max_contracts + 1
                concurrent_db.commit()
        return original_require_config(db, **kwargs)

    monkeypatch.setattr(bot_service, "_require_bot_config", mutate_before_final_reload)
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

    db_session.expire_all()
    persisted_config = db_session.query(BotConfig).filter(BotConfig.id == config_id).one()
    assert result.status == "risk_blocked"
    assert "execution_state_changed" in _risk_codes(result)
    assert result.order_attempt is not None
    assert result.order_attempt.status == "blocked"
    assert client.place_order_calls == []
    assert float(persisted_config.max_contracts) == original_max_contracts + 1


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


def test_superseded_evaluation_failure_cannot_disable_or_overwrite_new_active_run(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, enabled=False)
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    evaluation_count = 0
    winning_run_id: int | None = None
    timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)

    def overlapping_fetch(_db, *, user_id, config, client):
        nonlocal evaluation_count, winning_run_id
        evaluation_count += 1
        if evaluation_count == 1:
            with SessionLocal() as competing_db:
                winner = start_bot_run(
                    competing_db,
                    user_id=user_id,
                    bot_config_id=int(config.id),
                    client=client,
                    dry_run=True,
                )
                competing_db.commit()
                assert winner.run is not None
                winning_run_id = int(winner.run.id)
        return [
            ProjectXMarketCandle(
                user_id=user_id,
                contract_id=str(config.contract_id),
                symbol=config.symbol,
                live=False,
                unit=str(config.timeframe_unit),
                unit_number=int(config.timeframe_unit_number),
                candle_timestamp=timestamp,
                open_price=100,
                high_price=101,
                low_price=99,
                close_price=100,
                volume=100,
                is_partial=False,
            )
        ], SignalResult(
            action="HOLD",
            reason="no signal",
            candle_timestamp=timestamp,
            price=100,
            raw_payload={},
        )

    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", overlapping_fetch)
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(bot_service, "build_signal_trade_evaluation", lambda **_kwargs: None)

    with pytest.raises(
        BotRunEvaluationError,
        match="changed_during_market_fetch|cannot be evaluated",
    ):
        start_bot_run(
            db_session,
            user_id=USER_A,
            bot_config_id=int(config.id),
            client=RecordingClient(),
            dry_run=True,
        )

    db_session.rollback()
    db_session.expire_all()
    assert winning_run_id is not None
    persisted_config = db_session.get(BotConfig, int(config.id))
    winning_run = db_session.get(BotRun, winning_run_id)
    losing_run = db_session.query(BotRun).filter(BotRun.id != winning_run_id).one()
    assert persisted_config is not None and persisted_config.enabled is True
    assert winning_run is not None and winning_run.status == "running"
    assert winning_run.raw_state["phase"] == "idle"
    assert losing_run.status == "stopped"
    assert losing_run.stop_reason == "superseded_by_manual_start"
    assert losing_run.raw_state["phase"] != "error"


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


@pytest.mark.parametrize("strategy_type", ["donchian_breakout", "topbot_adaptive"])
def test_live_target_exit_is_resized_from_authoritative_provider_position(
    db_session,
    monkeypatch,
    strategy_type,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = strategy_type
    config.max_contracts = 2
    config.max_open_position = 2
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "effective_order_size": 2.0,
            "current_position_qty": 2.0,
            "target_position_qty": 0.0,
        },
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}]
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

    assert result.status == "submitted"
    assert result.order_attempt is not None
    assert float(result.order_attempt.size) == 1.0
    assert result.order_attempt.raw_request["size"] == 1
    assert result.order_attempt.raw_request["side"] == 1
    assert result.order_attempt.raw_request["strategyOrderPlan"]["authoritative_provider_position_qty"] == 1.0
    assert float(result.decision.quantity) == 1.0
    assert result.decision.raw_payload["planned_order_size"] == 2.0
    assert result.decision.raw_payload["effective_order_size"] == 1.0
    assert len(client.place_order_calls) == 1
    assert client.place_order_calls[0]["size"] == 1
    assert client.place_order_calls[0]["side"] == 1


def test_live_target_direction_conflict_fails_closed(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    config.max_contracts = 2
    config.max_open_position = 2
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "effective_order_size": 1.0,
            "current_position_qty": 1.0,
            "target_position_qty": 0.0,
        },
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": -1.0}]
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
    assert "authoritative_target_direction_conflict" in _risk_codes(result)
    assert float(result.decision.quantity) == 0.0
    conflict_event = next(
        row for row in result.risk_events if row.code == "authoritative_target_direction_conflict"
    )
    assert conflict_event.raw_payload["effective_quantity"] == 0.0
    assert conflict_event.raw_payload["authoritative_target_delta"] == 1.0
    assert client.place_order_calls == []


def test_initial_target_risk_math_uses_provider_delta_without_order_attempt(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "topbot_adaptive"
    config.max_contracts = 1
    config.max_open_position = 1
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="BUY",
        raw_payload={
            "signal_category": "entry",
            "entry_price": 101.0,
            "stop_loss": 100.0,
            "take_profit": 103.0,
            "effective_order_size": 2.0,
            "current_position_qty": -1.0,
            "target_position_qty": 1.0,
        },
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(positions=[])

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "submitted"
    assert result.order_attempt is not None
    assert float(result.order_attempt.size) == 1.0
    assert float(result.decision.quantity) == 1.0
    assert result.decision.raw_payload["planned_order_size"] == 2.0
    assert result.decision.raw_payload["authoritative_provider_position_qty"] == 0.0
    assert len(client.place_order_calls) == 1
    assert client.place_order_calls[0]["size"] == 1


def test_bracketed_atomic_reversal_fails_closed(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "reversal",
            "entry_price": 101.0,
            "stop_loss": 102.0,
            "take_profit": 99.0,
            "effective_order_size": 2.0,
            "current_position_qty": 1.0,
            "target_position_qty": -1.0,
        },
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}]
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
    assert "atomic_reversal_not_supported" in _risk_codes(result)
    assert client.place_order_calls == []


def test_open_position_state_never_falls_back_across_delivery_contracts(db_session):
    db_session.add(
        ProjectXTradeEvent(
            user_id=USER_A,
            account_id=9001,
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=100,
            trade_timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
            order_id="m26-entry",
        )
    )
    db_session.flush()

    state = bot_service.load_open_position_state(
        db_session,
        user_id=USER_A,
        account_id=9001,
        contract_id="CON.F.US.MNQ.U26",
        symbol="MNQ",
    )

    assert state.side == "flat"
    assert state.net_qty == 0.0


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


def test_live_daily_loss_preflight_uses_every_trade_history_page(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    monkeypatch.setattr(bot_service, "_LIVE_PREFLIGHT_TRADE_PAGE_SIZE", 1)

    now = datetime.now(timezone.utc)
    pages = {
        0: [
            {
                "account_id": 9001,
                "timestamp": now,
                "source_trade_id": "new-profit",
                "order_id": "new-profit-order",
                "pnl": 100.0,
                "fees": 0.0,
                "voided": False,
                "raw_payload": {"voided": False},
            }
        ],
        1: [
            {
                "account_id": 9001,
                "timestamp": now - timedelta(minutes=1),
                "source_trade_id": "older-loss",
                "order_id": "older-loss-order",
                "pnl": -500.0,
                "fees": 0.0,
                "voided": False,
                "raw_payload": {"voided": False},
            }
        ],
        2: [],
    }

    class PagedClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.trade_offsets: list[int] = []

        def fetch_trade_history(self, **kwargs):
            assert kwargs["require_valid_collection"] is True
            assert kwargs["limit"] == 1
            offset = int(kwargs["offset"])
            self.trade_offsets.append(offset)
            return list(pages[offset])

    client = PagedClient()
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
    assert "live_preflight_unavailable" not in _risk_codes(result)
    assert client.trade_offsets == [0, 1, 2]
    assert client.place_order_calls == []


@pytest.mark.parametrize("failure_mode", ["repeated_page", "partial_overlap", "page_cap"])
def test_live_preflight_fails_closed_on_ambiguous_trade_pagination(
    db_session,
    monkeypatch,
    failure_mode,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    page_size = 2 if failure_mode == "partial_overlap" else 1
    monkeypatch.setattr(bot_service, "_LIVE_PREFLIGHT_TRADE_PAGE_SIZE", page_size)
    monkeypatch.setattr(bot_service, "_LIVE_PREFLIGHT_TRADE_MAX_PAGES", 2)

    now = datetime.now(timezone.utc)

    class AmbiguousClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.trade_offsets: list[int] = []

        def fetch_trade_history(self, **kwargs):
            assert kwargs["require_valid_collection"] is True
            offset = int(kwargs["offset"])
            self.trade_offsets.append(offset)
            if failure_mode == "partial_overlap":
                identities = (
                    ["first", "overlap"]
                    if offset == 0
                    else ["overlap", "new-but-ambiguous"]
                )
            else:
                identities = [
                    "repeated" if failure_mode == "repeated_page" else f"page-{offset}"
                ]
            return [
                {
                    "account_id": 9001,
                    "timestamp": now - timedelta(minutes=offset),
                    "source_trade_id": identity,
                    "order_id": f"{identity}-order",
                    "pnl": 100.0,
                    "fees": 0.0,
                    "voided": False,
                    "raw_payload": {"voided": False},
                }
                for identity in identities
            ]

    client = AmbiguousClient()
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
    assert client.trade_offsets == [0, page_size]
    assert client.place_order_calls == []


def test_live_daily_loss_uses_round_turn_fees_and_topstep_commission(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.max_daily_loss = 250
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        trades=[
            {
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "symbol": "MNQ",
                "timestamp": datetime.now(timezone.utc),
                "size": 1.0,
                "pnl": -248.25,
                "fees": 0.75,
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


def test_local_daily_pnl_uses_canonical_round_turn_fee_and_commission(db_session):
    db_session.add(
        ProjectXTradeEvent(
            user_id=USER_A,
            account_id=9001,
            contract_id=CONTRACT_ID,
            symbol="MNQ",
            side="SELL",
            size=1,
            price=100,
            trade_timestamp=datetime.now(timezone.utc),
            order_id="closed-trade",
            pnl=-248.25,
            fees=0.75,
            fee_scope="per_side",
        )
    )
    db_session.flush()

    assert bot_service._todays_account_net_pnl(
        db_session,
        user_id=USER_A,
        account_id=9001,
    ) == pytest.approx(-250.25)


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


def test_late_reconciliation_blocks_current_evaluation_and_restarts_settlement_window(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    old_timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
    prior = BotOrderAttempt(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        contract_id=CONTRACT_ID,
        execution_mode="live",
        side="BUY",
        order_type="market",
        size=1,
        status="submission_unknown",
        raw_request={"customTag": "topsignal-late-fill"},
        created_at=old_timestamp,
        updated_at=old_timestamp,
    )
    db_session.add(prior)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        orders=[
            {
                "order_id": "provider-late-fill",
                "account_id": 9001,
                "status": 2,
                "custom_tag": "topsignal-late-fill",
                "raw_payload": {"id": "provider-late-fill", "status": 2},
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
    assert "order_reconciliation_settling" in _risk_codes(result)
    assert prior.status == "submitted"
    assert prior.updated_at > old_timestamp
    assert bot_service._recent_live_submission_count(
        db_session,
        user_id=USER_A,
        account_id=9001,
    ) == 1
    assert client.place_order_calls == []


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


def test_bracket_required_live_entry_fails_closed_without_tick_metadata(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "ema_trend_pullback"
    config.contract_id = "CON.F.US.BP6.U26"
    config.symbol = "BP6"
    config.allowed_contracts = [config.contract_id]
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        raw_payload={"entry_price": 101.0, "stop_loss": 100.0, "take_profit": 103.0},
    )
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
    assert "instrument_tick_metadata_missing" in _risk_codes(result)
    assert client.place_order_calls == []


def test_provider_tick_metadata_is_persisted_and_used_for_live_brackets(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "ema_trend_pullback"
    config.contract_id = "CON.F.US.BP6.U26"
    config.symbol = "BP6"
    config.allowed_contracts = [config.contract_id]
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        raw_payload={"entry_price": 101.0, "stop_loss": 100.0, "take_profit": 103.0},
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    class MetadataClient(RecordingClient):
        def search_contracts(self, *, search_text, live):
            del search_text, live
            return [
                {
                    "id": "CON.F.US.BP6.U26",
                    "symbol_id": "F.US.BP6",
                    "tick_size": 0.0001,
                    "tick_value": 6.25,
                    "active_contract": True,
                }
            ]

    client = MetadataClient()
    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "submitted"
    assert len(client.place_order_calls) == 1
    assert client.place_order_calls[0]["stop_loss_bracket"] == {"ticks": 10000, "type": 4}
    assert client.place_order_calls[0]["take_profit_bracket"] == {"ticks": 20000, "type": 1}
    metadata = db_session.query(InstrumentMetadata).filter(InstrumentMetadata.symbol == "BP6").one()
    assert float(metadata.tick_size) == pytest.approx(0.0001)
    assert float(metadata.tick_value) == pytest.approx(6.25)


def test_invalid_protective_geometry_is_blocked_before_order_claim(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "ema_trend_pullback"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        raw_payload={"entry_price": 101.0, "stop_loss": 102.0, "take_profit": 103.0},
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=RecordingClient(),
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.status == "risk_blocked"
    assert "strategy_stop_geometry_invalid" in _risk_codes(result)
    assert result.order_attempt is None


def test_missing_tick_metadata_does_not_block_valid_dry_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session)
    config.strategy_type = "ema_trend_pullback"
    config.contract_id = "CON.F.US.BP6.U26"
    config.symbol = "BP6"
    config.allowed_contracts = [config.contract_id]
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        raw_payload={"entry_price": 101.0, "stop_loss": 100.0, "take_profit": 103.0},
    )

    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=RecordingClient(),
        dry_run=True,
    )

    assert result.status == "dry_run_attempt"
    assert result.order_attempt is not None
    assert "instrument_tick_metadata_missing" not in _risk_codes(result)


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
