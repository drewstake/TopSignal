from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from .db import Base

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"
USER_ID_TYPE = UUID(as_uuid=False).with_variant(Text, "sqlite")


class Account(Base):
    __tablename__ = "accounts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    provider = Column(Text, nullable=False)
    external_id = Column(Text, nullable=False)
    name = Column(Text, nullable=True)
    display_name = Column(Text, nullable=True)
    account_state = Column(Text, nullable=False, server_default="ACTIVE")
    can_trade = Column(Boolean, nullable=True)
    is_visible = Column(Boolean, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_missing_at = Column(DateTime(timezone=True), nullable=True)
    is_main = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "account_state in ('ACTIVE','LOCKED_OUT','HIDDEN','MISSING')",
            name="accounts_account_state_check",
        ),
        UniqueConstraint("user_id", "provider", "external_id", name="uq_accounts_provider_external_id"),
        Index("idx_accounts_is_main", "user_id", "is_main"),
        Index("idx_accounts_account_state", "user_id", "account_state"),
    )

class Trade(Base):
    __tablename__ = "trades"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, ForeignKey("accounts.id"), nullable=False)

    symbol = Column(Text, nullable=False)
    side = Column(Text, nullable=False)

    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    qty = Column(Numeric(18, 6), nullable=False)
    entry_price = Column(Numeric(18, 6), nullable=False)
    exit_price = Column(Numeric(18, 6), nullable=True)

    pnl = Column(Numeric(18, 2), nullable=True)
    fees = Column(Numeric(18, 2), nullable=True)
    notes = Column(Text, nullable=True)
    is_rule_break = Column(Boolean, nullable=False, server_default="false")
    rule_break_type = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("side in ('LONG','SHORT')", name="trades_side_check"),
    )


class InstrumentMetadata(Base):
    __tablename__ = "instrument_metadata"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    symbol = Column(Text, nullable=False, unique=True)
    tick_size = Column(Numeric(18, 6), nullable=False)
    tick_value = Column(Numeric(18, 6), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        CheckConstraint("tick_size > 0", name="instrument_metadata_tick_size_positive_check"),
        CheckConstraint("tick_value > 0", name="instrument_metadata_tick_value_positive_check"),
    )


class ProjectXTradeEvent(Base):
    __tablename__ = "projectx_trade_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, nullable=False)
    contract_id = Column(Text, nullable=False)
    symbol = Column(Text, nullable=True)
    side = Column(Text, nullable=False)
    size = Column(Numeric(18, 6), nullable=False)
    price = Column(Numeric(18, 6), nullable=False)
    trade_timestamp = Column(DateTime(timezone=True), nullable=False)
    fees = Column(Numeric(18, 6), nullable=False, server_default="0")
    pnl = Column(Numeric(18, 6), nullable=True)
    order_id = Column(Text, nullable=False)
    source_trade_id = Column(Text, nullable=True)
    status = Column(Text, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("side in ('BUY','SELL','UNKNOWN')", name="projectx_trade_events_side_check"),
        UniqueConstraint(
            "user_id",
            "account_id",
            "source_trade_id",
            name="uq_projectx_trade_events_account_source_trade",
        ),
        UniqueConstraint(
            "user_id",
            "account_id",
            "order_id",
            "trade_timestamp",
            name="uq_projectx_trade_events_account_order_ts",
        ),
    )


class ProjectXTradeDaySync(Base):
    __tablename__ = "projectx_trade_day_syncs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, nullable=False)
    trade_date = Column(Date, nullable=False)
    sync_status = Column(Text, nullable=False, server_default="partial")
    last_synced_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    row_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("sync_status in ('partial','complete')", name="projectx_trade_day_syncs_status_check"),
        UniqueConstraint("user_id", "account_id", "trade_date", name="uq_projectx_trade_day_syncs_account_date"),
    )


