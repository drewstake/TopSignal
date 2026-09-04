import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.bot_schemas import BotConfigUpdateIn
from app.models import (
    Account,
    AccountEmergencyAction,
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRun,
    BotRuntimeLease,
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
from app.services.bot_service import (
    BotRunEvaluationError,
    BotWorkerLeaseToken,
    SignalResult,
    evaluate_bot_config,
    start_bot_run,
)
from app.services.bot_risk import RiskBlock, RiskEvaluationContext, evaluate_risk
from app.services.projectx_client import PROJECTX_ERROR_NETWORK, ProjectXClientError


USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"
CONTRACT_ID = "CON.F.US.MNQ.M26"


@pytest.fixture(autouse=True)
def open_exchange_session(monkeypatch):
    # Execution tests use mocked provider data and an open exchange by default.
    # Closure-specific tests override this; the real calendar has its own suite.
    monkeypatch.setattr(bot_service, "futures_session_is_open", lambda *_args, **_kwargs: True)


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
        account_simulated: bool | None = True,
        positions: list[dict] | None = None,
        open_orders: list[dict] | None = None,
        trades: list[dict] | None = None,
        orders: list[dict] | None = None,
    ):
        self.order_error = order_error
        self.account_can_trade = account_can_trade
        self.account_simulated = account_simulated
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.trades = trades or []
        self.orders = orders or []
        self.place_order_calls: list[dict] = []
        self.cancel_order_calls: list[dict] = []
        self.close_position_calls: list[dict] = []

    def list_accounts(self, *, only_active_accounts=True):
        del only_active_accounts
        return [
            {
                "id": 9001,
                "name": "Practice 9001",
                "status": "ACTIVE" if self.account_can_trade else "LOCKED_OUT",
                "can_trade": self.account_can_trade,
                "simulated": self.account_simulated,
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

    def cancel_order(self, *, account_id, order_id):
        self.cancel_order_calls.append({"account_id": account_id, "order_id": order_id})
        self.open_orders = [
            row for row in self.open_orders if str(row.get("order_id")) != str(order_id)
        ]
        return {"success": True, "raw_payload": {"success": True}}

    def close_position(self, *, account_id, contract_id):
        self.close_position_calls.append(
            {"account_id": account_id, "contract_id": contract_id}
        )
        self.positions = [
            row
            for row in self.positions
            if not (
                int(row.get("account_id", -1)) == int(account_id)
                and str(row.get("contract_id")) == str(contract_id)
            )
        ]
        return {"success": True, "raw_payload": {"success": True}}


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
        provider_simulated=True,
        provider_classification_observed_at=datetime.now(timezone.utc),
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
    timestamp = candle_timestamp or (datetime.now(timezone.utc) - timedelta(minutes=6)).replace(microsecond=0)

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


def _add_running_live_worker_run(db: Session, config: BotConfig) -> BotRun:
    now = datetime.now(timezone.utc)
    run = BotRun(
        user_id=str(config.user_id),
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        status="running",
        dry_run=False,
        started_at=now,
        last_heartbeat_at=now,
        raw_state={
            "source": "manual_start",
            "phase": "idle",
            "continuous": True,
            "execution_mode": "live",
            "live_routing_confirmed": True,
        },
    )
    db.add(run)
    db.flush()
    return run


def _add_worker_lease(db: Session, *, owner_id: str) -> BotWorkerLeaseToken:
    now = datetime.now(timezone.utc)
    lease_name = "recurring-bot-evaluator-v1"
    db.add(
        BotRuntimeLease(
            lease_name=lease_name,
            owner_id=owner_id,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=45),
            details={},
        )
    )
    db.flush()
    return BotWorkerLeaseToken(
        lease_name=lease_name,
        owner_id=owner_id,
        lease_ttl_seconds=45,
    )


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


def test_worker_lease_takeover_mid_cycle_fences_place_order_and_keeps_run_armed(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    run = _add_running_live_worker_run(db_session, config)
    lease_token = _add_worker_lease(db_session, owner_id="worker-original")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    class MidCycleTakeoverClient(RecordingClient):
        def list_accounts(self, *, only_active_accounts=True):
            lease = db_session.get(BotRuntimeLease, lease_token.lease_name)
            lease.owner_id = "worker-takeover"
            lease.heartbeat_at = datetime.now(timezone.utc)
            lease.expires_at = datetime.now(timezone.utc) + timedelta(seconds=45)
            db_session.flush()
            return super().list_accounts(only_active_accounts=only_active_accounts)

    client = MidCycleTakeoverClient()
    result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=client,
        run=run,
        dry_run=False,
        confirm_live_order_routing=True,
        worker_lease_token=lease_token,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"worker_lease_lost"}
    assert client.place_order_calls == []
    assert result.order_attempt is not None
    assert result.order_attempt.status == "blocked"
    assert result.config.enabled is True
    assert result.run is not None and result.run.status == "running"


def test_final_boundary_reloads_disconnect_invalidated_classification_before_order(
    db_session,
    monkeypatch,
):
    account, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    class DisconnectDuringPreflightClient(RecordingClient):
        def list_accounts(self, *, only_active_accounts=True):
            # Simulate the account-hub disconnect invalidation that is allowed
            # to commit while remote preflight runs without an Account row lock.
            persisted = db_session.get(Account, int(account.id))
            persisted.provider_simulated = None
            persisted.provider_classification_observed_at = None
            db_session.flush()
            return super().list_accounts(only_active_accounts=only_active_accounts)

    client = DisconnectDuringPreflightClient()
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
    assert _risk_codes(result) == {"account_automation_classification_unknown"}
    assert client.place_order_calls == []
    assert result.config.enabled is True


def test_emergency_intent_inserted_during_preflight_fences_sync_place_order(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)

    class EmergencyDuringPreflightClient(RecordingClient):
        def list_accounts(self, *, only_active_accounts=True):
            now = datetime.now(timezone.utc)
            db_session.add(
                AccountEmergencyAction(
                    user_id=USER_A,
                    account_id=9001,
                    status="pending",
                    confirmed_flat=False,
                    lease_owner_id="emergency-owner",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={},
                )
            )
            db_session.flush()
            return super().list_accounts(only_active_accounts=only_active_accounts)

    client = EmergencyDuringPreflightClient()
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
    assert _risk_codes(result) == {"account_emergency_flatten_unresolved"}
    assert client.place_order_calls == []
    assert result.config.enabled is True


def test_worker_lease_loss_fences_verified_exit_cancel_and_close(
    db_session,
    monkeypatch,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    run = _add_running_live_worker_run(db_session, config)
    lease_token = _add_worker_lease(db_session, owner_id="worker-original")
    db_session.get(BotRuntimeLease, lease_token.lease_name).owner_id = "worker-takeover"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "current_position_qty": 1.0,
            "target_position_qty": 0.0,
        },
    )
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    client = RecordingClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}],
        open_orders=[
            {
                "order_id": "protective-order",
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
        run=run,
        dry_run=False,
        confirm_live_order_routing=True,
        worker_lease_token=lease_token,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"worker_lease_lost"}
    assert client.cancel_order_calls == []
    assert client.close_position_calls == []
    assert client.place_order_calls == []
    assert result.config.enabled is True
    assert result.run is not None and result.run.status == "running"


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


def test_account_emergency_action_database_invariants(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(
        AccountEmergencyAction(
            user_id=USER_A,
            account_id=9001,
            status="pending",
            confirmed_flat=False,
            lease_owner_id="first-owner",
            lease_expires_at=now + timedelta(minutes=5),
            request_payload={},
        )
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                AccountEmergencyAction(
                    user_id=USER_A,
                    account_id=9001,
                    status="pending",
                    confirmed_flat=False,
                    lease_owner_id="second-owner",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={},
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                AccountEmergencyAction(
                    user_id=USER_A,
                    account_id=9004,
                    status="pending",
                    confirmed_flat=False,
                    request_payload={},
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                AccountEmergencyAction(
                    user_id=USER_A,
                    account_id=9005,
                    status="unconfirmed",
                    confirmed_flat=False,
                    attempt_count=0,
                    request_payload={},
                    completed_at=now,
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                AccountEmergencyAction(
                    user_id=USER_A,
                    account_id=9002,
                    status="confirmed_account_flat",
                    confirmed_flat=False,
                    request_payload={},
                    completed_at=datetime.now(timezone.utc),
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                AccountEmergencyAction(
                    user_id=USER_A,
                    account_id=9003,
                    status="pending",
                    confirmed_flat=False,
                    lease_owner_id="completed-pending-owner",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={},
                    completed_at=now,
                )
            )
            db_session.flush()


@pytest.mark.parametrize("status", ["pending", "unconfirmed"])
def test_unresolved_account_emergency_latch_blocks_every_new_start(
    db_session,
    status,
):
    _, config = _add_account_and_config(db_session, enabled=False)
    action = AccountEmergencyAction(
        user_id=USER_A,
        account_id=9001,
        status=status,
        confirmed_flat=False,
        lease_owner_id=("active-owner" if status == "pending" else None),
        lease_expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=5)
            if status == "pending"
            else None
        ),
        request_payload={},
        completed_at=(datetime.now(timezone.utc) if status == "unconfirmed" else None),
    )
    db_session.add(action)
    db_session.commit()
    client = RecordingClient()

    with pytest.raises(
        bot_service.AccountEmergencyLatchActiveError,
        match="account_emergency_flatten_unresolved",
    ) as raised:
        start_bot_run(
            db_session,
            user_id=USER_A,
            bot_config_id=int(config.id),
            client=client,
            dry_run=True,
        )

    assert raised.value.action_id == int(action.id)
    assert raised.value.status == status
    assert db_session.query(BotRun).count() == 0
    assert db_session.get(BotConfig, int(config.id)).enabled is False
    assert client.place_order_calls == []


def test_later_confirmed_account_flat_action_clears_start_latch(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            AccountEmergencyAction(
                user_id=USER_A,
                account_id=9001,
                status="unconfirmed",
                confirmed_flat=False,
                request_payload={},
                completed_at=now - timedelta(seconds=1),
            ),
            AccountEmergencyAction(
                user_id=USER_A,
                account_id=9001,
                status="confirmed_account_flat",
                confirmed_flat=True,
                request_payload={},
                result_payload={"confirmed_flat": True},
                completed_at=now,
            ),
        ]
    )
    db_session.commit()
    timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)

    def hold_fetch(_db, *, user_id, config, client):
        del client
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

    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", hold_fetch)
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(bot_service, "build_signal_trade_evaluation", lambda **_kwargs: None)

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=RecordingClient(),
        dry_run=True,
    )

    assert result.run is not None
    assert result.run.status == "running"
    assert result.config.enabled is True


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


def test_unexpected_provider_failure_preserves_unknown_attempt_and_terminal_run(db_session, monkeypatch):
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
    assert attempt.status == "submission_unknown"
    assert attempt.execution_mode == "live"
    assert attempt.idempotency_key == result.idempotency_key
    assert "provider order unavailable" in str(attempt.rejection_reason)
    assert run.status == "error"
    assert run.stop_reason == "provider_order_submission_unknown"
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
    assert client.place_order_calls == []
    assert client.close_position_calls == [
        {"account_id": 9001, "contract_id": CONTRACT_ID}
    ]
    assert result.order_attempt.raw_request["providerAction"] == "Position/closeContract"
    assert result.order_attempt.raw_request["verifiedFlat"] is True


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


def test_reducing_exit_cancels_working_order_then_uses_verified_close(
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

    assert result.status == "submitted"
    assert result.risk_events == []
    assert client.place_order_calls == []
    assert client.cancel_order_calls == [
        {"account_id": 9001, "order_id": "protective-sell"}
    ]
    assert client.close_position_calls == [
        {"account_id": 9001, "contract_id": CONTRACT_ID}
    ]


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
                "contract_id": CONTRACT_ID,
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
                "contract_id": CONTRACT_ID,
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
                "contract_id": CONTRACT_ID,
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
    config.max_daily_loss = 100_000
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
    config.max_daily_loss = 100_000
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


@pytest.mark.parametrize("response", [None, [], "accepted", 1])
def test_malformed_submission_response_remains_unknown(response):
    attempt = SimpleNamespace(
        raw_request={"accountId": 9001, "contractId": CONTRACT_ID, "type": 2, "side": 0, "size": 1},
        status="pending", provider_order_id=None, rejection_reason=None, raw_response=None,
    )

    class MalformedClient(RecordingClient):
        def place_order(self, **kwargs):
            self.place_order_calls.append(kwargs)
            return response

    client = MalformedClient()
    bot_service._submit_order_attempt(client=client, order_attempt=attempt)
    assert attempt.status == "submission_unknown"
    assert len(client.place_order_calls) == 1


def test_typed_definite_rejection_does_not_claim_unknown_submission():
    attempt = SimpleNamespace(
        raw_request={"accountId": 9001, "contractId": CONTRACT_ID, "type": 2, "side": 0, "size": 1},
        status="pending", provider_order_id=None, rejection_reason=None, raw_response=None,
    )
    client = RecordingClient(order_error=ProjectXClientError("Order rejected", status_code=400))
    bot_service._submit_order_attempt(client=client, order_attempt=attempt)
    assert attempt.status == "error"
    assert len(client.place_order_calls) == 1


@pytest.mark.parametrize("changed", [
    {"account_id": 9999}, {"account_id": None},
    {"contract_id": "CON.F.US.ES.U26"}, {"contract_id": None},
    {"order_id": "different-recorded-order"},
])
def test_reconciliation_requires_matching_order_identity(db_session, changed):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    attempt = BotOrderAttempt(
        user_id=USER_A, bot_config_id=int(config.id), account_id=9001,
        contract_id=CONTRACT_ID, execution_mode="live", side="BUY", order_type="market",
        size=1, status="submission_unknown", provider_order_id="known-order",
        raw_request={"customTag": "identity-test"},
    )
    db_session.add(attempt)
    db_session.commit()
    provider_order = {
        "order_id": "known-order", "account_id": 9001, "contract_id": CONTRACT_ID,
        "status": 2, "custom_tag": "identity-test", **changed,
    }
    result = bot_service.reconcile_unresolved_order_attempts(
        db_session, user_id=USER_A, account_id=9001,
        client=RecordingClient(orders=[provider_order]),
    )
    assert result.unresolved_count == 1
    assert result.resolved_count == 0
    assert attempt.status == "submission_unknown"
    assert attempt.provider_order_id == "known-order"


@pytest.mark.parametrize("can_trade,state", [(False, "LOCKED_OUT"), (None, "ACTIVE"), (True, "HIDDEN")])
def test_final_boundary_blocks_tradability_change(db_session, monkeypatch, can_trade, state):
    account, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)

    class ChangedDuringPreflightClient(RecordingClient):
        def list_accounts(self, **kwargs):
            account.can_trade = can_trade
            account.account_state = state
            db_session.flush()
            return super().list_accounts(**kwargs)

    client = ChangedDuringPreflightClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=False, confirm_live_order_routing=True,
    )
    assert result.status == "risk_blocked"
    assert "account_tradability_changed" in _risk_codes(result)
    assert client.place_order_calls == []


