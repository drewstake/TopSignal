"""Bounded research records; deliberately separate from execution state."""
from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Index, Integer, Numeric, Text, UniqueConstraint

from .db import Base
from .models import USER_ID_TYPE


class MarketObservation(Base):
    __tablename__ = "market_observations"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(USER_ID_TYPE, nullable=False)
    contract_id = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    provider_timestamp = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    fingerprint = Column(Text, nullable=False)
    price = Column(Numeric(24, 8), nullable=True)
    size = Column(Numeric(24, 8), nullable=True)
    side = Column(Text, nullable=True)
    bid = Column(Numeric(24, 8), nullable=True)
    ask = Column(Numeric(24, 8), nullable=True)
    details = Column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_market_observation_fingerprint"),
        Index("ix_market_observation_owner_time", "user_id", "received_at"),
        Index("ix_market_observation_contract_time", "user_id", "contract_id", "received_at"),
    )


class DecisionResearchSnapshot(Base):
    __tablename__ = "decision_research_snapshots"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id = Column(USER_ID_TYPE, nullable=False)
    decision_id = Column(BigInteger, nullable=False)
    account_id = Column(BigInteger, nullable=False)
    contract_id = Column(Text, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    candle_source = Column(Text, nullable=False, default="projectx")
    candle_live = Column(Boolean, nullable=False, default=False)
    signal_timestamp = Column(DateTime(timezone=True), nullable=True)
    action = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    direction = Column(Text, nullable=True)
    entry_price = Column(Numeric(24, 8), nullable=True)
    stop_loss = Column(Numeric(24, 8), nullable=True)
    take_profit = Column(Numeric(24, 8), nullable=True)
    score = Column(Integer, nullable=True)
    snapshot_version = Column(Text, nullable=False)
    snapshot_hash = Column(Text, nullable=False)
    snapshot = Column(JSON, nullable=False)
    outcome = Column(Text, nullable=False)
    outcome_at = Column(DateTime(timezone=True), nullable=True)
    outcome_details = Column(JSON, nullable=True)
    routing = Column(JSON, nullable=True)
    __table_args__ = (
        UniqueConstraint("user_id", "decision_id", name="uq_decision_research_owner_decision"),
        Index("ix_decision_research_owner_account", "user_id", "account_id", "observed_at"),
        Index("ix_decision_research_pending", "user_id", "outcome", "observed_at"),
    )