class ProjectXMarketCandle(Base):
    __tablename__ = "projectx_market_candles"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    contract_id = Column(Text, nullable=False)
    symbol = Column(Text, nullable=True)
    live = Column(Boolean, nullable=False, server_default="false")
    unit = Column(Text, nullable=False)
    unit_number = Column(Integer, nullable=False)
    candle_timestamp = Column(DateTime(timezone=True), nullable=False)
    open_price = Column(Numeric(18, 6), nullable=False)
    high_price = Column(Numeric(18, 6), nullable=False)
    low_price = Column(Numeric(18, 6), nullable=False)
    close_price = Column(Numeric(18, 6), nullable=False)
    volume = Column(Numeric(18, 6), nullable=False, server_default="0")
    is_partial = Column(Boolean, nullable=False, server_default="false")
    source = Column(Text, nullable=False, server_default="projectx")
    raw_payload = Column(JSON, nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("unit in ('second','minute','hour','day','week','month')", name="projectx_market_candles_unit_check"),
        CheckConstraint("unit_number > 0", name="projectx_market_candles_unit_number_positive_check"),
        UniqueConstraint(
            "user_id",
            "contract_id",
            "live",
            "unit",
            "unit_number",
            "candle_timestamp",
            name="uq_projectx_market_candles_contract_timeframe",
        ),
        Index(
            "idx_projectx_market_candles_contract_ts",
            "user_id",
            "contract_id",
            "live",
            "unit",
            "unit_number",
            "candle_timestamp",
        ),
    )


class BotConfig(Base):
    __tablename__ = "bot_configs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, nullable=False)
    name = Column(Text, nullable=False)
    provider = Column(Text, nullable=False, server_default="projectx")
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    execution_mode = Column(Text, nullable=False, default="dry_run", server_default="dry_run")
    strategy_type = Column(Text, nullable=False, default="sma_cross", server_default="sma_cross")
    contract_id = Column(Text, nullable=False)
    symbol = Column(Text, nullable=True)
    timeframe_unit = Column(Text, nullable=False, default="minute", server_default="minute")
    timeframe_unit_number = Column(Integer, nullable=False, default=5, server_default="5")
    lookback_bars = Column(Integer, nullable=False, default=200, server_default="200")
    fast_period = Column(Integer, nullable=False, default=9, server_default="9")
    slow_period = Column(Integer, nullable=False, default=21, server_default="21")
    order_size = Column(Numeric(18, 6), nullable=False, default=1, server_default="1")
    max_contracts = Column(Numeric(18, 6), nullable=False, default=1, server_default="1")
    max_daily_loss = Column(Numeric(18, 6), nullable=False, default=250, server_default="250")
    max_trades_per_day = Column(Integer, nullable=False, default=3, server_default="3")
    max_open_position = Column(Numeric(18, 6), nullable=False, default=1, server_default="1")
    allowed_contracts = Column(JSON, nullable=False, server_default=text("'[]'"))
    trading_start_time = Column(Text, nullable=False, default="09:30", server_default="09:30")
    trading_end_time = Column(Text, nullable=False, default="15:45", server_default="15:45")
    cooldown_seconds = Column(Integer, nullable=False, default=300, server_default="300")
    max_data_staleness_seconds = Column(Integer, nullable=False, default=600, server_default="600")
    allow_market_depth = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        CheckConstraint("execution_mode in ('dry_run','live')", name="bot_configs_execution_mode_check"),
        CheckConstraint("strategy_type in ('sma_cross')", name="bot_configs_strategy_type_check"),
        CheckConstraint(
            "timeframe_unit in ('second','minute','hour','day','week','month')",
            name="bot_configs_timeframe_unit_check",
        ),
        CheckConstraint("timeframe_unit_number > 0", name="bot_configs_timeframe_unit_number_positive_check"),
        CheckConstraint("lookback_bars >= 25", name="bot_configs_lookback_bars_min_check"),
        CheckConstraint("fast_period > 0", name="bot_configs_fast_period_positive_check"),
        CheckConstraint("slow_period > fast_period", name="bot_configs_slow_period_gt_fast_check"),
        CheckConstraint("order_size > 0", name="bot_configs_order_size_positive_check"),
        CheckConstraint("max_contracts > 0", name="bot_configs_max_contracts_positive_check"),
        CheckConstraint("max_daily_loss >= 0", name="bot_configs_max_daily_loss_nonnegative_check"),
        CheckConstraint("max_trades_per_day >= 0", name="bot_configs_max_trades_per_day_nonnegative_check"),
        CheckConstraint("max_open_position > 0", name="bot_configs_max_open_position_positive_check"),
        CheckConstraint("cooldown_seconds >= 0", name="bot_configs_cooldown_seconds_nonnegative_check"),
        CheckConstraint("max_data_staleness_seconds > 0", name="bot_configs_data_staleness_positive_check"),
        Index("idx_bot_configs_user_account", "user_id", "account_id"),
        Index("idx_bot_configs_user_enabled", "user_id", "enabled"),
    )