def test_slow_authoritative_preflight_blocks_routing(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    times = iter([100.0, 116.0])
    monkeypatch.setattr(bot_service, "monotonic", lambda: next(times))
    client = RecordingClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=False, confirm_live_order_routing=True,
    )
    assert result.status == "risk_blocked"
    assert "live_preflight_unavailable" in _risk_codes(result)
    assert client.place_order_calls == []


def test_authoritative_preflight_expiry_after_database_lock_blocks_routing(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    times = iter([100.0, 101.0, 102.0, 116.0])
    monkeypatch.setattr(bot_service, "monotonic", lambda: next(times))
    client = RecordingClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=False, confirm_live_order_routing=True,
    )
    assert result.status == "risk_blocked"
    assert "live_preflight_unavailable" in _risk_codes(result)
    assert client.place_order_calls == []


def test_authoritative_loss_cooldown_blocks_before_local_trade_sync(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.cooldown_seconds = 300
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(trades=[{
        "account_id": 9001, "contract_id": CONTRACT_ID, "symbol": "MNQ", "size": 1,
        "timestamp": datetime.now(timezone.utc), "pnl": -10.0, "fees": 0.0,
        "voided": False, "order_id": "new-unsynced-loss", "source_trade_id": "loss-1",
    }])
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=False, confirm_live_order_routing=True,
    )
    assert db_session.query(ProjectXTradeEvent).count() == 0
    assert result.status == "risk_blocked"
    assert "cooldown_after_loss" in _risk_codes(result)
    assert client.place_order_calls == []


