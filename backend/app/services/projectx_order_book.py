from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import math
import os
from typing import Any, AsyncContextManager, Callable, Mapping

from .projectx_client import ProjectXClient, ProjectXClientError, validate_projectx_url
from .projectx_hubs import (
    _SIGNALR_RECORD_SEPARATOR,
    _append_query,
    _decode_signalr_frames,
    _open_hub,
    _signalr_handshake,
)
from .trading_day import futures_session_is_open

logger = logging.getLogger(__name__)

_DEFAULT_MARKET_HUB_URL = "https://rtc.topstepx.com/hubs/market"
_DEPTH_TARGET = "GatewayDepth"
_SUBSCRIBE_TARGET = "SubscribeContractMarketDepth"
_UNSUBSCRIBE_TARGET = "UnsubscribeContractMarketDepth"

# ProjectX DomType values documented at https://gateway.docs.projectx.com/docs/realtime/.
_ASK_TYPES = frozenset({1, 3, 10})  # Ask, BestAsk, NewBestAsk
_BID_TYPES = frozenset({2, 4, 9})  # Bid, BestBid, NewBestBid
_RESET_TYPE = 6
_RECENT_FINGERPRINT_LIMIT = 4096
_MARKET_CLOSED_MESSAGE = (
    "Market closed. Order book updates resume automatically when the trading session opens."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _Level:
    price: Decimal
    size: Decimal
    volume: Decimal
    current_volume: Decimal | None
    timestamp: datetime


class MarketByPriceBook:
    """Independent, incrementally maintained ProjectX book for one contract ID."""

    def __init__(self, contract_id: str):
        self.contract_id = _normalize_contract_id(contract_id)
        self._bids: dict[Decimal, _Level] = {}
        self._asks: dict[Decimal, _Level] = {}
        self._sequence = 0
        self._last_timestamp: datetime | None = None
        self._last_reset_timestamp: datetime | None = None
        self._level_timestamps: dict[tuple[str, Decimal], datetime] = {}
        self._explicit_depth_levels: set[tuple[str, Decimal]] = set()
        self._best_quote_prices: dict[str, Decimal] = {}
        self._best_quote_timestamps: dict[str, datetime] = {}
        self._recent_fingerprints: deque[tuple[Any, ...]] = deque()
        self._recent_fingerprint_set: set[tuple[Any, ...]] = set()

    @property
    def sequence(self) -> int:
        return self._sequence

    def apply(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Apply one GatewayDepth entry and return a UI event when state changed."""

        depth_type = _parse_dom_type(payload.get("type"))
        timestamp = _parse_timestamp(payload.get("timestamp"))
        if depth_type is None or timestamp is None:
            return None

        if depth_type == _RESET_TYPE:
            fingerprint = (timestamp, depth_type, None, None, None)
            if self._is_duplicate(fingerprint) and not self._bids and not self._asks:
                return None
            # Reset is an authoritative epoch boundary. Forget pre-reset payload
            # fingerprints so an identical rebuilt level is not mistaken for a
            # duplicate, even when the provider reuses timestamps and values.
            self._recent_fingerprints.clear()
            self._recent_fingerprint_set.clear()
            self._remember_fingerprint(fingerprint)
            self._bids.clear()
            self._asks.clear()
            self._level_timestamps.clear()
            self._explicit_depth_levels.clear()
            self._best_quote_prices.clear()
            self._best_quote_timestamps.clear()
            self._last_reset_timestamp = timestamp
            self._last_timestamp = timestamp
            self._sequence += 1
            return self.snapshot(reset=True)

        side = _side_for_dom_type(depth_type)
        if side is None:
            # Trade, session high/low, fill, and unknown events are not resting levels.
            return None

        price = _parse_decimal(payload.get("price"))
        volume = _parse_nonnegative_decimal(payload.get("volume"))
        if price is None or volume is None:
            return None

        current_volume = _parse_optional_nonnegative_decimal(payload.get("currentVolume"))
        if "currentVolume" in payload and payload.get("currentVolume") is not None and current_volume is None:
            return None

        fingerprint = (timestamp, depth_type, price, volume, current_volume)
        if self._is_duplicate(fingerprint):
            return None

        level_key = (side, price)
        previous_timestamp = self._level_timestamps.get(level_key)
        if self._last_reset_timestamp is not None and timestamp < self._last_reset_timestamp:
            return None
        if previous_timestamp is not None and timestamp < previous_timestamp:
            return None

        is_best_quote = depth_type not in {1, 2}
        best_timestamp = self._best_quote_timestamps.get(side)
        if best_timestamp is not None and timestamp < best_timestamp:
            return None
        levels = self._bids if side == "bid" else self._asks
        if is_best_quote:
            # A best quote proves only its current price. A prior quote is not
            # a resting depth level unless an explicit Ask/Bid also supplied it.
            # The new best price also invalidates any supposedly better levels.
            obsolete = [
                level_price for level_price in levels
                if (side == "bid" and level_price > price)
                or (side == "ask" and level_price < price)
            ] if volume > 0 else []
            if any(levels[level_price].timestamp > timestamp for level_price in obsolete):
                return None
            prior_best = self._best_quote_prices.get(side)
            if prior_best is not None and (side, prior_best) not in self._explicit_depth_levels:
                obsolete.append(prior_best)
            for level_price in obsolete:
                levels.pop(level_price, None)
                self._explicit_depth_levels.discard((side, level_price))
                # The side-wide best timestamp rejects late updates for removed
                # prices, so quote-only feeds need no unbounded tombstone map.
                self._level_timestamps.pop((side, level_price), None)
            self._best_quote_timestamps[side] = timestamp
            if volume > 0:
                self._best_quote_prices[side] = price
            else:
                self._best_quote_prices.pop(side, None)
        elif volume > 0:
            self._explicit_depth_levels.add(level_key)
        else:
            self._explicit_depth_levels.discard(level_key)

        self._remember_fingerprint(fingerprint)
        self._level_timestamps[level_key] = timestamp
        if self._last_timestamp is None or timestamp > self._last_timestamp:
            self._last_timestamp = timestamp

        previous = levels.get(price)
        if volume == 0:
            self._explicit_depth_levels.discard(level_key)
            if previous is None and not is_best_quote:
                return None
            levels.pop(price, None)
        else:
            replacement = _Level(
                price=price,
                size=volume,
                volume=volume,
                current_volume=current_volume,
                timestamp=timestamp,
            )
            if previous == replacement:
                return None
            levels[price] = replacement

        self._sequence += 1
        if is_best_quote:
            # A full snapshot removes obsolete best prices in every client.
            return self.snapshot()
        return {
            "contract_id": self.contract_id,
            "sequence": self._sequence,
            "timestamp": _iso_utc(timestamp),
            "side": side,
            "price": _json_number(price),
            # ProjectX documents `volume` as total volume at the price level.
            # It is aggregate size, not an order or trader count.
            "size": _json_number(volume),
            "volume": _json_number(volume),
            "current_volume": _json_number(current_volume) if current_volume is not None else None,
        }

    def clear_for_reconnect(self) -> dict[str, Any] | None:
        """Drop potentially stale state without creating a provider timestamp watermark."""

        self._bids.clear()
        self._asks.clear()
        self._level_timestamps.clear()
        self._explicit_depth_levels.clear()
        self._best_quote_prices.clear()
        self._best_quote_timestamps.clear()
        self._recent_fingerprints.clear()
        self._recent_fingerprint_set.clear()
        self._last_timestamp = None
        self._last_reset_timestamp = None
        self._sequence += 1
        return self.snapshot(reset=True)

    def snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "sequence": self._sequence,
            "timestamp": _iso_utc(self._last_timestamp) if self._last_timestamp is not None else None,
            "bids": [
                {"price": _json_number(level.price), "size": _json_number(level.size)}
                for level in sorted(self._bids.values(), key=lambda item: item.price, reverse=True)
            ],
            "asks": [
                {"price": _json_number(level.price), "size": _json_number(level.size)}
                for level in sorted(self._asks.values(), key=lambda item: item.price)
            ],
            "reset": bool(reset),
        }

    def _is_duplicate(self, fingerprint: tuple[Any, ...]) -> bool:
        return fingerprint in self._recent_fingerprint_set

    def _remember_fingerprint(self, fingerprint: tuple[Any, ...]) -> None:
        if fingerprint in self._recent_fingerprint_set:
            return
        self._recent_fingerprints.append(fingerprint)
        self._recent_fingerprint_set.add(fingerprint)
        while len(self._recent_fingerprints) > _RECENT_FINGERPRINT_LIMIT:
            expired = self._recent_fingerprints.popleft()
            self._recent_fingerprint_set.discard(expired)


@dataclass
class _ContractChannel:
    book: MarketByPriceBook
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    market_open: bool | None = None
    last_state: dict[str, Any] | None = None


class OrderBookSubscription:
    def __init__(
        self,
        *,
        session: "ProjectXMarketDepthSession",
        contract_id: str,
        queue: asyncio.Queue[dict[str, Any]],
        initial_events: list[dict[str, Any]],
        on_close: Callable[["OrderBookSubscription"], Any] | None = None,
    ):
        self.session = session
        self.contract_id = contract_id
        self.queue = queue
        self.initial_events = initial_events
        self._on_close = on_close
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.session.unsubscribe(self.contract_id, self.queue)
        finally:
            if self._on_close is not None:
                result = self._on_close(self)
                if hasattr(result, "__await__"):
                    await result


class ProjectXMarketDepthSession:
    """One server-side ProjectX market-hub connection for one TopSignal user."""

    def __init__(
        self,
        *,
        client: ProjectXClient,
        market_hub_url: str | None = None,
        connect_factory: Callable[..., AsyncContextManager[Any]] | None = None,
        reconnect_base_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        subscriber_queue_size: int = 512,
        send_timeout_seconds: float = 5.0,
        market_check_seconds: float = 5.0,
        keepalive_seconds: float = 15.0,
        now: Callable[[], datetime] | None = None,
    ):
        self._client = client
        self._market_hub_url = validate_projectx_url(
            market_hub_url
            or os.getenv("PROJECTX_MARKET_HUB_URL")
            or _DEFAULT_MARKET_HUB_URL,
            websocket=True,
        )
        self._connect_factory = _open_hub if connect_factory is None else connect_factory
        self._reconnect_base_seconds = max(0.01, float(reconnect_base_seconds))
        self._reconnect_max_seconds = max(
            self._reconnect_base_seconds,
            float(reconnect_max_seconds),
        )
        self._subscriber_queue_size = max(8, int(subscriber_queue_size))
        self._send_timeout_seconds = max(0.05, float(send_timeout_seconds))
        self._market_check_seconds = max(0.01, float(market_check_seconds))
        self._keepalive_seconds = max(0.01, float(keepalive_seconds))
        self._now = now or _utc_now
        self._market_wakeup = asyncio.Event()
        self._channels: dict[str, _ContractChannel] = {}
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._restart_lock = asyncio.Lock()
        self._runner_task: asyncio.Task[Any] | None = None
        self._websocket: Any | None = None
        self._connection_generation = 0
        self._provider_subscribed: set[str] = set()
        self._pending_invocations: dict[str, tuple[str, str, int]] = {}
        self._latest_contract_invocation: dict[str, str] = {}
        self._next_invocation_id = 1
        self._connection_state = "disconnected"
        self._closed = False

    def update_client(self, client: ProjectXClient) -> None:
        # A newly resolved client carries any credential rotation into the next reconnect.
        self._client = client

    async def subscribe(
        self,
        contract_id: str,
        *,
        on_close: Callable[[OrderBookSubscription], Any] | None = None,
    ) -> OrderBookSubscription:
        normalized_contract_id = _normalize_contract_id(contract_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._subscriber_queue_size)

        async with self._lock:
            if self._closed:
                raise RuntimeError("market depth session is closed")
            channel = self._channels.get(normalized_contract_id)
            if channel is None:
                channel = _ContractChannel(book=MarketByPriceBook(normalized_contract_id))
                self._channels[normalized_contract_id] = channel
            if not futures_session_is_open(self._now(), symbol=normalized_contract_id):
                self._publish_state_locked(normalized_contract_id, channel, "market_closed")
            channel.subscribers.add(queue)
            self._market_wakeup.set()

            state = self._connection_state
            if self._runner_task is None or self._runner_task.done():
                state = "reconnecting"
                self._connection_state = state
                self._runner_task = asyncio.create_task(
                    self._run_connection_loop(),
                    name="projectx-market-depth",
                )

            # Queue registration and snapshot capture happen under the same lock as
            # applying updates, so no delta can overtake this initial snapshot.
            state_payload = self._state_for_market(normalized_contract_id, state)
            initial_events = [
                {"event": "state", "data": state_payload},
                {"event": "snapshot", "data": channel.book.snapshot()},
            ]

        try:
            await self._subscribe_provider_if_active(normalized_contract_id)
        except BaseException:
            # Dynamic provider subscription failed (or its caller was cancelled)
            # after local registration. Roll that registration back so refcounts,
            # connection lifetime, and registry pruning cannot leak.
            cleanup_task = asyncio.create_task(
                self._rollback_failed_subscription(normalized_contract_id, queue),
                name="projectx-market-depth-subscribe-rollback",
            )
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    # Preserve the outer cancellation, but let rollback finish.
                    continue
                except Exception:
                    break
            await asyncio.gather(cleanup_task, return_exceptions=True)
            raise
        return OrderBookSubscription(
            session=self,
            contract_id=normalized_contract_id,
            queue=queue,
            initial_events=initial_events,
            on_close=on_close,
        )

    async def unsubscribe(
        self,
        contract_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        normalized_contract_id = _normalize_contract_id(contract_id)
        removed_contract = False
        provider_state_indeterminate = False
        task_to_stop: asyncio.Task[Any] | None = None

        async with self._lock:
            channel = self._channels.get(normalized_contract_id)
            if channel is None:
                return
            channel.subscribers.discard(queue)
            if not channel.subscribers:
                del self._channels[normalized_contract_id]
                removed_contract = True
            if not self._channels and self._runner_task is not None:
                task_to_stop = self._runner_task

        if removed_contract:
            try:
                await self._unsubscribe_provider_if_unused(normalized_contract_id)
            except Exception as exc:
                # Local reference counting and task cleanup remain authoritative
                # even if the already-failing provider socket cannot unsubscribe.
                logger.warning(
                    "[market-depth] provider unsubscribe failed error_type=%s",
                    type(exc).__name__,
                )
                provider_state_indeterminate = True

        if provider_state_indeterminate and task_to_stop is None:
            await self._restart_indeterminate_connection()

        if task_to_stop is not None:
            await self._stop_runner_task(task_to_stop)
            async with self._lock:
                if self._runner_task is task_to_stop:
                    self._runner_task = None
                if self._channels and not self._closed and self._runner_task is None:
                    self._connection_state = "reconnecting"
                    self._runner_task = asyncio.create_task(
                        self._run_connection_loop(),
                        name="projectx-market-depth",
                    )
                elif not self._channels:
                    self._connection_state = "disconnected"

    async def _rollback_failed_subscription(
        self,
        contract_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        await self.unsubscribe(contract_id, queue)
        # A timed-out or failed send may still have reached ProjectX. Reconnect
        # remaining channels so provider state is rebuilt from known intent.
        await self._restart_indeterminate_connection()

    async def _restart_indeterminate_connection(self) -> None:
        async with self._restart_lock:
            async with self._lock:
                if not self._channels or self._closed:
                    return
                task_to_restart = self._runner_task

            await self._broadcast_state(
                "reconnecting",
                message="Market depth subscription state is being recovered.",
            )
            old_websocket: Any | None = None
            old_generation: int | None = None
            if task_to_restart is not None:
                async with self._send_lock:
                    old_websocket = self._websocket
                    old_generation = self._connection_generation
                await self._stop_runner_task(task_to_restart)
                if old_websocket is not None and old_generation is not None:
                    await self._deactivate_connection(old_websocket, old_generation)

            # Reset after the old reader is inactive; generation checks reject
            # late frames if its shutdown exceeded the bounded wait.
            await self._clear_books_for_reconnect()

            async with self._lock:
                if self._runner_task is task_to_restart:
                    self._runner_task = None
                if self._channels and not self._closed and self._runner_task is None:
                    self._connection_state = "reconnecting"
                    self._runner_task = asyncio.create_task(
                        self._run_connection_loop(),
                        name="projectx-market-depth",
                    )

    async def _stop_runner_task(self, task: asyncio.Task[Any]) -> None:
        async with self._send_lock:
            websocket = self._websocket
        close = getattr(websocket, "close", None)
        if callable(close):
            try:
                close_result = close()
                if hasattr(close_result, "__await__"):
                    async with asyncio.timeout(2.0):
                        await close_result
            except Exception:
                pass
        task.cancel()
        try:
            async with asyncio.timeout(2.0):
                await asyncio.gather(task, return_exceptions=True)
        except asyncio.TimeoutError:
            logger.warning("[market-depth] connection task did not stop before timeout")

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            task = self._runner_task
            self._channels.clear()
        if task is not None:
            await self._stop_runner_task(task)
        await self._force_deactivate_connection()
        async with self._lock:
            self._runner_task = None
            self._connection_state = "disconnected"

    async def is_idle(self) -> bool:
        async with self._lock:
            return not self._channels

    async def process_signalr_frame(
        self,
        frame: Mapping[str, Any],
        *,
        connection_generation: int | None = None,
    ) -> None:
        frame_type = frame.get("type")
        if frame_type == 1 and str(frame.get("target") or "").casefold() == _DEPTH_TARGET.casefold():
            arguments = frame.get("arguments")
            if not isinstance(arguments, list) or len(arguments) < 2:
                return
            contract_id = arguments[0]
            if not isinstance(contract_id, str) or not contract_id.strip():
                return
            await self._apply_depth_entries_for_connection(
                contract_id.strip(),
                arguments[1],
                connection_generation=connection_generation,
            )
            return

        if frame_type == 3:
            await self._handle_invocation_completion(
                frame,
                connection_generation=connection_generation,
            )
            return

        if frame_type == 7:
            if (
                connection_generation is not None
                and not await self._connection_is_active(connection_generation)
            ):
                return
            raise ConnectionError("ProjectX market hub closed the connection")

        # Defensive support for already-unwrapped GatewayDepth envelopes used by
        # existing adapters. The exact contract ID remains mandatory.
        contract_id = frame.get("contractId", frame.get("contract_id"))
        if isinstance(contract_id, str) and contract_id.strip():
            data = frame.get("data", frame)
            await self._apply_depth_entries_for_connection(
                contract_id.strip(),
                data,
                connection_generation=connection_generation,
            )

    async def _apply_depth_entries_for_connection(
        self,
        contract_id: str,
        raw_entries: Any,
        *,
        connection_generation: int | None,
    ) -> None:
        entries = _depth_entries(raw_entries)
        if connection_generation is None:
            for entry in entries:
                await self._apply_depth_entry(contract_id, entry)
            return

        # Keep validation and application atomic with deactivation. Lock ordering
        # is send-lock then book/channel lock everywhere this pair is needed.
        async with self._send_lock:
            if (
                self._websocket is None
                or self._connection_generation != connection_generation
            ):
                return
            for entry in entries:
                await self._apply_depth_entry(contract_id, entry)

    async def _run_connection_loop(self) -> None:
        backoff_seconds = self._reconnect_base_seconds
        while await self._has_active_contracts():
            any_open, _ = await self._refresh_market_states()
            if not any_open:
                # Keep local SSE subscribers alive without authenticating or dialing
                # ProjectX throughout a scheduled closure. New contracts wake this wait.
                await self._wait_for_market_check()
                continue
            connected_once = False
            active_websocket: Any | None = None
            active_generation: int | None = None
            try:
                token = await asyncio.to_thread(self._client.get_access_token)
                any_open, _ = await self._refresh_market_states()
                if not any_open:
                    continue
                url_with_token = _append_query(
                    self._market_hub_url,
                    {"access_token": token},
                )
                async with self._connect_factory(
                    url_with_token,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2 * 1024 * 1024,
                ) as websocket:
                    pending_frames = await _signalr_handshake(websocket)
                    active_generation = await self._activate_connection(websocket)
                    active_websocket = websocket
                    await self._sync_provider_subscriptions(
                        websocket=websocket,
                        connection_generation=active_generation,
                    )
                    connected_once = True
                    backoff_seconds = self._reconnect_base_seconds
                    await self._broadcast_state("connected")
                    # Existing SSE viewers need a new baseline after a scheduled
                    # closure, even if the provider starts with deltas, not Reset.
                    async with self._lock:
                        for contract_id, channel in self._channels.items():
                            if futures_session_is_open(self._now(), symbol=contract_id):
                                self._broadcast_to_channel_locked(
                                    channel, {"event": "snapshot", "data": channel.book.snapshot(reset=True)},
                                )

                    for frame in pending_frames:
                        await self.process_signalr_frame(
                            frame,
                            connection_generation=active_generation,
                        )

                    await self._run_connected(websocket, active_generation)
                    if await self._has_active_contracts():
                        await self._broadcast_state(
                            "reconnecting",
                            message="Market depth connection interrupted.",
                        )
            except asyncio.CancelledError:
                raise
            except ProjectXClientError:
                logger.warning("[market-depth] ProjectX authentication unavailable")
                await self._broadcast_state(
                    "unavailable",
                    message="ProjectX market depth is unavailable.",
                )
            except Exception as exc:
                logger.warning(
                    "[market-depth] connection interrupted error_type=%s",
                    type(exc).__name__,
                )
                await self._broadcast_state(
                    "reconnecting",
                    message="Market depth connection interrupted.",
                )
            finally:
                if active_websocket is not None and active_generation is not None:
                    await self._deactivate_connection(
                        active_websocket,
                        active_generation,
                    )

            if not await self._has_active_contracts():
                break
            if connected_once:
                await self._clear_books_for_reconnect()
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(self._reconnect_max_seconds, backoff_seconds * 2.0)

    def _state_for_market(
        self, contract_id: str, state: str, *, message: str | None = None,
    ) -> dict[str, Any]:
        if not futures_session_is_open(self._now(), symbol=contract_id):
            return _state_payload(contract_id, "market_closed", message=_MARKET_CLOSED_MESSAGE)
        return _state_payload(contract_id, state, message=message)

    def _publish_state_locked(
        self, contract_id: str, channel: _ContractChannel, state: str,
        *, message: str | None = None,
    ) -> None:
        payload = self._state_for_market(contract_id, state, message=message)
        if payload["state"] == "market_closed":
            if channel.last_state == payload:
                return
            self._broadcast_to_channel_locked(
                channel, {"event": "snapshot", "data": channel.book.clear_for_reconnect()},
            )
        channel.last_state = payload
        self._broadcast_to_channel_locked(channel, {"event": "state", "data": payload})

    async def _refresh_market_states(self) -> tuple[bool, bool]:
        any_open = False
        changed = False
        async with self._lock:
            for contract_id, channel in self._channels.items():
                is_open = futures_session_is_open(self._now(), symbol=contract_id)
                was_open = channel.market_open
                channel.market_open = is_open
                any_open |= is_open
                changed |= was_open is not None and was_open != is_open
                if not is_open:
                    self._publish_state_locked(contract_id, channel, "market_closed")
                elif was_open is False:
                    self._publish_state_locked(contract_id, channel, "reconnecting")
        return any_open, changed

    async def _wait_for_market_check(self) -> None:
        try:
            # Python 3.11 wait_for can consume caller cancellation when the
            # event finishes in the same loop turn, stranding connection cleanup.
            async with asyncio.timeout(self._market_check_seconds):
                await self._market_wakeup.wait()
        except asyncio.TimeoutError:
            pass
        self._market_wakeup.clear()

    async def _watch_market_hours(self) -> None:
        while True:
            await self._wait_for_market_check()
            _, changed = await self._refresh_market_states()
            if changed:
                # Rebuild the shared connection from the contracts still open.
                # Closed channels remain attached to SSE and receive no depth.
                return

    async def _send_keepalives(self, websocket: Any, generation: int) -> None:
        while True:
            await asyncio.sleep(self._keepalive_seconds)
            async with self._send_lock:
                if self._websocket is not websocket or self._connection_generation != generation:
                    return
                # SignalR requires protocol pings; WebSocket control pings alone
                # do not satisfy its client timeout during an otherwise quiet feed.
                async with asyncio.timeout(self._send_timeout_seconds):
                    await websocket.send('{"type":6}' + _SIGNALR_RECORD_SEPARATOR)

    async def _run_connected(self, websocket: Any, generation: int) -> None:
        async def receive() -> None:
            async for raw_message in websocket:
                for frame in _decode_signalr_frames(raw_message):
                    await self.process_signalr_frame(frame, connection_generation=generation)

        tasks = [
            asyncio.create_task(receive(), name="projectx-market-depth-reader"),
            asyncio.create_task(self._watch_market_hours(), name="projectx-market-depth-hours"),
            asyncio.create_task(
                self._send_keepalives(websocket, generation), name="projectx-market-depth-keepalive",
            ),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _apply_depth_entry(self, contract_id: str, entry: Mapping[str, Any]) -> None:
        async with self._lock:
            channel = self._channels.get(contract_id)
            if channel is None:
                return
            if not futures_session_is_open(self._now(), symbol=contract_id):
                self._publish_state_locked(contract_id, channel, "market_closed")
                return
            event_data = channel.book.apply(entry)
            if event_data is None:
                return
            event_name = "snapshot" if "bids" in event_data else "update"
            event = {"event": event_name, "data": event_data}
            self._broadcast_to_channel_locked(channel, event)

    async def _clear_books_for_reconnect(self) -> None:
        async with self._lock:
            for channel in self._channels.values():
                snapshot = channel.book.clear_for_reconnect()
                if snapshot is not None:
                    self._broadcast_to_channel_locked(
                        channel,
                        {"event": "snapshot", "data": snapshot},
                    )

    async def _broadcast_state(self, state: str, *, message: str | None = None) -> None:
        async with self._lock:
            self._connection_state = state
            for contract_id, channel in self._channels.items():
                self._publish_state_locked(contract_id, channel, state, message=message)

    def _broadcast_to_channel_locked(
        self,
        channel: _ContractChannel,
        event: dict[str, Any],
    ) -> None:
        for queue in tuple(channel.subscribers):
            if queue.full():
                # A slow client cannot safely skip a delta. Coalesce its backlog
                # into a fresh snapshot so it can resume from consistent state.
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                coalesced = (
                    event
                    if event.get("event") == "snapshot"
                    else {"event": "snapshot", "data": channel.book.snapshot()}
                )
                queue.put_nowait(coalesced)
                if event.get("event") == "state":
                    queue.put_nowait(event)
                continue
            queue.put_nowait(event)

    async def _activate_connection(self, websocket: Any) -> int:
        async with self._send_lock:
            self._connection_generation += 1
            generation = self._connection_generation
            self._websocket = websocket
            self._provider_subscribed.clear()
            self._pending_invocations.clear()
            self._latest_contract_invocation.clear()
            return generation

    async def _connection_is_active(self, connection_generation: int) -> bool:
        async with self._send_lock:
            return (
                self._websocket is not None
                and self._connection_generation == connection_generation
            )

    async def _deactivate_connection(self, websocket: Any, connection_generation: int) -> None:
        async with self._send_lock:
            if (
                self._websocket is websocket
                and self._connection_generation == connection_generation
            ):
                self._websocket = None
                self._provider_subscribed.clear()
                self._pending_invocations.clear()
                self._latest_contract_invocation.clear()

    async def _force_deactivate_connection(self) -> None:
        async with self._send_lock:
            self._websocket = None
            self._provider_subscribed.clear()
            self._pending_invocations.clear()
            self._latest_contract_invocation.clear()

    async def _sync_provider_subscriptions(
        self,
        *,
        websocket: Any,
        connection_generation: int,
    ) -> None:
        async with self._lock:
            contract_ids = tuple(self._channels)
        for contract_id in contract_ids:
            await self._subscribe_provider_if_active(
                contract_id,
                websocket=websocket,
                connection_generation=connection_generation,
            )

    async def _subscribe_provider_if_active(
        self,
        contract_id: str,
        *,
        websocket: Any | None = None,
        connection_generation: int | None = None,
    ) -> None:
        async with self._send_lock:
            async with self._lock:
                active = contract_id in self._channels and futures_session_is_open(
                    self._now(), symbol=contract_id,
                )
            current_websocket = self._websocket
            current_generation = self._connection_generation
            if websocket is not None and current_websocket is not websocket:
                return
            if (
                connection_generation is not None
                and current_generation != connection_generation
            ):
                return
            if (
                not active
                or current_websocket is None
                or contract_id in self._provider_subscribed
            ):
                return
            # Reserve before sending. A fast completion can otherwise discard an
            # error and then be overwritten by a post-send `add`.
            self._provider_subscribed.add(contract_id)
            try:
                await self._send_invocation_locked(
                    current_websocket,
                    _SUBSCRIBE_TARGET,
                    contract_id,
                    current_generation,
                )
            except BaseException:
                if (
                    self._websocket is current_websocket
                    and self._connection_generation == current_generation
                ):
                    self._provider_subscribed.discard(contract_id)
                raise

    async def _unsubscribe_provider_if_unused(self, contract_id: str) -> None:
        async with self._send_lock:
            async with self._lock:
                still_active = contract_id in self._channels
            websocket = self._websocket
            if still_active or websocket is None or contract_id not in self._provider_subscribed:
                return
            connection_generation = self._connection_generation
            self._provider_subscribed.discard(contract_id)
            await self._send_invocation_locked(
                websocket,
                _UNSUBSCRIBE_TARGET,
                contract_id,
                connection_generation,
            )

    async def _send_invocation_locked(
        self,
        websocket: Any,
        target: str,
        contract_id: str,
        connection_generation: int,
    ) -> None:
        invocation_id = str(self._next_invocation_id)
        self._next_invocation_id += 1
        payload = {
            "type": 1,
            "target": target,
            "arguments": [contract_id],
            "invocationId": invocation_id,
        }
        self._pending_invocations[invocation_id] = (
            target,
            contract_id,
            connection_generation,
        )
        self._latest_contract_invocation[contract_id] = invocation_id
        try:
            async with asyncio.timeout(self._send_timeout_seconds):
                await websocket.send(
                    json.dumps(payload, separators=(",", ":"))
                    + _SIGNALR_RECORD_SEPARATOR
                )
        except BaseException:
            self._pending_invocations.pop(invocation_id, None)
            if self._latest_contract_invocation.get(contract_id) == invocation_id:
                self._latest_contract_invocation.pop(contract_id, None)
            raise

    async def _handle_invocation_completion(
        self,
        frame: Mapping[str, Any],
        *,
        connection_generation: int | None,
    ) -> None:
        invocation_id = str(frame.get("invocationId") or "")
        unavailable_contract: str | None = None
        connected_contract: str | None = None
        unsubscribe_failed = False
        async with self._send_lock:
            pending = self._pending_invocations.pop(invocation_id, None)
            if pending is None:
                return
            target, contract_id, pending_generation = pending
            source_generation = (
                self._connection_generation
                if connection_generation is None
                else connection_generation
            )
            if source_generation != pending_generation:
                return
            if self._latest_contract_invocation.get(contract_id) != invocation_id:
                # A newer subscribe/unsubscribe superseded this completion.
                return
            self._latest_contract_invocation.pop(contract_id, None)
            has_error = bool(frame.get("error"))
            if target == _SUBSCRIBE_TARGET:
                if has_error:
                    if self._connection_generation == pending_generation:
                        self._provider_subscribed.discard(contract_id)
                    unavailable_contract = contract_id
                else:
                    connected_contract = contract_id
            elif target == _UNSUBSCRIBE_TARGET and has_error:
                unsubscribe_failed = True

        if unavailable_contract is not None:
            await self._broadcast_contract_state(
                unavailable_contract,
                "unavailable",
                message="ProjectX rejected the market-depth subscription.",
            )
        if connected_contract is not None:
            await self._broadcast_contract_state(connected_contract, "connected")
        if unsubscribe_failed:
            raise ConnectionError("ProjectX rejected the market-depth unsubscribe")

    async def _broadcast_contract_state(
        self,
        contract_id: str,
        state: str,
        *,
        message: str | None = None,
    ) -> None:
        async with self._lock:
            channel = self._channels.get(contract_id)
            if channel is None:
                return
            self._publish_state_locked(contract_id, channel, state, message=message)

    async def _has_active_contracts(self) -> bool:
        async with self._lock:
            return bool(self._channels) and not self._closed


@dataclass
class _RegistryUserSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    leases: int = 0


class ProjectXOrderBookRegistry:
    """User-scoped session registry; ProjectX credentials never leave the backend."""

    def __init__(
        self,
        *,
        session_factory: Callable[..., ProjectXMarketDepthSession] = ProjectXMarketDepthSession,
    ):
        self._session_factory = session_factory
        self._sessions: dict[str, ProjectXMarketDepthSession] = {}
        self._lock = asyncio.Lock()
        self._user_slots: dict[str, _RegistryUserSlot] = {}

    async def subscribe(
        self,
        *,
        user_id: str,
        client: ProjectXClient,
        contract_id: str,
    ) -> OrderBookSubscription:
        slot = await self._acquire_user_slot(user_id)
        try:
            async with self._lock:
                session = self._sessions.get(user_id)
                if session is None:
                    session = self._session_factory(client=client)
                    self._sessions[user_id] = session
                else:
                    session.update_client(client)
            try:
                return await session.subscribe(
                    contract_id,
                    on_close=lambda subscription: self._prune_user_subscription(
                        user_id,
                        subscription,
                    ),
                )
            except BaseException:
                if await session.is_idle():
                    async with self._lock:
                        if self._sessions.get(user_id) is session:
                            del self._sessions[user_id]
                raise
        finally:
            await self._release_user_slot(user_id, slot)

    async def close(self) -> None:
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    async def _prune_subscription(self, subscription: OrderBookSubscription) -> None:
        async with self._lock:
            user_id = next(
                (
                    candidate_user_id
                    for candidate_user_id, session in self._sessions.items()
                    if session is subscription.session
                ),
                None,
            )
        if user_id is None:
            return
        await self._prune_user_subscription(user_id, subscription)

    async def _prune_user_subscription(
        self,
        user_id: str,
        subscription: OrderBookSubscription,
    ) -> None:
        slot = await self._acquire_user_slot(user_id)
        try:
            if not await subscription.session.is_idle():
                return
            async with self._lock:
                if self._sessions.get(user_id) is subscription.session:
                    del self._sessions[user_id]
        finally:
            await self._release_user_slot(user_id, slot)

    async def _acquire_user_slot(self, user_id: str) -> _RegistryUserSlot:
        async with self._lock:
            slot = self._user_slots.get(user_id)
            if slot is None:
                slot = _RegistryUserSlot()
                self._user_slots[user_id] = slot
            slot.leases += 1
        try:
            await slot.lock.acquire()
        except BaseException:
            await self._drop_user_slot_lease(user_id, slot)
            raise
        return slot

    async def _release_user_slot(self, user_id: str, slot: _RegistryUserSlot) -> None:
        slot.lock.release()
        await self._drop_user_slot_lease(user_id, slot)

    async def _drop_user_slot_lease(self, user_id: str, slot: _RegistryUserSlot) -> None:
        async with self._lock:
            slot.leases = max(0, slot.leases - 1)
            if (
                slot.leases == 0
                and user_id not in self._sessions
                and self._user_slots.get(user_id) is slot
            ):
                del self._user_slots[user_id]


def _depth_entries(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, Mapping):
        nested = raw.get("data")
        if "type" not in raw and isinstance(nested, (Mapping, list, tuple)):
            return _depth_entries(nested)
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [entry for entry in raw if isinstance(entry, Mapping)]
    return []


def _side_for_dom_type(depth_type: int) -> str | None:
    if depth_type in _BID_TYPES:
        return "bid"
    if depth_type in _ASK_TYPES:
        return "ask"
    return None


def _parse_dom_type(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    try:
        if Decimal(str(value)) != Decimal(parsed):
            return None
    except (InvalidOperation, ValueError):
        return None
    return parsed


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_nonnegative_decimal(value: Any) -> Decimal | None:
    parsed = _parse_decimal(value)
    if parsed is None or parsed < 0:
        return None
    return parsed


def _parse_optional_nonnegative_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _parse_nonnegative_decimal(value)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _normalize_contract_id(contract_id: str) -> str:
    normalized = str(contract_id or "").strip()
    if not normalized:
        raise ValueError("contract_id must not be empty")
    if len(normalized) > 120:
        raise ValueError("contract_id is too long")
    return normalized


def _state_payload(
    contract_id: str,
    state: str,
    *,
    message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"contract_id": contract_id, "state": state}
    if message:
        payload["message"] = message
    return payload
