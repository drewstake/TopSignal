from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .projectx_order_book import (
    ProjectXMarketDepthSession,
    _depth_entries,
    _json_number,
    _parse_decimal,
    _parse_timestamp,
)
from .trading_day import futures_session_is_open


class ProjectXMarketPriceSession(ProjectXMarketDepthSession):
    """Reuse the tenant-scoped hub lifecycle for display-only last-trade prices.

    Provider contract trade events: https://gateway.docs.projectx.com/docs/realtime/
    No account subscriptions, order routing, or tick persistence is involved.
    """

    _subscribe_target = "SubscribeContractTrades"
    _unsubscribe_target = "UnsubscribeContractTrades"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_prices: dict[str, tuple] = {}

    def _observe(self, method: str, **kwargs: Any) -> None:
        # The price viewer must not activate research capture or persistence.
        pass

    async def _send_observation_subscription_locked(self, *args, **kwargs) -> None:
        # Trades are this session's primary subscription, never a second one.
        pass

    async def unsubscribe(self, contract_id, queue) -> None:
        await super().unsubscribe(contract_id, queue)
        async with self._lock:
            if contract_id not in self._channels:
                self._last_prices.pop(contract_id, None)

    async def _clear_books_for_reconnect(self) -> None:
        await super()._clear_books_for_reconnect()
        self._last_prices.clear()

    def _broadcast_to_channel_locked(self, channel, event) -> None:
        # Prices are absolute values. A slow viewer needs the latest value,
        # rather than a depth-book snapshot or an unbounded tick backlog.
        for queue in tuple(channel.subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
            queue.put_nowait(event)

    async def process_signalr_frame(self, frame: Mapping[str, Any], *, connection_generation: int | None = None) -> None:
        if frame.get("type") != 1:
            await super().process_signalr_frame(frame, connection_generation=connection_generation)
            return
        if str(frame.get("target") or "").casefold() != "gatewaytrade":
            return
        arguments = frame.get("arguments")
        if not isinstance(arguments, list) or len(arguments) < 2 or not isinstance(arguments[0], str):
            return
        contract_id = arguments[0].strip()
        async with self._send_lock:
            if connection_generation is not None and (
                self._websocket is None or self._connection_generation != connection_generation
            ):
                return
            async with self._lock:
                channel = self._channels.get(contract_id)
                if channel is None or not futures_session_is_open(self._now(), symbol=contract_id):
                    return
                for entry in _depth_entries(arguments[1]):
                    price = _parse_decimal(entry.get("price"))
                    timestamp = _parse_timestamp(entry.get("timestamp"))
                    if price is None or price <= 0 or timestamp is None:
                        continue
                    previous = self._last_prices.get(contract_id)
                    if previous and (timestamp < previous[0] or (timestamp, price) == previous):
                        continue
                    self._last_prices[contract_id] = (timestamp, price)
                    self._broadcast_to_channel_locked(channel, {"event": "price", "data": {
                        "contract_id": contract_id,
                        "symbol": entry.get("symbolId") if isinstance(entry.get("symbolId"), str) else None,
                        "price": _json_number(price),
                        "timestamp": timestamp.isoformat(),
                    }})


async def stream_market_prices(request, subscription, *, interval_seconds: float):
    """Bound display cadence and release the subscription on every exit path."""
    try:
        for event in subscription.initial_events:
            if event.get("event") == "state":
                yield event
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield None  # SSE keepalive
                continue
            if event.get("event") == "snapshot":
                continue
            if event.get("event") == "price":
                await asyncio.sleep(interval_seconds)
                # Drain only the bounded current backlog, retaining its newest
                # state/price. A disconnect must supersede a queued old price.
                for _ in range(subscription.queue.qsize()):
                    candidate = subscription.queue.get_nowait()
                    if candidate.get("event") in {"price", "state"}:
                        event = candidate
            yield event
    finally:
        await subscription.close()
