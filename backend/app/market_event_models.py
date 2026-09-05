"""Tenant-scoped append-only observations; registration/migrations live in app setup."""
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, Text, UniqueConstraint

from .db import Base
from .models import USER_ID_TYPE


class MarketEventVersion(Base):
    __tablename__ = "market_event_versions"
    id = Column(Text, primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(USER_ID_TYPE, nullable=False)
    source = Column(Text, nullable=False)
    source_event_id = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    country = Column(Text, nullable=False)
    importance = Column(Text, nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_end_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_date = Column(Text, nullable=True)
    time_precision = Column(Text, nullable=False, default="exact")
    published_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    available_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(Text, nullable=False)
    actual = Column(Text, nullable=True)
    forecast = Column(Text, nullable=True)
    previous = Column(Text, nullable=True)
    revised = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    raw_fields = Column(JSON, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_event_id", "observed_at", name="uq_market_event_observation"),
        Index("ix_market_event_asof", "user_id", "source", "available_at"),
        Index("ix_market_event_identity", "user_id", "source", "source_event_id", "observed_at"),
    )


class MarketEventSourceSnapshot(Base):
    __tablename__ = "market_event_source_snapshots"
    id = Column(Text, primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(USER_ID_TYPE, nullable=False)
    source = Column(Text, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(Text, nullable=False)
    error_code = Column(Text, nullable=True)
    event_count = Column(Integer, nullable=False, default=0)
    coverage_start = Column(DateTime(timezone=True), nullable=True)
    coverage_end = Column(DateTime(timezone=True), nullable=True)
    coverage_complete = Column(Boolean, nullable=False, default=False)
    __table_args__ = (Index("ix_market_event_source_asof", "user_id", "source", "observed_at"),)
