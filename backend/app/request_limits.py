from __future__ import annotations

from collections import deque

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

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = int(max_body_bytes)

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
        buffered: deque[Message] = deque()
        received_bytes = 0
        while True:
            message = await receive()
            buffered.append(message)
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
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        if declared_length is not None and received_bytes != declared_length:
            await self._reject(
                scope,
                receive,
                send,
                status_code=400,
                detail="invalid_content_length",
            )
            return

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            return {"type": "http.disconnect"}

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
