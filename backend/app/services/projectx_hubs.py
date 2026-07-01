from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

import websockets

from .projectx_client import ProjectXClient
from .streaming_pnl_tracker import StreamingPnlTracker

logger = logging.getLogger(__name__)

_SIGNALR_RECORD_SEPARATOR = "\x1e"
_MARKET_SUBSCRIBE_ENV = "PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE"
_USER_SUBSCRIBE_ENV = "PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE"


@dataclass
class DispatchCircuitSnapshot:
    name: str
    state: str
    consecutive_failures: int
    total_failures: int
    total_successes: int
    skipped_dispatches: int
    last_error: str | None


class _DispatchCircuit:
    """
    Small per-stream circuit breaker for tracker dispatch.

    This follows the same failure-isolation shape used by project-x-py's
    realtime circuit breaker without pulling in the SDK dependency stack.
    """

    def __init__(
        self,
        *,
        name: str,
        failure_threshold: int,
        recovery_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ):
        self._name = name
        self._failure_threshold = max(1, int(failure_threshold))
        self._recovery_seconds = max(0.1, float(recovery_seconds))
        self._now = now
        self._state = "closed"
        self._opened_at: float | None = None
        self._consecutive_failures = 0
        self._total_failures = 0
        self._total_successes = 0
        self._skipped_dispatches = 0
        self._last_error: str | None = None

    def allow_dispatch(self) -> bool:
        if self._state != "open":
            return True

        opened_at = self._opened_at
        if opened_at is not None and self._now() - opened_at >= self._recovery_seconds:
            self._state = "half_open"
            return True

        self._skipped_dispatches += 1
        return False

    def record_success(self) -> None:
        self._state = "closed"
        self._opened_at = None
        self._consecutive_failures = 0
        self._total_successes += 1

    def record_failure(self, exc: Exception) -> None:
        self._total_failures += 1
        self._consecutive_failures += 1
        self._last_error = str(exc)
        if self._state == "half_open" or self._consecutive_failures >= self._failure_threshold:
            self._state = "open"
            self._opened_at = self._now()

    def snapshot(self) -> DispatchCircuitSnapshot:
        return DispatchCircuitSnapshot(
            name=self._name,
            state=self._state,
            consecutive_failures=self._consecutive_failures,
            total_failures=self._total_failures,
            total_successes=self._total_successes,
            skipped_dispatches=self._skipped_dispatches,
            last_error=self._last_error,
        )


