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

from .projectx_client import ProjectXClient, validate_projectx_url
from .streaming_pnl_tracker import StreamingPnlTracker

logger = logging.getLogger(__name__)

_SIGNALR_RECORD_SEPARATOR = "\x1e"
_MARKET_SUBSCRIBE_ENV = "PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE"
_USER_SUBSCRIBE_ENV = "PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE"
_DEFAULT_USER_HUB_URL = "https://rtc.topstepx.com/hubs/user"
_HANDSHAKE_TIMEOUT_SECONDS = 10.0


def _open_hub(url: str, **kwargs):
    connection = websockets.connect(url, **kwargs)
    # websockets 16 exposes this policy hook; an authenticated connection must
    # fail at a redirect rather than forward credentials to a new endpoint.
    connection.process_redirect = lambda exc: exc
    return connection


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
        # The exception may embed an entire provider payload or a websocket URL
        # containing the bearer token. Retain only its stable type for health.
        self._last_error = type(exc).__name__
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
        client_factory: Callable[[], ProjectXClient],
        user_id: str | None = None,
        account_id: int | None = None,
        market_hub_url: str | None = None,
        user_hub_url: str | None = None,
        reconnect_base_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        dispatch_failure_threshold: int = 5,
        dispatch_recovery_seconds: float = 30.0,
        user_account_refresh_seconds: float = 240.0,
        on_user_account: Callable[[Mapping[str, Any]], None] | None = None,
        on_user_disconnect: Callable[[], None] | None = None,
    ):
        self._tracker = tracker
        self._client_factory = client_factory
        self._user_id = user_id.strip() if user_id and user_id.strip() else None
        self._account_id = int(account_id) if account_id is not None else None
        self._market_hub_url = (
            os.getenv("PROJECTX_MARKET_HUB_URL") if market_hub_url is None else market_hub_url
        )
        self._user_hub_url = (
            os.getenv("PROJECTX_USER_HUB_URL", _DEFAULT_USER_HUB_URL)
            if user_hub_url is None
            else user_hub_url
        )
        for url in (self._market_hub_url, self._user_hub_url):
            if url:
                validate_projectx_url(url, websocket=True)
        if self._user_hub_url and (self._user_id is None or self._account_id is None):
            raise ValueError("a user hub requires an explicit user_id and account_id scope")
        if self._account_id is not None and self._account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        self._reconnect_base_seconds = max(0.5, float(reconnect_base_seconds))
        self._reconnect_max_seconds = max(self._reconnect_base_seconds, float(reconnect_max_seconds))
        self._dispatch_failure_threshold = max(1, int(dispatch_failure_threshold))
        self._dispatch_recovery_seconds = max(0.1, float(dispatch_recovery_seconds))
        self._user_account_refresh_seconds = min(
            240.0, max(30.0, float(user_account_refresh_seconds))
        )
        self._on_user_account = on_user_account
        self._on_user_disconnect = on_user_disconnect
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

        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _access_token(self) -> str:
        return self._client_factory().get_access_token()

    async def probe_user_account_once(
        self,
        *,
        timeout_seconds: float = 10.0,
    ) -> Mapping[str, Any]:
        """Read one scoped GatewayUserAccount snapshot, then close the socket.

        Unlike ``run_forever``, this bounded probe never starts market,
        position, order, or trade subscriptions and never invokes the normal
        disconnect invalidation callback when its intentional close occurs.
        The configured account callback completes before the observation is
        returned, so persistence failures cannot be mistaken for success.
        """

        if not self._user_hub_url or self._user_id is None or self._account_id is None:
            raise ValueError("a user-account probe requires an explicit user hub and owner scope")

        timeout = min(15.0, max(0.5, float(timeout_seconds)))
        loop = asyncio.get_running_loop()
        observed: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        configured_callback = self._on_user_account

        def capture(payload: Mapping[str, Any]) -> None:
            if configured_callback is not None:
                configured_callback(payload)
            if not observed.done():
                observed.set_result(dict(payload))

        async def probe() -> Mapping[str, Any]:
            token = await asyncio.to_thread(self._access_token)
            url_with_token = _append_query(
                self._user_hub_url,
                {"access_token": token},
            )
            async with _open_hub(
                url_with_token,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=2 * 1024 * 1024,
            ) as websocket:
                initial_frames = await _signalr_handshake(websocket)
                for frame in initial_frames:
                    self._dispatch_frame("user", frame)
                if observed.done():
                    return observed.result()

                await websocket.send(
                    json.dumps(
                        {"type": 1, "target": "SubscribeAccounts", "arguments": []}
                    )
                    + _SIGNALR_RECORD_SEPARATOR
                )
                while not observed.done():
                    raw_message = await websocket.recv()
                    for frame in _decode_signalr_frames(raw_message):
                        self._dispatch_frame("user", frame)
                        if observed.done():
                            break
                return observed.result()

        self._on_user_account = capture
        try:
            return await asyncio.wait_for(probe(), timeout=timeout)
        finally:
            self._on_user_account = configured_callback

    async def _consume_hub(self, stream_kind: str, hub_url: str) -> None:
        backoff_seconds = self._reconnect_base_seconds
        subscribe_env = _MARKET_SUBSCRIBE_ENV if stream_kind == "market" else _USER_SUBSCRIBE_ENV

        while True:
            connected_at: float | None = None
            try:
                token = await asyncio.to_thread(self._access_token)
                url_with_token = _append_query(hub_url, {"access_token": token})
                logger.info("[hubs] connecting kind=%s", stream_kind)

                async with _open_hub(
                    url_with_token,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2 * 1024 * 1024,
                ) as websocket:
                    initial_frames = await _signalr_handshake(websocket)
                    connected_at = time.monotonic()
                    subscription_messages = _load_subscription_messages(subscribe_env)
                    if stream_kind == "user":
                        subscription_messages = [
                            *_default_user_subscription_messages(self._account_id),
                            *subscription_messages,
                        ]
                    for message in subscription_messages:
                        await websocket.send(json.dumps(message) + _SIGNALR_RECORD_SEPARATOR)
                    for frame in initial_frames:
                        self._dispatch_frame(stream_kind, frame)

                    receiver = asyncio.create_task(
                        self._receive_messages(stream_kind, websocket)
                    )
                    refresh = (
                        asyncio.create_task(self._refresh_user_account_loop(websocket))
                        if stream_kind == "user" and self._on_user_account is not None
                        else None
                    )
                    tasks = {receiver, *([refresh] if refresh is not None else [])}
                    try:
                        done, _pending = await asyncio.wait(
                            tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in done:
                            await task
                    finally:
                        # Cancellation while waiting must reap BOTH children;
                        # otherwise each reconnect leaks a refresh coroutine.
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                if stream_kind == "user":
                    self._notify_user_disconnect()
                raise
            except Exception as exc:
                if stream_kind == "user":
                    self._notify_user_disconnect()
                if connected_at is not None and time.monotonic() - connected_at >= 60.0:
                    backoff_seconds = self._reconnect_base_seconds
                logger.warning(
                    "[hubs] disconnected kind=%s retry_in=%.1fs error_type=%s",
                    stream_kind,
                    backoff_seconds,
                    type(exc).__name__,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(self._reconnect_max_seconds, backoff_seconds * 2.0)

    async def _receive_messages(self, stream_kind: str, websocket: Any) -> None:
        async for raw_message in websocket:
            for frame in _decode_signalr_frames(raw_message):
                self._dispatch_frame(stream_kind, frame)
        raise ConnectionError(f"ProjectX {stream_kind} hub closed the connection")

    async def _refresh_user_account_loop(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(self._user_account_refresh_seconds)
            # GatewayUserAccount is snapshot-on-subscribe/change rather than a
            # periodic event. Re-subscribe before the five-minute safety TTL so
            # only a fresh hub-derived classification can renew eligibility.
            for message in (
                {"type": 1, "target": "UnsubscribeAccounts", "arguments": []},
                {"type": 1, "target": "SubscribeAccounts", "arguments": []},
            ):
                await websocket.send(
                    json.dumps(message) + _SIGNALR_RECORD_SEPARATOR
                )

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

        if stream_kind == "user":
            target = str(frame.get("target") or "").strip().casefold()
            if target == "gatewayuseraccount":
                argument_account_id = next(
                    (_positive_int(argument) for argument in arguments if not isinstance(argument, Mapping)),
                    None,
                )
                for argument in arguments:
                    if isinstance(argument, Mapping):
                        payload = dict(argument)
                        if argument_account_id is not None:
                            payload.setdefault("accountId", argument_account_id)
                        self._dispatch_user_account(payload)
                return
            if target and target != "gatewayuserposition":
                # Orders, trades, and account events are not position payloads.
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
                self._tracker.ingest_position_event(
                    payload,
                    user_id=self._user_id,
                    account_id=self._account_id,
                )
        except Exception as exc:
            circuit.record_failure(exc)
            snapshot = circuit.snapshot()
            logger.error(
                "projectx_hub_dispatch_failed",
                extra={
                    "reason_code": "projectx_hub_dispatch_error",
                    "error_type": type(exc).__name__,
                    "stream_kind": stream_kind,
                    "circuit_state": snapshot.state,
                    "consecutive_failures": snapshot.consecutive_failures,
                },
            )
            return

        circuit.record_success()

    def _dispatch_user_account(self, payload: Mapping[str, Any]) -> None:
        callback = self._on_user_account
        if callback is None:
            return
        account_id = _positive_int(
            payload.get("id", payload.get("accountId", payload.get("account_id")))
        )
        simulated = payload.get(
            "simulated", payload.get("isSimulated", payload.get("is_simulated"))
        )
        if account_id != self._account_id:
            # SubscribeAccounts may snapshot every account owned by this user;
            # this runner persists only its explicitly scoped account.
            return
        if not isinstance(simulated, bool):
            raise ValueError("GatewayUserAccount omitted a boolean simulated classification")
        callback({"id": account_id, "simulated": simulated})

    def _notify_user_disconnect(self) -> None:
        callback = self._on_user_disconnect
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            logger.error(
                "projectx_user_hub_disconnect_invalidation_failed",
                extra={"error_type": type(exc).__name__},
            )

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


async def _signalr_handshake(
    websocket: websockets.WebSocketClientProtocol,
    *,
    timeout_seconds: float = _HANDSHAKE_TIMEOUT_SECONDS,
) -> list[Mapping[str, Any]]:
    handshake_payload = {"protocol": "json", "version": 1}
    await websocket.send(json.dumps(handshake_payload) + _SIGNALR_RECORD_SEPARATOR)
    try:
        raw_response = await asyncio.wait_for(
            websocket.recv(),
            timeout=max(0.1, float(timeout_seconds)),
        )
    except asyncio.TimeoutError as exc:
        raise ConnectionError("ProjectX SignalR handshake timed out") from exc

    if isinstance(raw_response, bytes):
        response_text = raw_response.decode("utf-8", errors="strict")
    elif isinstance(raw_response, str):
        response_text = raw_response
    else:
        raise ConnectionError("ProjectX SignalR handshake returned an invalid frame type")
    chunks = [
        chunk
        for chunk in response_text.split(_SIGNALR_RECORD_SEPARATOR)
        if chunk.strip()
    ]
    if not chunks:
        raise ConnectionError("ProjectX SignalR handshake returned an empty response")
    try:
        response = json.loads(chunks[0])
    except json.JSONDecodeError as exc:
        raise ConnectionError("ProjectX SignalR handshake returned malformed JSON") from exc
    if not isinstance(response, Mapping):
        raise ConnectionError("ProjectX SignalR handshake returned an invalid response")
    if response.get("error"):
        raise ConnectionError("ProjectX SignalR handshake was rejected")
    if response:
        raise ConnectionError("ProjectX SignalR handshake response was not an acknowledgement")

    remaining = _SIGNALR_RECORD_SEPARATOR.join(chunks[1:])
    return _decode_signalr_frames(remaining) if remaining else []


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


def _default_user_subscription_messages(account_id: int | None) -> list[Mapping[str, Any]]:
    if account_id is None or account_id <= 0:
        return []
    return [
        {"type": 1, "target": "SubscribeAccounts", "arguments": []},
        {"type": 1, "target": "SubscribePositions", "arguments": [account_id]},
        {"type": 1, "target": "SubscribeOrders", "arguments": [account_id]},
        {"type": 1, "target": "SubscribeTrades", "arguments": [account_id]},
    ]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and value != normalized:
        return None
    return normalized if normalized > 0 else None


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
