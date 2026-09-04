from __future__ import annotations

import asyncio
from collections import deque

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from starlette.routing import Route

from app.request_limits import RequestBodyLimitMiddleware


UPLOAD_PATHS = (
    "/api/accounts/7/journal/11/images",
    "/api/accounts/7/trade-imports/preview",
)


def test_slow_body_times_out_without_reaching_handler():
    sent = []

    async def downstream(*_args):
        pytest.fail("An incomplete request must not reach a mutating handler")

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    app = RequestBodyLimitMiddleware(downstream, max_body_bytes=100, body_timeout_seconds=0.01)
    asyncio.run(app(_scope("/api/bots/1/start"), receive, send))
    assert sent[0]["status"] == 408
    assert b"request_body_timeout" in sent[1]["body"]


def test_many_tiny_frames_are_replayed_as_one_bounded_body():
    async def exercise():
        remaining = 1000

        async def receive():
            nonlocal remaining
            if remaining == 0:
                return {"type": "http.disconnect"}
            remaining -= 1
            return {"type": "http.request", "body": b"x", "more_body": remaining > 0}

        async def downstream(scope, replay, send):
            assert await replay() == {"type": "http.request", "body": b"x" * 1000, "more_body": False}
            assert await replay() == {"type": "http.disconnect"}

        async def send(_message):
            pass

        await RequestBodyLimitMiddleware(downstream, max_body_bytes=1000)(_scope("/api/expenses"), receive, send)

    asyncio.run(exercise())


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
def test_stream_stays_connected_after_request_body_ends(method, spec_version):
    """Exercise the real streaming response through the auth/CORS middleware shape."""
    async def exercise():
        incoming = asyncio.Queue()
        payload = b'{"instrument":"MNQ"}' if method == "POST" else b""
        incoming.put_nowait({"type": "http.request", "body": payload, "more_body": False})
        sent = []

        async def endpoint(request):
            assert await request.body() == payload

            async def events():
                assert not await request.is_disconnected()
                yield ": connected\n\n"
                await asyncio.sleep(0.001)
                assert not await request.is_disconnected()
                yield 'event: progress\ndata: {"percent":50}\n\n'
                await asyncio.sleep(0.001)
                yield 'event: result\ndata: {"status":"complete"}\n\n'

            return StreamingResponse(events(), media_type="text/event-stream")

        async def pass_through(request, call_next):
            return await call_next(request)

        async def send(message):
            sent.append(message)

        app = Starlette(routes=[Route("/events", endpoint, methods=["GET", "POST"])])
        app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=100)
        app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"])
        app.add_middleware(BaseHTTPMiddleware, dispatch=pass_through)
        scope = _scope("/events", content_length=len(payload), content_type=b"application/json",
                       extra_headers=((b"origin", b"http://localhost:5173"),))
        scope["method"] = method
        scope["asgi"]["spec_version"] = spec_version
        await asyncio.wait_for(app(scope, incoming.get, send), timeout=2)
        assert sent[0]["status"] == 200
        assert dict(sent[0]["headers"])[b"access-control-allow-origin"] == b"http://localhost:5173"
        body = b"".join(message.get("body", b"") for message in sent)
        assert body == (b': connected\n\nevent: progress\ndata: {"percent":50}\n\n'
                        b'event: result\ndata: {"status":"complete"}\n\n')
        assert sent[-1].get("more_body", False) is False

    asyncio.run(exercise())


def test_real_disconnect_after_buffered_body_reaches_stream_listener():
    async def exercise():
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b"{}", "more_body": False})
        cleanup = asyncio.Event()
        sent = []

        async def endpoint(scope, receive, send):
            assert (await receive())["body"] == b"{}"

            async def events():
                try:
                    yield ": connected\n\n"
                    await asyncio.Event().wait()
                finally:
                    cleanup.set()

            await StreamingResponse(events(), media_type="text/event-stream")(scope, receive, send)

        async def send(message):
            sent.append(message)
            if message.get("body") == b": connected\n\n":
                incoming.put_nowait({"type": "http.disconnect"})

        app = RequestBodyLimitMiddleware(endpoint, max_body_bytes=100)
        await asyncio.wait_for(app(_scope("/events", content_length=2), incoming.get, send), timeout=2)
        assert cleanup.is_set()
        assert any(message.get("body") == b": connected\n\n" for message in sent)
        assert incoming.empty(), "The transport's actual disconnect must be consumed"

    asyncio.run(exercise())


def _scope(
    path: str,
    *,
    content_length: int | str | None = None,
    content_type: bytes = b"multipart/form-data; boundary=test-boundary",
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
) -> dict:
    headers = [(b"content-type", content_type), *extra_headers]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