@pytest.mark.parametrize("foreign_state", ["ACTIVE", "LOCKED_OUT", "HIDDEN"])
def test_shared_broker_account_cannot_start_live_run(db_session, foreign_state):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    foreign_account, _ = _add_account_and_config(db_session, user_id=USER_B)
    foreign_account.account_state = foreign_state
    db_session.commit()
    client = RecordingClient()
    with pytest.raises(ValueError, match="shared_broker_account_ownership"):
        start_bot_run(
            db_session, user_id=USER_A, bot_config_id=int(config.id), client=client,
            dry_run=False, confirm_live_order_routing=True,
        )
    assert config.enabled is False
    assert db_session.query(BotRun).count() == 0
    assert client.place_order_calls == []


def test_shared_broker_account_can_still_dry_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session)
    _add_account_and_config(db_session, user_id=USER_B)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    client = RecordingClient()
    result = start_bot_run(
        db_session, user_id=USER_A, bot_config_id=int(config.id), client=client, dry_run=True,
    )
    assert result.status == "dry_run_attempt"
    assert client.place_order_calls == []


def test_final_boundary_blocks_new_shared_account_ownership(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)

    class NewOwnerDuringPreflightClient(RecordingClient):
        def list_accounts(self, **kwargs):
            _add_account_and_config(db_session, user_id=USER_B)
            return super().list_accounts(**kwargs)

    client = NewOwnerDuringPreflightClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=False, confirm_live_order_routing=True,
    )
    assert "shared_broker_account_ownership" in _risk_codes(result)
    assert client.place_order_calls == []


@pytest.mark.parametrize("orphan_kind", ["running", "pending", "submission_unknown", "submitted"])
def test_deleted_duplicate_account_does_not_clear_foreign_execution_interlock(db_session, orphan_kind):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    foreign_account, foreign_config = _add_account_and_config(db_session, user_id=USER_B)
    if orphan_kind == "running":
        _add_running_live_worker_run(db_session, foreign_config)
    else:
        db_session.add(BotOrderAttempt(
            user_id=USER_B, bot_config_id=int(foreign_config.id), account_id=9001,
            contract_id=CONTRACT_ID, execution_mode="live", side="BUY", order_type="market",
            size=1, status=orphan_kind, raw_request={"customTag": "orphan"},
        ))
    db_session.delete(foreign_account)
    db_session.commit()
    assert db_session.query(Account).filter(Account.user_id == USER_B).count() == 0
    with pytest.raises(ValueError, match="shared_broker_account_ownership"):
        start_bot_run(
            db_session, user_id=USER_A, bot_config_id=int(config.id), client=RecordingClient(),
            dry_run=False, confirm_live_order_routing=True,
        )


@pytest.mark.parametrize("revoke_after_lock", [False, True])
def test_worker_shutdown_fences_mutation_before_and_after_database_lock(db_session, monkeypatch, revoke_after_lock):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    run = _add_running_live_worker_run(db_session, config)
    token = _add_worker_lease(db_session, owner_id="shutdown-worker")
    checks = iter([True, False] if revoke_after_lock else [False])
    token = replace(token, mutation_allowed=lambda: next(checks))
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        run=run, dry_run=False, confirm_live_order_routing=True, worker_lease_token=token,
    )
    assert result.status == "risk_blocked"
    assert "worker_lease_lost" in _risk_codes(result)
    assert client.place_order_calls == []


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


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("closed_age_seconds, expected_block", [(30, False), (120, True)])
def test_configured_positive_staleness_threshold_controls_order_routing(
    db_session, monkeypatch, dry_run, closed_age_seconds, expected_block,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.max_data_staleness_seconds = 60
    db_session.commit()
    # Five-minute candle's age is measured from close, not its opening time.
    timestamp = datetime.now(timezone.utc) - timedelta(minutes=5, seconds=closed_age_seconds)
    _patch_actionable_signal(monkeypatch, candle_timestamp=timestamp)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=dry_run, confirm_live_order_routing=True,
    )
    assert ("stale_market_data" in _risk_codes(result)) is expected_block
    if expected_block:
        assert result.status == "risk_blocked"
        assert client.place_order_calls == []
    else:
        assert result.status == ("dry_run_attempt" if dry_run else "submitted")
        assert len(client.place_order_calls) == (0 if dry_run else 1)