class BotRun(Base):
    __tablename__ = "bot_runs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    bot_config_id = Column(BigInteger, ForeignKey("bot_configs.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    status = Column(Text, nullable=False, default="running", server_default="running")
    dry_run = Column(Boolean, nullable=False, default=True, server_default="true")
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    stop_reason = Column(Text, nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    raw_state = Column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint("status in ('running','stopped','blocked','error')", name="bot_runs_status_check"),
        Index("idx_bot_runs_config_started", "user_id", "bot_config_id", "started_at"),
        Index("idx_bot_runs_account_status", "user_id", "account_id", "status"),
    )


class BotDecision(Base):
    __tablename__ = "bot_decisions"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    bot_config_id = Column(BigInteger, ForeignKey("bot_configs.id", ondelete="CASCADE"), nullable=False)
    bot_run_id = Column(BigInteger, ForeignKey("bot_runs.id", ondelete="SET NULL"), nullable=True)
    account_id = Column(BigInteger, nullable=False)
    contract_id = Column(Text, nullable=False)
    symbol = Column(Text, nullable=True)
    decision_type = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    candle_timestamp = Column(DateTime(timezone=True), nullable=True)
    price = Column(Numeric(18, 6), nullable=True)
    quantity = Column(Numeric(18, 6), nullable=True)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "decision_type in ('signal','risk_reject','order_attempt','lifecycle')",
            name="bot_decisions_type_check",
        ),
        CheckConstraint("action in ('BUY','SELL','HOLD','NONE','STOP')", name="bot_decisions_action_check"),
        Index("idx_bot_decisions_config_created", "user_id", "bot_config_id", "created_at"),
    )


class BotOrderAttempt(Base):
    __tablename__ = "bot_order_attempts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    bot_config_id = Column(BigInteger, ForeignKey("bot_configs.id", ondelete="CASCADE"), nullable=False)
    bot_run_id = Column(BigInteger, ForeignKey("bot_runs.id", ondelete="SET NULL"), nullable=True)
    bot_decision_id = Column(BigInteger, ForeignKey("bot_decisions.id", ondelete="SET NULL"), nullable=True)
    account_id = Column(BigInteger, nullable=False)
    contract_id = Column(Text, nullable=False)
    side = Column(Text, nullable=False)
    order_type = Column(Text, nullable=False, default="market", server_default="market")
    size = Column(Numeric(18, 6), nullable=False)
    limit_price = Column(Numeric(18, 6), nullable=True)
    stop_price = Column(Numeric(18, 6), nullable=True)
    trail_price = Column(Numeric(18, 6), nullable=True)
    status = Column(Text, nullable=False, default="pending", server_default="pending")
    provider_order_id = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    raw_request = Column(JSON, nullable=True)
    raw_response = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        CheckConstraint("side in ('BUY','SELL')", name="bot_order_attempts_side_check"),
        CheckConstraint("order_type in ('market','limit','stop','trailing_stop')", name="bot_order_attempts_order_type_check"),
        CheckConstraint(
            "status in ('pending','dry_run','submitted','blocked','rejected','error')",
            name="bot_order_attempts_status_check",
        ),
        CheckConstraint("size > 0", name="bot_order_attempts_size_positive_check"),
        Index("idx_bot_order_attempts_config_created", "user_id", "bot_config_id", "created_at"),
        Index("idx_bot_order_attempts_account_created", "user_id", "account_id", "created_at"),
    )


class BotRiskEvent(Base):
    __tablename__ = "bot_risk_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    bot_config_id = Column(BigInteger, ForeignKey("bot_configs.id", ondelete="CASCADE"), nullable=False)
    bot_run_id = Column(BigInteger, ForeignKey("bot_runs.id", ondelete="SET NULL"), nullable=True)
    account_id = Column(BigInteger, nullable=False)
    severity = Column(Text, nullable=False, default="warning", server_default="warning")
    code = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("severity in ('info','warning','critical')", name="bot_risk_events_severity_check"),
        Index("idx_bot_risk_events_config_created", "user_id", "bot_config_id", "created_at"),
    )


