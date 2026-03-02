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
from sqlalchemy.dialects.postgresql import ARRAY
from .db import Base

class Account(Base):
    __tablename__ = "accounts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    provider = Column(Text, nullable=False)
    external_id = Column(Text, nullable=False)
    name = Column(Text, nullable=True)
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
        UniqueConstraint("provider", "external_id", name="uq_accounts_provider_external_id"),
        Index("idx_accounts_is_main", "is_main"),
        Index("idx_accounts_account_state", "account_state"),
    )

class Trade(Base):
    __tablename__ = "trades"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
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


class ProjectXTradeEvent(Base):
    __tablename__ = "projectx_trade_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
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
            "account_id",
            "source_trade_id",
            name="uq_projectx_trade_events_account_source_trade",
        ),
        UniqueConstraint(
            "account_id",
            "order_id",
            "trade_timestamp",
            name="uq_projectx_trade_events_account_order_ts",
        ),
    )


class ProjectXTradeDaySync(Base):
    __tablename__ = "projectx_trade_day_syncs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id = Column(BigInteger, nullable=False)
    trade_date = Column(Date, nullable=False)
    sync_status = Column(Text, nullable=False, server_default="partial")
    last_synced_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    row_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("sync_status in ('partial','complete')", name="projectx_trade_day_syncs_status_check"),
        UniqueConstraint("account_id", "trade_date", name="uq_projectx_trade_day_syncs_account_date"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
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
        UniqueConstraint("account_id", "entry_date", name="uq_journal_entries_account_entry_date"),
    )


class JournalEntryImage(Base):
    __tablename__ = "journal_entry_images"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
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
        Index("idx_journal_entry_images_account_date", "account_id", "entry_date"),
        Index("idx_journal_entry_images_journal_entry", "journal_entry_id"),
    )


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
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
        Index("idx_expenses_expense_date", "expense_date"),
        Index("idx_expenses_account_id", "account_id"),
        Index("idx_expenses_category", "category"),
        Index(
            "uq_expenses_dedupe",
            "expense_date",
            "category",
            func.coalesce(account_type, ""),
            func.coalesce(plan_size, ""),
            func.coalesce(account_id, 0),
            "amount_cents",
            unique=True,
        ),
    )