class ProjectXHubRunner:
    """
    Minimal SignalR websocket consumer for ProjectX market/user hub events.

    The payload adapters are isolated in StreamingPnlTracker parser functions so
    event-shape changes can be handled in one place.
    """

    def __init__(
        self,
        *,
        tracker: StreamingPnlTracker,
        client_factory: Callable[[], ProjectXClient] = ProjectXClient.from_env,
        market_hub_url: str | None = None,
        user_hub_url: str | None = None,
        reconnect_base_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        dispatch_failure_threshold: int = 5,
        dispatch_recovery_seconds: float = 30.0,
    ):
        self._tracker = tracker
        self._client_factory = client_factory
        self._market_hub_url = market_hub_url or os.getenv("PROJECTX_MARKET_HUB_URL")
        self._user_hub_url = user_hub_url or os.getenv("PROJECTX_USER_HUB_URL")
        self._reconnect_base_seconds = max(0.5, float(reconnect_base_seconds))
        self._reconnect_max_seconds = max(self._reconnect_base_seconds, float(reconnect_max_seconds))
        self._dispatch_failure_threshold = max(1, int(dispatch_failure_threshold))
        self._dispatch_recovery_seconds = max(0.1, float(dispatch_recovery_seconds))
        self._dispatch_circuits: dict[str, _DispatchCircuit] = {}

    async def run_forever(self) -> None:
        tasks: list[asyncio.Task[Any]] = []
        if self._market_hub_url:
            tasks.append(asyncio.create_task(self._consume_hub("market", self._market_hub_url)))
        if self._user_hub_url:
            tasks.append(asyncio.create_task(self._consume_hub("user", self._user_hub_url)))

        if not tasks:
            logger.info("[hubs] market/user hub URLs are not configured; streaming runner is idle")
            return

        await asyncio.gather(*tasks)

    async def _consume_hub(self, stream_kind: str, hub_url: str) -> None:
        backoff_seconds = self._reconnect_base_seconds
        subscribe_env = _MARKET_SUBSCRIBE_ENV if stream_kind == "market" else _USER_SUBSCRIBE_ENV

        while True:
            try:
                client = self._client_factory()
                token = client.get_access_token()
                url_with_token = _append_query(hub_url, {"access_token": token})
                logger.info("[hubs] connecting kind=%s", stream_kind)

                async with websockets.connect(
                    url_with_token,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2 * 1024 * 1024,
                ) as websocket:
                    await _signalr_handshake(websocket)
                    for message in _load_subscription_messages(subscribe_env):
                        await websocket.send(json.dumps(message) + _SIGNALR_RECORD_SEPARATOR)

                    backoff_seconds = self._reconnect_base_seconds
                    async for raw_message in websocket:
                        for frame in _decode_signalr_frames(raw_message):
                            self._dispatch_frame(stream_kind, frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[hubs] disconnected kind=%s retry_in=%.1fs error=%s",
                    stream_kind,
                    backoff_seconds,
                    exc,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(self._reconnect_max_seconds, backoff_seconds * 2.0)

    def _dispatch_frame(self, stream_kind: str, frame: Mapping[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == 1 and isinstance(frame.get("arguments"), list):
            self._dispatch_signalr_invocation(stream_kind, frame)
            return

        self._dispatch_payload(stream_kind, frame)

    def _dispatch_signalr_invocation(self, stream_kind: str, frame: Mapping[str, Any]) -> None:
        arguments = frame.get("arguments")
        if not isinstance(arguments, list):
            return

        if stream_kind == "market" and len(arguments) >= 2 and isinstance(arguments[0], str) and isinstance(arguments[1], Mapping):
            payload = dict(arguments[1])
            payload.setdefault("contractId", arguments[0])
            self._dispatch_payload(stream_kind, payload)
            return

        for argument in arguments:
            if isinstance(argument, Mapping):
                self._dispatch_payload(stream_kind, argument)

    def _dispatch_payload(self, stream_kind: str, payload: Mapping[str, Any]) -> None:
        circuit = self._dispatch_circuit(stream_kind)
        if not circuit.allow_dispatch():
            logger.warning("[hubs] dispatch circuit open; dropping payload kind=%s", stream_kind)
            return

        try:
            if stream_kind == "market":
                self._tracker.ingest_market_event(payload)
            else:
                self._tracker.ingest_position_event(payload)
        except Exception as exc:
            circuit.record_failure(exc)
            snapshot = circuit.snapshot()
            logger.exception(
                "[hubs] dispatch failed kind=%s state=%s consecutive_failures=%s",
                stream_kind,
                snapshot.state,
                snapshot.consecutive_failures,
            )
            return

        circuit.record_success()

    def _dispatch_circuit(self, stream_kind: str) -> _DispatchCircuit:
        circuit = self._dispatch_circuits.get(stream_kind)
        if circuit is None:
            circuit = _DispatchCircuit(
                name=stream_kind,
                failure_threshold=self._dispatch_failure_threshold,
                recovery_seconds=self._dispatch_recovery_seconds,
            )
            self._dispatch_circuits[stream_kind] = circuit
        return circuit

    def dispatch_health(self) -> dict[str, dict[str, int | str | None]]:
        return {
            name: {
                "state": snapshot.state,
                "consecutive_failures": snapshot.consecutive_failures,
                "total_failures": snapshot.total_failures,
                "total_successes": snapshot.total_successes,
                "skipped_dispatches": snapshot.skipped_dispatches,
                "last_error": snapshot.last_error,
            }
            for name, circuit in self._dispatch_circuits.items()
            for snapshot in [circuit.snapshot()]
        }


async def _signalr_handshake(websocket: websockets.WebSocketClientProtocol) -> None:
    handshake_payload = {"protocol": "json", "version": 1}
    await websocket.send(json.dumps(handshake_payload) + _SIGNALR_RECORD_SEPARATOR)


def _decode_signalr_frames(raw_message: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw_message, bytes):
        text = raw_message.decode("utf-8", errors="ignore")
    else:
        text = str(raw_message)

    chunks = [chunk for chunk in text.split(_SIGNALR_RECORD_SEPARATOR) if chunk.strip()]
    frames: list[Mapping[str, Any]] = []
    for chunk in chunks:
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            frames.append(parsed)
    return frames


def _load_subscription_messages(env_name: str) -> list[Mapping[str, Any]]:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[hubs] invalid JSON in %s", env_name)
        return []

    if isinstance(parsed, Mapping):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, Mapping)]
    return []


def _append_query(url: str, params: Mapping[str, str]) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme == "https":
        scheme = "wss"
    elif scheme == "http":
        scheme = "ws"
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing.update(params)
    updated_query = urlencode(existing)
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            updated_query,
            parsed.fragment,
        )
    )