def _run_request(
    *,
    path: str,
    messages: list[dict],
    max_body_bytes: int,
    content_length: int | str | None,
    content_type: bytes = b"multipart/form-data; boundary=test-boundary",
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
    cors_origin: str | None = None,
) -> tuple[list[dict], dict]:
    incoming = deque(messages)
    sent: list[dict] = []
    state = {"downstream_called": False, "receive_calls": 0, "body": b""}

    async def receive() -> dict:
        state["receive_calls"] += 1
        if incoming:
            return incoming.popleft()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    async def parser_and_handler(scope, downstream_receive, downstream_send) -> None:
        state["downstream_called"] = True
        body = bytearray()
        while True:
            message = await downstream_receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        state["body"] = bytes(body)
        await downstream_send(
            {"type": "http.response.start", "status": 204, "headers": []}
        )
        await downstream_send({"type": "http.response.body", "body": b""})

    application = RequestBodyLimitMiddleware(
        parser_and_handler,
        max_body_bytes=max_body_bytes,
    )
    if cors_origin is not None:
        application = CORSMiddleware(
            application,
            allow_origins=[cors_origin],
            allow_credentials=True,
        )
    asyncio.run(
        application(
            _scope(
                path,
                content_length=content_length,
                content_type=content_type,
                extra_headers=extra_headers,
            ),
            receive,
            send,
        )
    )
    return sent, state


@pytest.mark.parametrize("path", UPLOAD_PATHS)
def test_declared_oversized_upload_is_rejected_before_parser_or_handler(path):
    sent, state = _run_request(
        path=path,
        messages=[{"type": "http.request", "body": b"unused"}],
        max_body_bytes=100,
        content_length=101,
    )

    assert sent[0]["status"] == 413
    assert state["receive_calls"] == 0
    assert state["downstream_called"] is False


@pytest.mark.parametrize("path", UPLOAD_PATHS)
def test_chunked_oversized_upload_is_rejected_before_parser_or_handler(path):
    sent, state = _run_request(
        path=path,
        messages=[
            {"type": "http.request", "body": b"a" * 60, "more_body": True},
            {"type": "http.request", "body": b"b" * 41, "more_body": True},
            {"type": "http.request", "body": b"tail", "more_body": False},
        ],
        max_body_bytes=100,
        content_length=None,
    )

    assert sent[0]["status"] == 413
    assert state["receive_calls"] == 2
    assert state["downstream_called"] is False


def test_normal_upload_is_replayed_unchanged_to_parser_and_handler():
    body = (
        b"--test-boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="trades.csv"\r\n'
        b"Content-Type: text/csv\r\n\r\nId,PnL\r\n1,2\r\n"
        b"--test-boundary--\r\n"
    )
    sent, state = _run_request(
        path=UPLOAD_PATHS[1],
        messages=[
            {"type": "http.request", "body": body[:80], "more_body": True},
            {"type": "http.request", "body": body[80:], "more_body": False},
        ],
        max_body_bytes=512,
        content_length=len(body),
    )

    assert sent[0]["status"] == 204
    assert state["downstream_called"] is True
    assert state["body"] == body


@pytest.mark.parametrize("content_length", ["-1", "garbage", ""])
def test_malformed_content_length_is_rejected_fail_closed(content_length):
    sent, state = _run_request(
        path="/api/expenses",
        messages=[{"type": "http.request", "body": b"{}", "more_body": False}],
        max_body_bytes=100,
        content_length=content_length,
        content_type=b"application/json",
    )

    assert sent[0]["status"] == 400
    assert state["receive_calls"] == 0
    assert state["downstream_called"] is False


def test_duplicate_content_length_is_rejected_fail_closed():
    sent, state = _run_request(
        path="/api/expenses",
        messages=[{"type": "http.request", "body": b"{}", "more_body": False}],
        max_body_bytes=100,
        content_length=2,
        content_type=b"application/json",
        extra_headers=((b"content-length", b"2"),),
    )

    assert sent[0]["status"] == 400
    assert state["receive_calls"] == 0
    assert state["downstream_called"] is False


def test_declared_oversized_json_api_body_is_rejected_before_handler():
    sent, state = _run_request(
        path="/api/expenses",
        messages=[{"type": "http.request", "body": b"unused", "more_body": False}],
        max_body_bytes=100,
        content_length=101,
        content_type=b"application/json",
    )

    assert sent[0]["status"] == 413
    assert state["receive_calls"] == 0
    assert state["downstream_called"] is False


def test_chunked_oversized_json_api_body_is_rejected_before_handler():
    sent, state = _run_request(
        path="/api/expenses",
        messages=[
            {"type": "http.request", "body": b"a" * 60, "more_body": True},
            {"type": "http.request", "body": b"b" * 41, "more_body": False},
        ],
        max_body_bytes=100,
        content_length=None,
        content_type=b"application/json",
    )

    assert sent[0]["status"] == 413
    assert state["downstream_called"] is False


def test_limit_response_keeps_allowed_origin_cors_headers():
    origin = "https://app.example"
    sent, state = _run_request(
        path="/api/expenses",
        messages=[{"type": "http.request", "body": b"unused", "more_body": False}],
        max_body_bytes=100,
        content_length=101,
        content_type=b"application/json",
        extra_headers=((b"origin", origin.encode("ascii")),),
        cors_origin=origin,
    )

    assert sent[0]["status"] == 413
    headers = dict(sent[0]["headers"])
    assert headers[b"access-control-allow-origin"] == origin.encode("ascii")
    assert state["downstream_called"] is False
