"""Detect backend event-loop stalls that would delay every live stream.

A stalled loop stops SSE heartbeats, provider pings and request handling at
once. Logging the stall separates backend delays from provider (ProjectX) or
browser/network problems when a viewer reports a stale feed. It only measures
timer lateness; it performs no I/O and never touches market data.
"""
from __future__ import annotations

import asyncio
import logging

from ..bot_diagnostics import event as diagnostic_event

logger = logging.getLogger(__name__)


async def monitor_event_loop_lag(
    *,
    interval_seconds: float = 1.0,
    stall_threshold_seconds: float = 1.0,
    report_interval_seconds: float = 30.0,
) -> None:
    loop = asyncio.get_running_loop()
    worst_lag = 0.0
    stalls = 0
    last_report = float("-inf")
    while True:
        started = loop.time()
        await asyncio.sleep(interval_seconds)
        lag = loop.time() - started - interval_seconds
        if lag >= stall_threshold_seconds:
            stalls += 1
            worst_lag = max(worst_lag, lag)
        now = loop.time()
        # The first stall reports at once; later ones are summarized at most
        # once per window, including stalls seen just after a report.
        if not stalls or now - last_report < report_interval_seconds:
            continue
        diagnostic_event(
            logger, "event_loop_stall", level=logging.WARNING,
            worst_lag_ms=round(worst_lag * 1000), stalls=stalls,
            window_seconds=None if last_report == float("-inf") else round(now - last_report),
        )
        last_report = now
        worst_lag = 0.0
        stalls = 0