@pytest.mark.parametrize("prior_status", ["blocked", "rejected", "error"])
@pytest.mark.parametrize("prior_age_seconds, expected_block", [(30, True), (301, False)])
def test_rejected_order_cooldown_blocks_until_configured_interval_expires(
    db_session, monkeypatch, prior_status, prior_age_seconds, expected_block,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.cooldown_seconds = 300
    db_session.add(BotOrderAttempt(
        user_id=USER_A, bot_config_id=int(config.id), account_id=9001,
        contract_id=CONTRACT_ID, execution_mode="live", side="BUY", order_type="market",
        size=1, status=prior_status, raw_request={"customTag": "prior-rejection"},
        created_at=datetime.now(timezone.utc) - timedelta(seconds=prior_age_seconds),
    ))
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient()
    result = evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=None, client=client,
        dry_run=False, confirm_live_order_routing=True,
    )
    assert ("cooldown_after_rejection" in _risk_codes(result)) is expected_block
    assert result.status == ("risk_blocked" if expected_block else "submitted")
    assert len(client.place_order_calls) == (0 if expected_block else 1)


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


def _enable_live_test_routing(monkeypatch) -> None:
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    monkeypatch.setattr(bot_service, "futures_session_is_open", lambda *_args, **_kwargs: True)


def _add_mnq_tick_metadata(db_session) -> None:
    db_session.add(InstrumentMetadata(symbol="MNQ", tick_size=0.25, tick_value=0.5))
    db_session.flush()


def test_verified_position_reduction_bypasses_every_entry_risk_gate():
    blocks = evaluate_risk(
        RiskEvaluationContext(
            bot_enabled=True,
            account_state="ACTIVE",
            account_can_trade=True,
            live_funded_account=False,
            configured_execution_mode="dry_run",
            dry_run=True,
            confirm_live_order_routing=False,
            running_under_tests=False,
            live_environment_enabled=False,
            contract_allowed=False,
            action="SELL",
            order_size=1.0,
            resulting_position_qty=1.0,
            max_contracts=float("nan"),
            max_open_position=float("nan"),
            trades_today=99,
            max_trades_per_day=0,
            daily_pnl=-10_000.0,
            max_daily_loss=1.0,
            latest_candle_age_seconds=10_000.0,
            max_data_staleness_seconds=1,
            inside_trading_session=False,
            delayed_session_block=RiskBlock("session_loss_limit_reached", "delayed"),
            cooldown_block=RiskBlock("cooldown_after_loss", "cooldown"),
            position_reducing=True,
            account_gross_position_qty=10_000.0,
            max_account_gross_position=1.0,
            account_unrealized_pnl=-10_000.0,
            unrealized_pnl_complete=False,
            proposed_stop_risk=None,
            require_proposed_stop_risk=True,
            exchange_session_open=False,
        )
    )

    assert blocks == []


def test_partial_live_reduction_fails_closed_without_projectx_reduce_only(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    config.max_contracts = 3
    config.max_open_position = 3
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "effective_order_size": 1.0,
            "current_position_qty": 2.0,
            "target_position_qty": 1.0,
        },
    )
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 2.0}]
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
    assert "partial_reduction_not_supported" in _risk_codes(result)
    assert client.place_order_calls == []
    assert client.close_position_calls == []


