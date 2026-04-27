from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

import websockets

from .projectx_client import ProjectXClient
from .streaming_pnl_tracker import StreamingPnlTracker

logger = logging.getLogger(__name__)

_SIGNALR_RECORD_SEPARATOR = "\x1e"
_MARKET_SUBSCRIBE_ENV = "PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE"
_USER_SUBSCRIBE_ENV = "PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE"


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
    ):
        self._tracker = tracker
        self._client_factory = client_factory
        self._market_hub_url = market_hub_url or os.getenv("PROJECTX_MARKET_HUB_URL")
        self._user_hub_url = user_hub_url or os.getenv("PROJECTX_USER_HUB_URL")
        self._reconnect_base_seconds = max(0.5, float(reconnect_base_seconds))
        self._reconnect_max_seconds = max(self._reconnect_base_seconds, float(reconnect_max_seconds))

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
        if stream_kind == "market":
            self._tracker.ingest_market_event(payload)
            return
        self._tracker.ingest_position_event(payload)


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
