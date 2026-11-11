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

# Viewers judge feed health from these; they carry observations, never prices.
DEFAULT_PRICE_HEARTBEAT_SECONDS = 5.0


class ProjectXMarketPriceSession(ProjectXMarketDepthSession):
    """Reuse the tenant-scoped hub lifecycle for display-only last-trade prices.

    Provider contract trade events: https://gateway.docs.projectx.com/docs/realtime/
    No account subscriptions, order routing, or tick persistence is involved.
    """

    _subscribe_target = "SubscribeContractTrades"
    _unsubscribe_target = "UnsubscribeContractTrades"
    _stream_kind = "price"
    _uptime_proves_health = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_prices: dict[str, tuple] = {}
        self._last_price_events: dict[str, dict[str, Any]] = {}

    def _observe(self, method: str, **kwargs: Any) -> None:
        # The price viewer must not activate research capture or persistence.
        pass

    async def _send_observation_subscription_locked(self, *args, **kwargs) -> None:
        # Trades are this session's primary subscription, never a second one.
        pass

    async def _unsubscribe(self, contract_id, queue, **kwargs) -> None:
        # Covers both immediate removal and disconnect-grace expiry. A contract
        # that is no longer subscribed must not replay an old last trade later.
        await super()._unsubscribe(contract_id, queue, **kwargs)
        async with self._lock:
            if contract_id not in self._channels:
                self._last_prices.pop(contract_id, None)
                self._last_price_events.pop(contract_id, None)

    async def _clear_books_for_reconnect(self) -> None:
        await super()._clear_books_for_reconnect()
        self._last_prices.clear()
        self._last_price_events.clear()

    def last_price_event(self, contract_id: str) -> dict[str, Any] | None:
        """The newest provider trade seen on the current, unbroken connection.

        It keeps its provider timestamp, so a resuming viewer can display the
        real last trade immediately and judge its age itself.
        """
        if self._websocket is None or not futures_session_is_open(self._now(), symbol=contract_id):
            return None
        return self._last_price_events.get(contract_id)

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
                    # A valid trade for a subscribed contract proves this hub
                    # connection delivers market data; its loss retries promptly.
                    self._healthy_connection_generation = self._connection_generation
                    previous = self._last_prices.get(contract_id)
                    if previous and (timestamp < previous[0] or (timestamp, price) == previous):
                        continue
                    self._last_prices[contract_id] = (timestamp, price)
                    event = {"event": "price", "data": {
                        "contract_id": contract_id,
                        "symbol": entry.get("symbolId") if isinstance(entry.get("symbolId"), str) else None,
                        "price": _json_number(price),
                        "timestamp": timestamp.isoformat(),
                    }}
                    self._last_price_events[contract_id] = event
                    self._broadcast_to_channel_locked(channel, event)


def _heartbeat_event(subscription) -> dict[str, Any] | None:
    status = getattr(subscription.session, "feed_status", None)
    if not callable(status):
        return None
    return {"event": "heartbeat", "data": status(subscription.contract_id)}


async def stream_market_prices(
    request,
    subscription,
    *,
    interval_seconds: float,
    heartbeat_seconds: float = DEFAULT_PRICE_HEARTBEAT_SECONDS,
):
    """Bound display cadence and release the subscription on every exit path.

    Heartbeats arrive on a fixed cadence even while prices flow, so a viewer
    can tell a quiet market (heartbeats, no trades) from a broken path (no
    bytes at all) or a provider interruption (heartbeat state/idle time).
    """
    loop = asyncio.get_running_loop()
    heartbeat_seconds = max(0.05, float(heartbeat_seconds))
    try:
        for event in subscription.initial_events:
            if event.get("event") == "state":
                yield event
        replay = getattr(subscription.session, "last_price_event", None)
        last_price = replay(subscription.contract_id) if callable(replay) else None
        if last_price is not None:
            yield last_price
        next_heartbeat = loop.time() + heartbeat_seconds
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(
                    subscription.queue.get(), timeout=max(0.0, next_heartbeat - loop.time()),
                )
            except asyncio.TimeoutError:
                next_heartbeat = loop.time() + heartbeat_seconds
                yield _heartbeat_event(subscription)  # None falls back to an SSE comment
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
            if loop.time() >= next_heartbeat:
                next_heartbeat = loop.time() + heartbeat_seconds
                heartbeat = _heartbeat_event(subscription)
                if heartbeat is not None:
                    yield heartbeat
    finally:
        await subscription.close()
