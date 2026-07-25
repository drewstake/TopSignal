from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_

from ..models import ProjectXTradeEvent
from .trading_day import as_utc, trading_day_date


def trade_event_range_filters(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Any]:
    """Build mixed-source range filters for provider and imported trade rows.

    Imported rows carry Topstep's authoritative ``trade_date`` and are filtered
    at trading-day granularity. Provider rows do not carry that field, so they
    retain the existing exact timestamp bounds.
    """

    filters: list[Any] = []
    if start is not None:
        start_utc = as_utc(start)
        start_trade_date = trading_day_date(start_utc)
        filters.append(
            or_(
                and_(
                    ProjectXTradeEvent.trade_date.isnot(None),
                    ProjectXTradeEvent.trade_date >= start_trade_date,
                ),
                and_(
                    ProjectXTradeEvent.trade_date.is_(None),
                    ProjectXTradeEvent.trade_timestamp >= start_utc,
                ),
            )
        )
    if end is not None:
        end_utc = as_utc(end)
        end_trade_date = trading_day_date(end_utc)
        filters.append(
            or_(
                and_(
                    ProjectXTradeEvent.trade_date.isnot(None),
                    ProjectXTradeEvent.trade_date <= end_trade_date,
                ),
                and_(
                    ProjectXTradeEvent.trade_date.is_(None),
                    ProjectXTradeEvent.trade_timestamp <= end_utc,
                ),
            )
        )
    return filters
