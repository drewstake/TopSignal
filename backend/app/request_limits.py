from __future__ import annotations

import asyncio
import math

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _content_length(scope: Scope) -> int | None:
    values = [
        value
        for name, value in scope.get("headers", ())
        if name.lower() == b"content-length"
    ]
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("duplicate_content_length")
    try:
        text = values[0].decode("ascii").strip()
    except UnicodeDecodeError:
        raise ValueError("invalid_content_length") from None
    if not text or not text.isdecimal():
        raise ValueError("invalid_content_length")
    try:
        return int(text)
    except ValueError:
        raise ValueError("invalid_content_length") from None


class RequestBodyLimitMiddleware:
    """Bound every HTTP request body before routing or form/JSON parsing."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, body_timeout_seconds: float = 30) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        if not math.isfinite(body_timeout_seconds) or body_timeout_seconds <= 0:
            raise ValueError("body_timeout_seconds must be finite and positive")
        self.body_timeout_seconds = body_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        try:
            declared_length = _content_length(scope)
        except ValueError:
            await self._reject(
                scope,
                receive,
                send,
                status_code=400,
                detail="invalid_content_length",
            )
            return
        if declared_length is not None and declared_length > self.max_body_bytes:
            await self._reject(
                scope,
                receive,
                send,
                status_code=413,
                detail="request_body_too_large",
            )
            return

        # Unknown-length/chunked bodies cannot be rejected before parsing unless
        # the ASGI boundary reads them first. Buffering all bodies also verifies
        # Content-Length instead of trusting it; memory remains bounded by the
        # configured ceiling and accepted upload handlers already materialize
        # their at-most-10-MiB files.
        buffered = bytearray()
        received_bytes = 0
        deadline = asyncio.get_running_loop().time() + self.body_timeout_seconds
        while True:
            try:
                message = await asyncio.wait_for(
                    receive(), timeout=max(0, deadline - asyncio.get_running_loop().time())
                )
            except asyncio.TimeoutError:
                await self._reject(scope, receive, send, status_code=408, detail="request_body_timeout")
                return
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        status_code=413,
                        detail="request_body_too_large",
                    )
                    return
                # Collapse tiny/empty ASGI frames; a byte limit alone does not
                # bound the memory consumed by millions of queued messages.
                buffered.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        if declared_length is not None and received_bytes != declared_length:
            await self._reject(
                scope,
                receive,
                send,
                status_code=400,
                detail="invalid_content_length",
            )
            return

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(buffered), "more_body": False}
            # End of the request body is not a client disconnect. Streaming
            # responses must keep listening to the real transport; fabricating
            # a disconnect here cancels SSE before its headers/results arrive.
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
        )
        await response(scope, receive, send)
