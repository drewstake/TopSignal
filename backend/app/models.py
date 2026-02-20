from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from .db import Base

class Account(Base):
    __tablename__ = "accounts"

    id = Column(BigInteger, primary_key=True)
    provider = Column(Text, nullable=False)
    external_id = Column(Text, nullable=False)
    name = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_accounts_provider_external_id"),
    )

class Trade(Base):
    __tablename__ = "trades"

    id = Column(BigInteger, primary_key=True)
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

    id = Column(BigInteger, primary_key=True)
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
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("side in ('BUY','SELL','UNKNOWN')", name="projectx_trade_events_side_check"),
        UniqueConstraint(
            "account_id",
            "order_id",
            "trade_timestamp",
            name="uq_projectx_trade_events_account_order_ts",
        ),
    )
