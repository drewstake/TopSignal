"""Local-only operation regressions. Never import or launch the trading app."""

import asyncio
import importlib.util
import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import threading
import time

import pytest

from app.production_logging import SafeAccessFormatter, SafeFormatter, redact


ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("message,secrets", [
    ("request https://broker.invalid/path?access_token=secret-query#sensitive", ["secret-query", "sensitive"]),
    ("failed postgresql+psycopg://user:database-password@db.invalid/db", ["user:", "database-password"]),
    ("Authorization: Bearer secret-bearer", ["secret-bearer"]),
    ("{'api_key': 'secret-key', 'refresh_token': 'secret-refresh'}", ["secret-key", "secret-refresh"]),
    ("token=secret-token", ["secret-token"]),
    ("credential received eyJhbGciOiJIUzI1NiJ9.c2VjcmV0.c2lnbmF0dXJl", ["eyJhbGci", "c2VjcmV0"]),
])
def test_redacts_credentials_and_url_queries(message, secrets):
    safe = redact(message)
    assert all(secret not in safe for secret in secrets)


def test_exception_text_is_redacted():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeFormatter("%(message)s"))
    logger = logging.Logger("isolated")
    logger.addHandler(handler)
    try:
        raise RuntimeError("password=do-not-log-this")
    except RuntimeError:
        logger.exception("provider failed")
    assert "do-not-log-this" not in stream.getvalue()
    assert "RuntimeError" in stream.getvalue()


def test_uvicorn_access_formatter_retains_status_without_query_or_record_mutation():
    formatter = SafeAccessFormatter('%(client_addr)s "%(request_line)s" %(status_code)s', use_colors=False)
    args = ("127.0.0.1:1234", "GET", "/oauth?access_token=secret", "1.1", 503)
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s - %s %s HTTP/%s %s", args, None)
    result = formatter.format(record)
    assert '"GET /oauth HTTP/1.1" 503' in result
    assert "secret" not in result
    assert record.args == args


def test_production_logging_is_size_bounded_and_usable(tmp_path):
    config = json.loads((ROOT / "backend" / "logging.production.json").read_text())
    for name in ("rotating_file", "rotating_access_file"):
        options = config["handlers"][name]
        assert options["class"] == "logging.handlers.RotatingFileHandler"
        assert options["maxBytes"] == 10 * 1024 * 1024
        assert 1 <= options["backupCount"] <= 14
    # Exercise real rollover with a small threshold, not just configuration text.
    path = tmp_path / "operations.log"
    handler = RotatingFileHandler(path, maxBytes=64, backupCount=2)
    handler.setFormatter(SafeFormatter("%(message)s"))
    try:
        for _ in range(20):
            handler.emit(logging.LogRecord("test", 20, "", 0, "password=secret extra diagnostic data", (), None))
    finally:
        handler.close()
    assert len(list(tmp_path.glob("operations.log*"))) == 3
    assert all("secret" not in p.read_text() for p in tmp_path.iterdir())


def test_backend_shutdown_file_gracefully_stops_and_drains_watcher(tmp_path):
    module = load_script("serve-production-backend")
    marker = tmp_path / "shutdown.request"

    class Server:
        should_exit = False
        entered = False
        drained = False

        async def serve(self):
            self.entered = True
            marker.write_text("stop")
            while not self.should_exit:
                await asyncio.sleep(0.001)
            self.drained = True

    async def exercise():
        server = Server()
        await asyncio.wait_for(module.serve_until_stopped(server, marker, 0.001), 1)
        assert server.entered and server.drained
        assert len(asyncio.all_tasks()) == 1
        # A preexisting stop request never starts a server.
        second = Server()
        await module.serve_until_stopped(second, marker)
        assert not second.entered

    asyncio.run(exercise())


def test_backend_watcher_is_cleaned_up_after_server_failure(tmp_path):
    module = load_script("serve-production-backend")

    class Server:
        should_exit = False

        async def serve(self):
            raise RuntimeError("simulated startup failure")

    async def exercise():
        with pytest.raises(RuntimeError, match="simulated"):
            await module.serve_until_stopped(Server(), tmp_path / "stop", 0.001)
        assert len(asyncio.all_tasks()) == 1

    asyncio.run(exercise())


def test_backend_stop_latch_and_unreadable_control_state_fail_closed(tmp_path):
    module = load_script("serve-production-backend")
    marker = tmp_path / "STOP"
    marker.write_text("operator stop")
    assert module.stop_requested(tmp_path / "shutdown.request", marker)

    class Unreadable:
        def stat(self):
            raise PermissionError("control directory unavailable")

    assert module.stop_requested(Unreadable())
    assert not module.stop_requested(tmp_path / "missing", None)


def test_frontend_connection_slots_bound_and_release_after_disconnect():
    module = load_script("serve-production-frontend")
    accepted = threading.Event()

    class SlowHandler:
        def __init__(self, request, *_args):
            accepted.set()
            try:
                request.recv(1)
            except OSError:
                pass

    server = module.BoundedFrontendServer(("127.0.0.1", 0), SlowHandler, max_connections=1)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    first = socket.create_connection(server.server_address, timeout=1)
    try:
        assert accepted.wait(1)
        assert not server._slots.acquire(blocking=False)
        with socket.create_connection(server.server_address, timeout=1) as second:
            assert second.recv(1) == b""
        first.close()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if server._slots.acquire(blocking=False):
                server._slots.release()
                break
            time.sleep(0.01)
        else:
            pytest.fail("connection slot was leaked")
    finally:
        first.close()
        server.shutdown()
        server.server_close()
        thread.join(1)


def test_frontend_sockets_have_bounded_timeout():
    module = load_script("serve-production-frontend")
    server = module.BoundedFrontendServer(("127.0.0.1", 0), object)
    client = socket.create_connection(server.server_address, timeout=1)
    try:
        accepted, _ = server.get_request()
        try:
            assert accepted.gettimeout() == 10
        finally:
            accepted.close()
    finally:
        client.close()
        server.server_close()