def test_full_exit_reconciles_ambiguous_close_without_retry(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "current_position_qty": 1.0,
            "target_position_qty": 0.0,
        },
    )
    _enable_live_test_routing(monkeypatch)

    class AmbiguousButFilledClient(RecordingClient):
        def close_position(self, *, account_id, contract_id):
            super().close_position(account_id=account_id, contract_id=contract_id)
            raise ProjectXClientError(
                "close timed out",
                status_code=408,
                submission_outcome_unknown=True,
                reason_code=PROJECTX_ERROR_NETWORK,
            )

    client = AmbiguousButFilledClient(
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
    assert len(client.close_position_calls) == 1
    assert result.order_attempt.raw_response["close_reconciled_after_error"] is True
    assert client.place_order_calls == []


def test_full_exit_reconciles_ambiguous_protective_cancel_before_close(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "current_position_qty": 1.0,
            "target_position_qty": 0.0,
        },
    )
    _enable_live_test_routing(monkeypatch)

    class AmbiguousButCancelledClient(RecordingClient):
        def cancel_order(self, *, account_id, order_id):
            super().cancel_order(account_id=account_id, order_id=order_id)
            raise ProjectXClientError(
                "cancel timed out",
                status_code=408,
                submission_outcome_unknown=True,
                reason_code=PROJECTX_ERROR_NETWORK,
            )

    client = AmbiguousButCancelledClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}],
        open_orders=[
            {
                "order_id": "201",
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

    assert result.status == "submitted"
    assert len(client.cancel_order_calls) == 1
    assert len(client.close_position_calls) == 1
    assert len(result.order_attempt.raw_response["cancel_errors"]) == 1
    assert client.place_order_calls == []


def test_sma_live_entry_uses_validated_atomic_provider_brackets(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_params = {"protective_stop_ticks": 12, "take_profit_ticks": 24}
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
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

    assert result.status == "submitted"
    assert len(client.place_order_calls) == 1
    assert client.place_order_calls[0]["stop_loss_bracket"] == {"ticks": 12, "type": 4}
    assert client.place_order_calls[0]["take_profit_bracket"] == {"ticks": 24, "type": 1}
    assert result.order_attempt.raw_request["stopLossBracket"] == {"ticks": 12, "type": 4}


def test_sma_entry_stop_risk_must_fit_remaining_account_loss_budget(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_params = {"protective_stop_ticks": 600, "take_profit_ticks": 800}
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
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
    assert "proposed_stop_risk_exceeds_daily_loss_budget" in _risk_codes(result)
    assert client.place_order_calls == []


def test_exit_label_cannot_bypass_protection_for_exposure_increasing_order(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.strategy_type = "donchian_breakout"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={"signal_category": "exit"},
    )
    _enable_live_test_routing(monkeypatch)
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
    assert "protective_stop_bracket_missing" in _risk_codes(result)
    assert client.place_order_calls == []


@pytest.mark.parametrize(
    ("position", "position_limit", "expected_code"),
    [
        (
            {"account_id": 9001, "contract_id": "CON.F.US.MES.M26", "signed_size": 1.0, "unrealized_pnl": 0.0},
            1,
            "max_account_gross_position",
        ),
        (
            {"account_id": 9001, "contract_id": "CON.F.US.MES.M26", "signed_size": 1.0},
            2,
            "account_unrealized_pnl_unavailable",
        ),
        (
            {"account_id": 9001, "contract_id": "CON.F.US.MES.M26", "signed_size": 1.0, "unrealized_pnl": -251.0},
            2,
            "max_daily_loss",
        ),
    ],
)
def test_live_entry_uses_account_wide_exposure_and_unrealized_pnl(
    db_session,
    monkeypatch,
    position,
    position_limit,
    expected_code,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.max_contracts = position_limit
    config.max_open_position = position_limit
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(positions=[position])

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
    assert expected_code in _risk_codes(result)
    assert client.place_order_calls == []


def test_account_wide_gross_limit_includes_working_orders_on_other_contracts(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(
        open_orders=[
            {
                "order_id": "other-contract-entry",
                "account_id": 9001,
                "contract_id": "CON.F.US.MES.M26",
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
    assert "max_account_gross_position" in _risk_codes(result)
    assert client.place_order_calls == []


def test_prior_projectx_delivery_blocks_entry_despite_ui_provider_root_alias(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.contract_id = "CON.F.US.ENQ.M26"
    config.symbol = "NQ"
    config.allowed_contracts = [config.contract_id]
    config.max_contracts = 3
    config.max_open_position = 3
    db_session.add(InstrumentMetadata(symbol="NQ", tick_size=0.25, tick_value=5.0))
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(
        positions=[
            {
                "account_id": 9001,
                "contract_id": "CON.F.US.ENQ.H26",
                "signed_size": 1.0,
                "unrealized_pnl": 0.0,
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
    assert "prior_delivery_exposure" in _risk_codes(result)
    assert result.decision.raw_payload["prior_delivery_contract_ids"] == ["CON.F.US.ENQ.H26"]
    assert client.place_order_calls == []


def test_exchange_closure_blocks_entries_but_not_verified_full_exit(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr(bot_service, "live_execution_environment_enabled", lambda: True)
    monkeypatch.setattr(bot_service, "futures_session_is_open", lambda *_args, **_kwargs: False)
    entry_client = RecordingClient()

    entry = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=entry_client,
        dry_run=False,
        confirm_live_order_routing=True,
    )
    assert entry.status == "risk_blocked"
    assert "exchange_session_closed" in _risk_codes(entry)
    assert entry_client.place_order_calls == []

    config_id = int(config.id)
    db_session.rollback()
    db_session.expunge_all()
    db_session.query(BotOrderAttempt).delete(synchronize_session=False)
    db_session.query(BotDecision).delete(synchronize_session=False)
    config = db_session.get(BotConfig, config_id)
    config.strategy_type = "donchian_breakout"
    db_session.commit()
    _patch_actionable_signal(
        monkeypatch,
        action="SELL",
        raw_payload={
            "signal_category": "exit",
            "current_position_qty": 1.0,
            "target_position_qty": 0.0,
        },
    )
    exit_client = RecordingClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}]
    )
    exit_result = evaluate_bot_config(
        db_session,
        user_id=USER_A,
        config=config,
        account=None,
        client=exit_client,
        dry_run=False,
        confirm_live_order_routing=True,
    )
    assert exit_result.status == "submitted"
    assert len(exit_client.close_position_calls) == 1
    assert exit_client.place_order_calls == []


def test_account_entry_fill_count_is_account_wide_and_excludes_exits(db_session):
    _, config = _add_account_and_config(db_session)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            ProjectXTradeEvent(
                user_id=USER_A,
                account_id=9001,
                contract_id=CONTRACT_ID,
                symbol="MNQ",
                side="BUY",
                size=1,
                price=100,
                trade_timestamp=now,
                order_id="entry-from-another-bot",
                pnl=None,
            ),
            ProjectXTradeEvent(
                user_id=USER_A,
                account_id=9001,
                contract_id=CONTRACT_ID,
                symbol="MNQ",
                side="SELL",
                size=1,
                price=101,
                trade_timestamp=now,
                order_id="position-reducing-exit",
                pnl=20,
                fees=0,
            ),
        ]
    )
    db_session.flush()

    assert config.id is not None
    assert bot_service._todays_account_entry_trade_count(
        db_session,
        user_id=USER_A,
        account_id=9001,
    ) == 1


def test_provider_entry_fill_consumes_account_daily_trade_limit(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    config.max_trades_per_day = 1
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(
        trades=[
            {
                "account_id": 9001,
                "source_trade_id": "provider-entry-fill",
                "order_id": "other-bot-entry",
                "timestamp": datetime.now(timezone.utc),
                "pnl": None,
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
    assert "max_trades_per_day" in _risk_codes(result)
    assert client.place_order_calls == []


def test_unknown_account_classification_keeps_continuous_live_run_armed(db_session, monkeypatch):
    account, config = _add_account_and_config(
        db_session,
        enabled=False,
        execution_mode="live",
    )
    account.provider_simulated = None
    account.provider_classification_observed_at = None
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=RecordingClient(account_simulated=None),
        dry_run=False,
        confirm_live_order_routing=True,
        continuous=True,
    )

    assert result.status == "risk_blocked"
    assert _risk_codes(result) == {"account_automation_classification_unknown"}
    assert result.run.status == "running"
    assert result.config.enabled is True
    assert result.run.raw_state["continuous"] is True
    assert result.run.raw_state["live_routing_confirmed"] is True


def test_fresh_gateway_account_classification_allows_live_entry(db_session, monkeypatch):
    account, config = _add_account_and_config(db_session, execution_mode="live")
    account.provider_simulated = True
    account.provider_classification_observed_at = datetime.now(timezone.utc)
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(account_simulated=None)

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


def test_authoritative_non_simulated_account_is_terminal_for_continuous_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=RecordingClient(account_simulated=False),
        dry_run=False,
        confirm_live_order_routing=True,
        continuous=True,
    )

    assert result.status == "risk_blocked"
    assert "account_type_not_eligible_for_automation" in _risk_codes(result)
    assert result.run.status == "blocked"
    assert result.config.enabled is False


def test_retryable_pre_routing_outage_preserves_only_opted_in_continuous_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    db_session.commit()

    def fail_fetch(*_args, **_kwargs):
        raise ProjectXClientError(
            "gateway temporarily unavailable",
            status_code=503,
            reason_code=PROJECTX_ERROR_NETWORK,
        )

    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", fail_fetch)
    with pytest.raises(BotRunEvaluationError, match="gateway temporarily unavailable"):
        start_bot_run(
            db_session,
            user_id=USER_A,
            bot_config_id=int(config.id),
            client=RecordingClient(),
            dry_run=False,
            confirm_live_order_routing=True,
            continuous=True,
        )
    db_session.commit()
    db_session.expire_all()

    run = db_session.query(BotRun).one()
    assert run.status == "running"
    assert run.raw_state["phase"] == "retry_wait"
    assert run.raw_state["last_transient_error"]["provider_status_code"] == 503
    assert db_session.get(BotConfig, config.id).enabled is True
    assert db_session.query(BotOrderAttempt).count() == 0


def test_retryable_error_after_durable_pending_claim_is_terminal(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    run = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
        raw_state={"live_routing_confirmed": True, "continuous": True},
    )
    db_session.add(run)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    real_require_config = bot_service._require_bot_config
    call_count = 0

    def fail_after_claim(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise ProjectXClientError(
                "gateway failed after audit claim",
                status_code=503,
                reason_code=PROJECTX_ERROR_NETWORK,
            )
        return real_require_config(*args, **kwargs)

    monkeypatch.setattr(bot_service, "_require_bot_config", fail_after_claim)
    with pytest.raises(ProjectXClientError, match="failed after audit claim"):
        evaluate_bot_config(
            db_session,
            user_id=USER_A,
            config=config,
            account=None,
            client=RecordingClient(),
            run=run,
            dry_run=False,
            confirm_live_order_routing=True,
            preserve_run_on_transient_pre_routing_error=True,
        )
    db_session.commit()
    db_session.expire_all()

    persisted_run = db_session.get(BotRun, int(run.id))
    attempt = db_session.query(BotOrderAttempt).one()
    assert persisted_run.status == "error"
    assert db_session.get(BotConfig, int(config.id)).enabled is False
    assert attempt.status == "pending"


def test_live_preflight_outage_is_transient_for_continuous_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)

    class UnavailablePreflightClient(RecordingClient):
        def search_open_positions(self, *, account_id):
            del account_id
            raise ProjectXClientError("positions unavailable", status_code=503)

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=UnavailablePreflightClient(),
        dry_run=False,
        confirm_live_order_routing=True,
        continuous=True,
    )

    assert result.status == "risk_blocked"
    assert "live_preflight_unavailable" in _risk_codes(result)
    assert result.run.status == "running"
    assert result.config.enabled is True


def test_outside_session_is_transient_for_continuous_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    monkeypatch.setattr(bot_service, "_is_inside_trading_session", lambda *_args, **_kwargs: False)
    client = RecordingClient()

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
        continuous=True,
    )

    assert result.status == "risk_blocked"
    assert "outside_session" in _risk_codes(result)
    assert result.run.status == "running"
    assert result.config.enabled is True
    assert client.place_order_calls == []


def test_max_daily_loss_is_sticky_and_terminal_for_continuous_run(db_session, monkeypatch):
    _, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    _add_mnq_tick_metadata(db_session)
    db_session.commit()
    _patch_actionable_signal(monkeypatch)
    _enable_live_test_routing(monkeypatch)
    client = RecordingClient(
        trades=[
            {
                "account_id": 9001,
                "source_trade_id": "loss-close",
                "order_id": "loss-close",
                "timestamp": datetime.now(timezone.utc),
                "pnl": -251.0,
                "fees": 0.0,
            }
        ]
    )

    result = start_bot_run(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
        continuous=True,
    )

    assert result.status == "risk_blocked"
    assert "max_daily_loss" in _risk_codes(result)
    assert result.run.status == "blocked"
    assert result.config.enabled is False
    assert client.place_order_calls == []


def test_only_one_running_live_bot_may_own_an_account(db_session):
    _, first = _add_account_and_config(db_session, execution_mode="live", name="First live bot")
    second = BotConfig(
        user_id=USER_A,
        account_id=9001,
        name="Second live bot",
        enabled=False,
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
    db_session.add(second)
    db_session.flush()
    db_session.add(
        BotRun(
            user_id=USER_A,
            bot_config_id=int(first.id),
            account_id=9001,
            status="running",
            dry_run=False,
            started_at=datetime.now(timezone.utc),
            raw_state={"live_routing_confirmed": True, "continuous": True},
        )
    )
    db_session.commit()

    with pytest.raises(ValueError, match="account_live_bot_already_running"):
        start_bot_run(
            db_session,
            user_id=USER_A,
            bot_config_id=int(second.id),
            client=RecordingClient(),
            dry_run=False,
            confirm_live_order_routing=True,
            continuous=True,
        )


def _add_unresolved_live_attempt(db_session, *, config, run, status="submission_unknown"):
    decision = BotDecision(
        user_id=USER_A,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id),
        account_id=9001,
        contract_id=CONTRACT_ID,
        decision_type="signal",
        action="BUY",
        reason="ambiguous prior submission",
        candle_timestamp=datetime.now(timezone.utc),
        quantity=1,
    )
    db_session.add(decision)
    db_session.flush()
    attempt = BotOrderAttempt(
        user_id=USER_A,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id),
        bot_decision_id=int(decision.id),
        account_id=9001,
        contract_id=CONTRACT_ID,
        execution_mode="live",
        correlation_id="ambiguous-before-emergency",
        idempotency_key="ambiguous-before-emergency",
        side="BUY",
        order_type="market",
        size=1,
        status=status,
        raw_request={"customTag": "topsignal-emergency-recovery"},
    )
    db_session.add(attempt)
    db_session.flush()
    return attempt


def test_emergency_flatten_is_account_wide_and_resolves_stale_attempts(db_session):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    second_payload = {
        column.name: getattr(config, column.name)
        for column in BotConfig.__table__.columns
        if column.name not in {"id", "created_at", "updated_at"}
    }
    second_payload.update(name="Same-account observer", enabled=True, execution_mode="dry_run")
    second_config = BotConfig(**second_payload)
    db_session.add(second_config)
    db_session.flush()
    run = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
        raw_state={"live_routing_confirmed": True, "continuous": True},
    )
    db_session.add(run)
    db_session.flush()
    second_run = BotRun(
        user_id=USER_A,
        bot_config_id=int(second_config.id),
        account_id=9001,
        status="running",
        dry_run=True,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(second_run)
    db_session.flush()
    unresolved = _add_unresolved_live_attempt(db_session, config=config, run=run)
    db_session.commit()
    client = RecordingClient(
        positions=[
            {"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0},
            {"account_id": 9001, "contract_id": "CON.F.US.MES.M26", "signed_size": -2.0},
        ],
        open_orders=[
            {"order_id": "101", "account_id": 9001, "contract_id": CONTRACT_ID, "status": 1, "signed_size": -1.0},
            {"order_id": "102", "account_id": 9001, "contract_id": "CON.F.US.MES.M26", "status": 6, "signed_size": 2.0},
        ],
    )

    result = bot_service.emergency_flatten_bot_config(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client_factory=lambda: client,
        confirm_broker_flatten=True,
    )

    assert result.status == "confirmed_account_flat"
    assert result.confirmed_flat is True
    assert result.audit["scope"] == "entire_account"
    assert {row["order_id"] for row in client.cancel_order_calls} == {"101", "102"}
    assert {row["contract_id"] for row in client.close_position_calls} == {
        CONTRACT_ID,
        "CON.F.US.MES.M26",
    }
    assert client.positions == []
    assert client.open_orders == []
    assert unresolved.status == "error"
    assert "must not be retried" in unresolved.rejection_reason
    assert int(unresolved.id) in result.audit["resolved_unresolved_live_attempt_ids"]
    assert result.run.status == "stopped"
    assert second_run.status == "stopped"
    assert second_config.enabled is False
    assert int(second_run.id) in result.audit["stopped_account_run_ids"]
    assert db_session.get(BotConfig, int(config.id)).enabled is False


def test_unconfirmed_emergency_flatten_leaves_ambiguous_attempt_unresolved(db_session):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    run = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    unresolved = _add_unresolved_live_attempt(db_session, config=config, run=run)
    db_session.commit()

    class UnconfirmedCancelClient(RecordingClient):
        def cancel_order(self, *, account_id, order_id):
            self.cancel_order_calls.append({"account_id": account_id, "order_id": order_id})
            raise ProjectXClientError(
                "cancel timed out",
                status_code=408,
                submission_outcome_unknown=True,
            )

    client = UnconfirmedCancelClient(
        positions=[{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}],
        open_orders=[
            {"order_id": "101", "account_id": 9001, "contract_id": CONTRACT_ID, "status": 1, "signed_size": -1.0}
        ],
    )
    result = bot_service.emergency_flatten_bot_config(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client=client,
        confirm_broker_flatten=True,
    )

    assert result.status == "unconfirmed"
    assert result.confirmed_flat is False
    assert result.risk_block.code == "account_working_order_cancellation_unconfirmed"
    assert unresolved.status == "submission_unknown"
    assert client.close_position_calls == []
    assert result.run.status == "stopped"
    assert config.enabled is False


def test_emergency_client_factory_failure_still_persists_local_stop(db_session):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    run = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    def fail_client_factory():
        raise ProjectXClientError("credentials unavailable", status_code=503)

    result = bot_service.emergency_flatten_bot_config(
        db_session,
        user_id=USER_A,
        bot_config_id=int(config.id),
        client_factory=fail_client_factory,
        confirm_broker_flatten=True,
    )

    assert result.status == "unconfirmed"
    assert result.risk_block.code == "emergency_broker_client_unavailable"
    assert result.run.status == "stopped"
    assert config.enabled is False


def test_account_emergency_flatten_works_without_any_bot_config(db_session, monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_LIVE_ORDER_EXECUTION", "false")
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "false")
    account = Account(
        user_id=USER_A,
        provider="projectx",
        external_id="9001",
        name="Practice 9001",
        trade_data_source="projectx",
        account_state="ACTIVE",
        can_trade=True,
        is_visible=True,
    )
    db_session.add(account)
    db_session.commit()
    client = RecordingClient(
        positions=[
            {"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1.0}
        ],
        open_orders=[
            {
                "order_id": "501",
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "status": 1,
                "signed_size": -1.0,
            }
        ],
    )

    result = bot_service.emergency_flatten_account(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client=client,
        confirm_broker_flatten=True,
    )

    assert result.confirmed_flat is True
    assert result.status == "confirmed_account_flat"
    assert result.disabled_bot_config_ids == ()
    assert result.stopped_bot_run_ids == ()
    assert client.cancel_order_calls == [{"account_id": 9001, "order_id": "501"}]
    assert client.close_position_calls == [
        {"account_id": 9001, "contract_id": CONTRACT_ID}
    ]
    db_session.expire_all()
    audit_row = db_session.get(AccountEmergencyAction, result.audit_id)
    assert audit_row is not None
    assert audit_row.account_id == 9001
    assert audit_row.status == "confirmed_account_flat"
    assert audit_row.confirmed_flat is True
    assert audit_row.result_payload["confirmed_flat"] is True


def test_account_emergency_flatten_stops_every_bot_before_client_failure(db_session):
    _, first = _add_account_and_config(
        db_session,
        execution_mode="live",
        name="First emergency bot",
    )
    second_values = {
        column.name: getattr(first, column.name)
        for column in BotConfig.__table__.columns
        if column.name not in {"id", "created_at", "updated_at"}
    }
    second_values["name"] = "Second emergency bot"
    second = BotConfig(**second_values)
    db_session.add(second)
    db_session.flush()
    first_run = BotRun(
        user_id=USER_A,
        bot_config_id=int(first.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )
    second_run = BotRun(
        user_id=USER_A,
        bot_config_id=int(second.id),
        account_id=9001,
        status="running",
        dry_run=True,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add_all([first_run, second_run])
    db_session.commit()

    def fail_after_asserting_durable_local_stop():
        db_session.expire_all()
        assert db_session.get(BotConfig, int(first.id)).enabled is False
        assert db_session.get(BotConfig, int(second.id)).enabled is False
        assert db_session.get(BotRun, int(first_run.id)).status == "stopped"
        assert db_session.get(BotRun, int(second_run.id)).status == "stopped"
        pending = db_session.query(AccountEmergencyAction).one()
        assert pending.status == "pending"
        raise ProjectXClientError("credentials unavailable", status_code=503)

    result = bot_service.emergency_flatten_account(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client_factory=fail_after_asserting_durable_local_stop,
        confirm_broker_flatten=True,
    )

    assert result.confirmed_flat is False
    assert result.status == "unconfirmed"
    assert result.risk_block.code == "emergency_broker_client_unavailable"
    assert set(result.disabled_bot_config_ids) == {int(first.id), int(second.id)}
    assert set(result.stopped_bot_run_ids) == {int(first_run.id), int(second_run.id)}
    db_session.expire_all()
    audit_row = db_session.get(AccountEmergencyAction, result.audit_id)
    assert audit_row.status == "unconfirmed"
    assert audit_row.confirmed_flat is False
    assert audit_row.risk_severity == "critical"
    assert audit_row.result_payload["status"] == "unconfirmed"


def test_account_emergency_intent_is_committed_before_waiting_for_execution_locks(
    db_session,
):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    run = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    SessionFactory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    emergency_db = SessionFactory()
    reached_execution_lock_query = Event()
    release_execution_lock_query = Event()
    outcome: dict[str, object] = {}

    def pause_before_config_lock(execute_state):
        statement = str(execute_state.statement).lower()
        if (
            execute_state.is_select
            and "from bot_configs" in statement
            and not reached_execution_lock_query.is_set()
        ):
            reached_execution_lock_query.set()
            assert release_execution_lock_query.wait(timeout=5)

    event.listen(emergency_db, "do_orm_execute", pause_before_config_lock)

    def invoke_emergency():
        try:
            outcome["result"] = bot_service.emergency_flatten_account(
                emergency_db,
                user_id=USER_A,
                account_id=9001,
                client_factory=lambda: (_ for _ in ()).throw(
                    ProjectXClientError("credentials unavailable", status_code=503)
                ),
                confirm_broker_flatten=True,
            )
        except BaseException as exc:  # surfaced on the main test thread below
            outcome["error"] = exc

    worker = Thread(target=invoke_emergency, daemon=True)
    worker.start()
    assert reached_execution_lock_query.wait(timeout=5)

    # The simulated execution-lock wait occurs before local run cleanup.  A
    # separate session must nevertheless observe the committed kill intent.
    with SessionFactory() as observer:
        pending = observer.query(AccountEmergencyAction).one()
        assert pending.status == "pending"
        assert pending.request_payload["phase"] == "intent_committed"
        assert observer.get(BotConfig, int(config.id)).enabled is True
        assert observer.get(BotRun, int(run.id)).status == "running"

    release_execution_lock_query.set()
    worker.join(timeout=5)
    event.remove(emergency_db, "do_orm_execute", pause_before_config_lock)
    emergency_db.close()

    assert worker.is_alive() is False
    assert "error" not in outcome
    result = outcome["result"]
    assert result.status == "unconfirmed"
    assert result.risk_block.code == "emergency_broker_client_unavailable"
    db_session.expire_all()
    assert db_session.get(BotConfig, int(config.id)).enabled is False
    assert db_session.get(BotRun, int(run.id)).status == "stopped"


def test_account_emergency_flatten_rejects_unowned_and_csv_accounts(db_session):
    account = Account(
        user_id=USER_B,
        provider="projectx",
        external_id="9001",
        name="Other user's account",
        trade_data_source="projectx",
        account_state="ACTIVE",
    )
    csv_account = Account(
        user_id=USER_A,
        provider="projectx",
        external_id="9002",
        name="Imported account",
        trade_data_source="csv_import",
        account_state="ACTIVE",
    )
    db_session.add_all([account, csv_account])
    db_session.commit()
    factory_calls = []

    with pytest.raises(LookupError, match="account_not_found"):
        bot_service.emergency_flatten_account(
            db_session,
            user_id=USER_A,
            account_id=9001,
            client_factory=lambda: factory_calls.append(True),
            confirm_broker_flatten=True,
        )
    db_session.rollback()
    with pytest.raises(ValueError, match="csv_import_accounts_cannot_be_emergency_flattened"):
        bot_service.emergency_flatten_account(
            db_session,
            user_id=USER_A,
            account_id=9002,
            client_factory=lambda: factory_calls.append(True),
            confirm_broker_flatten=True,
        )

    assert factory_calls == []
    assert db_session.query(AccountEmergencyAction).count() == 0


def test_pending_account_emergency_suppresses_duplicate_provider_mutations(db_session):
    _, config = _add_account_and_config(db_session, execution_mode="live")
    run = BotRun(
        user_id=USER_A,
        bot_config_id=int(config.id),
        account_id=9001,
        status="running",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )
    pending = AccountEmergencyAction(
        user_id=USER_A,
        account_id=9001,
        status="pending",
        confirmed_flat=False,
        lease_owner_id="active-owner",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        request_payload={"scope": "entire_account"},
    )
    db_session.add_all([run, pending])
    db_session.commit()
    factory_calls = []

    result = bot_service.emergency_flatten_account(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client_factory=lambda: factory_calls.append(True),
        confirm_broker_flatten=True,
    )

    assert result.confirmed_flat is False
    assert result.risk_block.code == "account_emergency_flatten_in_progress"
    assert result.audit_id == int(pending.id)
    assert result.audit["duplicate_request_suppressed"] is True
    assert factory_calls == []
    db_session.expire_all()
    assert db_session.get(BotConfig, int(config.id)).enabled is False
    assert db_session.get(BotRun, int(run.id)).status == "stopped"
    assert db_session.get(AccountEmergencyAction, int(pending.id)).status == "pending"


def test_stale_pending_account_emergency_reconciles_already_flat_without_mutation(
    db_session,
):
    _add_account_and_config(db_session, execution_mode="live")
    stale = AccountEmergencyAction(
        user_id=USER_A,
        account_id=9001,
        status="pending",
        confirmed_flat=False,
        lease_owner_id="crashed-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        attempt_count=1,
        request_payload={
            "correlationId": "crashed-request",
            # Historical payload is deliberately not a mutation replay source.
            "cancelledOrderIds": ["old-order-that-no-longer-exists"],
            "closeContractIds": ["OLD.CONTRACT"],
        },
    )
    db_session.add(stale)
    db_session.commit()
    original_audit_id = int(stale.id)
    client = RecordingClient()

    result = bot_service.emergency_flatten_account(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client=client,
        confirm_broker_flatten=True,
    )

    assert result.audit_id == original_audit_id
    assert result.confirmed_flat is True
    assert result.audit["recovered_stale_pending"] is True
    assert result.audit["reconciled_noop"] is True
    assert client.cancel_order_calls == []
    assert client.close_position_calls == []
    db_session.expire_all()
    recovered = db_session.get(AccountEmergencyAction, original_audit_id)
    assert recovered.status == "confirmed_account_flat"
    assert recovered.attempt_count == 2
    assert recovered.lease_owner_id != "crashed-worker"
    assert recovered.request_payload["previousCorrelationId"] == "crashed-request"


def test_stale_pending_account_emergency_flattens_only_current_broker_exposure(
    db_session,
):
    _add_account_and_config(db_session, execution_mode="live")
    stale = AccountEmergencyAction(
        user_id=USER_A,
        account_id=9001,
        status="pending",
        confirmed_flat=False,
        lease_owner_id="crashed-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        attempt_count=3,
        request_payload={
            "correlationId": "crashed-request",
            "cancelledOrderIds": ["stale-saved-order"],
            "closeContractIds": ["STALE.SAVED.CONTRACT"],
        },
    )
    db_session.add(stale)
    db_session.commit()
    client = RecordingClient(
        open_orders=[
            {
                "order_id": "current-order",
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "status": 1,
            }
        ],
        positions=[
            {
                "account_id": 9001,
                "contract_id": CONTRACT_ID,
                "signed_size": -2.0,
            }
        ],
    )

    result = bot_service.emergency_flatten_account(
        db_session,
        user_id=USER_A,
        account_id=9001,
        client=client,
        confirm_broker_flatten=True,
    )

    assert result.confirmed_flat is True
    assert client.cancel_order_calls == [
        {"account_id": 9001, "order_id": "current-order"}
    ]
    assert client.close_position_calls == [
        {"account_id": 9001, "contract_id": CONTRACT_ID}
    ]
    assert "stale-saved-order" not in result.audit["cancelled_order_ids"]
    assert "STALE.SAVED.CONTRACT" not in result.audit["close_contract_ids"]
    db_session.expire_all()
    recovered = db_session.get(AccountEmergencyAction, int(stale.id))
    assert recovered.status == "confirmed_account_flat"
    assert recovered.attempt_count == 4