class PositionLifecycle(Base):
    __tablename__ = "position_lifecycles"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, nullable=False)
    contract_id = Column(Text, nullable=False)
    symbol = Column(Text, nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=False)
    side = Column(Text, nullable=False)
    max_qty = Column(Numeric(18, 6), nullable=False)
    avg_entry_at_open = Column(Numeric(18, 6), nullable=True)
    realized_pnl_usd = Column(Numeric(18, 6), nullable=True)
    mae_usd = Column(Numeric(18, 6), nullable=True)
    mfe_usd = Column(Numeric(18, 6), nullable=True)
    mae_points = Column(Numeric(18, 6), nullable=True)
    mfe_points = Column(Numeric(18, 6), nullable=True)
    mae_timestamp = Column(DateTime(timezone=True), nullable=True)
    mfe_timestamp = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("side in ('LONG','SHORT')", name="position_lifecycles_side_check"),
        CheckConstraint("max_qty > 0", name="position_lifecycles_max_qty_positive_check"),
        Index("idx_position_lifecycles_account_opened", "user_id", "account_id", "opened_at"),
        Index("idx_position_lifecycles_contract_opened", "user_id", "contract_id", "opened_at"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, nullable=False)
    entry_date = Column(Date, nullable=False)
    title = Column(Text, nullable=False)
    mood = Column(Text, nullable=False)
    tags = Column(
        ARRAY(Text).with_variant(JSON, "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    body = Column(Text, nullable=False, server_default=text("''"))
    version = Column(Integer, nullable=False, server_default="1")
    stats_source = Column(Text, nullable=True)
    stats_json = Column(JSON, nullable=True)
    stats_pulled_at = Column(DateTime(timezone=True), nullable=True)
    is_archived = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "mood in ('Focused','Neutral','Frustrated','Confident')",
            name="journal_entries_mood_check",
        ),
        UniqueConstraint("user_id", "account_id", "entry_date", name="uq_journal_entries_account_entry_date"),
    )


class JournalEntryImage(Base):
    __tablename__ = "journal_entry_images"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    journal_entry_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id = Column(BigInteger, nullable=False)
    entry_date = Column(Date, nullable=False)
    filename = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    byte_size = Column(Integer, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_journal_entry_images_account_date", "user_id", "account_id", "entry_date"),
        Index("idx_journal_entry_images_journal_entry", "user_id", "journal_entry_id"),
    )


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    provider = Column(Text, nullable=False)
    username_encrypted = Column(Text, nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_credentials_user_provider"),
        Index("idx_provider_credentials_user_provider", "user_id", "provider"),
    )


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    account_id = Column(BigInteger, nullable=True)
    provider = Column(Text, nullable=False, server_default="topstep")
    expense_date = Column(Date, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(Text, nullable=False, server_default="USD")
    category = Column(Text, nullable=False)
    account_type = Column(Text, nullable=True)
    plan_size = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(
        ARRAY(Text).with_variant(JSON, "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="expenses_amount_cents_nonnegative_check"),
        CheckConstraint(
            "category in ('evaluation_fee', 'activation_fee', 'reset_fee', 'data_fee', 'other')",
            name="expenses_category_check",
        ),
        CheckConstraint(
            "account_type in ('no_activation', 'standard', 'practice')",
            name="expenses_account_type_check",
        ),
        CheckConstraint(
            "plan_size in ('50k', '100k', '150k')",
            name="expenses_plan_size_check",
        ),
        Index("idx_expenses_expense_date", "user_id", "expense_date"),
        Index("idx_expenses_account_id", "user_id", "account_id"),
        Index("idx_expenses_category", "user_id", "category"),
        Index(
            "uq_expenses_dedupe",
            "user_id",
            "expense_date",
            "category",
            func.coalesce(account_type, ""),
            func.coalesce(plan_size, ""),
            func.coalesce(account_id, 0),
            "amount_cents",
            unique=True,
        ),
    )


class Payout(Base):
    __tablename__ = "payouts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(
        USER_ID_TYPE,
        nullable=False,
        server_default=text(f"'{DEFAULT_USER_ID}'"),
    )
    payout_date = Column(Date, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(Text, nullable=False, server_default="USD")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now())

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="payouts_amount_cents_positive_check"),
        Index("idx_payouts_payout_date", "user_id", "payout_date"),
    )
