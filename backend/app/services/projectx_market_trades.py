"""Read-only, non-persistent trade prints for the liquidity heatmap overlay."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any, Mapping
from uuid import uuid4

from .projectx_market_price import ProjectXMarketPriceSession
from .projectx_order_book import _depth_entries, _json_number, _parse_decimal, _parse_timestamp
from .trading_day import futures_session_is_open


class ProjectXMarketTradesSession(ProjectXMarketPriceSession):
    # Inherit only the authenticated, reference-counted trades hub lifecycle.
    # No research hooks, price coalescing, account subscriptions or bot callbacks.
    _stream_kind = "trades"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._print_epoch = uuid4().hex
        self._print_sequence = 0
        self._seen_ids: OrderedDict[tuple[str, str], None] = OrderedDict()

    def _broadcast_to_channel_locked(self, channel, event) -> None:
        if event.get("event") == "snapshot":
            return
        for queue in tuple(channel.subscribers):
            if queue.full():
                dropped = 0
                while not queue.empty():
                    old = queue.get_nowait()
                    dropped += 1 if old.get("event") == "trade" else old.get("data", {}).get("dropped", 0)
                queue.put_nowait({"event": "trade_gap", "data": {"dropped": dropped}})
            queue.put_nowait(event)

    async def process_signalr_frame(self, frame: Mapping[str, Any], *, connection_generation: int | None = None) -> None:
        if frame.get("type") != 1 or str(frame.get("target") or "").casefold() != "gatewaytrade":
            await super().process_signalr_frame(frame, connection_generation=connection_generation)
            return
        arguments = frame.get("arguments")
        if not isinstance(arguments, list) or len(arguments) < 2 or not isinstance(arguments[0], str):
            return
        contract = arguments[0].strip()
        async with self._send_lock:
            if connection_generation is not None and (self._websocket is None or self._connection_generation != connection_generation):
                return
            async with self._lock:
                channel = self._channels.get(contract)
                if channel is None or not futures_session_is_open(self._now(), symbol=contract):
                    return
                for entry in _depth_entries(arguments[1]):
                    price, volume = _parse_decimal(entry.get("price")), _parse_decimal(entry.get("volume"))
                    timestamp = _parse_timestamp(entry.get("timestamp"))
                    if price is None or price <= 0 or volume is None or volume <= 0 or timestamp is None:
                        continue
                    # Reject ambiguous timestamps; preserve provider event time.
                    raw_time = str(entry.get("timestamp", ""))
                    if not (raw_time.endswith("Z") or "+" in raw_time[10:] or "-" in raw_time[10:]):
                        continue
                    if _json_number(price) is None or _json_number(volume) is None:
                        continue
                    # A valid print proves this connection delivers data, so
                    # its loss restarts the reconnect backoff ladder.
                    self._healthy_connection_generation = self._connection_generation
                    native_id = entry.get("tradeId")
                    if isinstance(native_id, (str, int)) and not isinstance(native_id, bool) and str(native_id).strip():
                        key = (contract, str(native_id))
                        if key in self._seen_ids:
                            continue
                        self._seen_ids[key] = None
                        while len(self._seen_ids) > 20_000:
                            self._seen_ids.popitem(last=False)
                    # Without a provider ID, identical time/price/size can be
                    # distinct prints. Never deduplicate by those fields.
                    self._print_sequence += 1
                    kind = entry.get("type")
                    side = "buy" if type(kind) is int and kind == 0 else "sell" if type(kind) is int and kind == 1 else "unknown"
                    self._broadcast_to_channel_locked(channel, {"event": "trade", "data": {
                        "id": f"{self._print_epoch}:{self._print_sequence}",
                        "timestamp": timestamp.isoformat(), "price": _json_number(price),
                        "volume": _json_number(volume), "side": side,
                    }})


async def stream_market_trades(request, subscription, *, interval_seconds: float = .1):
    """Batch at most 256 prints; state changes and loss stay explicit."""
    try:
        for event in subscription.initial_events:
            if event.get("event") == "state":
                yield event
        while not await request.is_disconnected():
            try:
                first = await asyncio.wait_for(subscription.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield None
                continue
            if first.get("event") == "state":
                yield first
                continue
            await asyncio.sleep(interval_seconds)
            pending = [first]
            for _ in range(min(255, subscription.queue.qsize())):
                pending.append(subscription.queue.get_nowait())
            trades, dropped = [], 0
            for event in pending:
                if event.get("event") == "trade":
                    trades.append(event["data"])
                elif event.get("event") == "trade_gap":
                    dropped += event["data"]["dropped"]
                elif event.get("event") == "state":
                    if trades or dropped:
                        yield {"event": "trades", "data": {"contract_id": subscription.contract_id, "trades": trades, "dropped": dropped}}
                        trades, dropped = [], 0
                    yield event
            if trades or dropped:
                yield {"event": "trades", "data": {"contract_id": subscription.contract_id, "trades": trades, "dropped": dropped}}
    finally:
        await subscription.close()
