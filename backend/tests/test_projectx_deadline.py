"""Real loopback transport regressions; never load credentials or call a broker."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import socket
import ssl
from threading import BoundedSemaphore, Event, Lock, Thread, enumerate as threads
from types import SimpleNamespace
import time
from urllib import request

import pytest

from app.services import projectx_client as client_module
from app.services import projectx_transport as transport
from app.services.projectx_client import ProjectXClient, ProjectXClientError, PROJECTX_ERROR_NETWORK


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    # Ignore operator proxy settings, keep the real deadline handlers/urllib I/O.
    build = request.build_opener
    monkeypatch.setattr(request, "build_opener", lambda *handlers: build(request.ProxyHandler({}), *handlers))
    monkeypatch.setattr(client_module, "_TOKEN_CACHE_BY_KEY", {})


@contextmanager
def serve(respond, *, tls=None):
    calls = []
    stopped = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append(self.path)
            try:
                respond(self, stopped)
            except OSError:
                pass  # Expected peer shutdown when a deadline closes the socket.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=.01))
    thread.start()
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", calls
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def client_at(url, timeout=.25):
    client = ProjectXClient(base_url="https://example.invalid", username="fixture", api_key="fixture", timeout_seconds=timeout)
    # Only fixtures bypass the public HTTPS-only endpoint validation.
    client.base_url = url
    return client


def reply(handler, payload, status=200):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def trickle(handler, stopped, *, status=200, phase="body"):
    if phase == "headers":
        data = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 80
    else:
        handler.send_response(status)
        handler.send_header("Content-Length", "1000")
        handler.end_headers()
        data = b" " * 1000
    for byte in data:
        handler.wfile.write(bytes([byte]))
        handler.wfile.flush()
        if stopped.wait(.02):
            break


def assert_timeout(call, *, unknown):
    before = time.monotonic()
    with pytest.raises(ProjectXClientError) as caught:
        call()
    elapsed = time.monotonic() - before
    assert elapsed < .85, f"total deadline did not interrupt I/O: {elapsed}"
    assert caught.value.status_code == 504
    assert caught.value.reason_code == PROJECTX_ERROR_NETWORK
    assert caught.value.submission_outcome_unknown is unknown
    assert not [t for t in threads() if t.name == "projectx-request-deadline"]
    return caught.value


@pytest.mark.parametrize("path", ["/api/Order/place", "/api/Order/cancel", "/api/Position/closeContract"])
@pytest.mark.parametrize("phase,status,unknown", [
    ("headers", 200, True), ("body", 200, True), ("body", 503, True),
    ("body", 400, False), ("body", 401, False),
])
def test_slow_response_stops_and_preserves_mutation_uncertainty(path, phase, status, unknown):
    with serve(lambda h, stop: trickle(h, stop, phase=phase, status=status)) as (url, calls):
        client = client_at(url)
        assert_timeout(lambda: client._request("POST", path, payload={}, with_auth=False), unknown=unknown)
        assert calls == [path]  # No retry or background order after returning.


def test_stalled_body_read_is_closed_and_nonmutation_is_not_ambiguous():
    def stall(h, stop):
        h.send_response(200)
        h.send_header("Content-Length", "100")
        h.end_headers()
        stop.wait(2)
    with serve(stall) as (url, calls):
        assert_timeout(lambda: client_at(url)._request("POST", "/api/Trade/search", with_auth=False), unknown=False)
        assert calls == ["/api/Trade/search"]


def test_authentication_deadline_prevents_order_dispatch():
    with serve(trickle) as (url, calls):
        assert_timeout(lambda: client_at(url)._request("POST", "/api/Order/place", payload={}, with_auth=True), unknown=False)
        assert calls == ["/api/Auth/loginKey"]


def test_authentication_and_401_retry_share_one_budget():
    auth_calls = 0
    def respond(h, stop):
        nonlocal auth_calls
        if h.path == "/api/Auth/loginKey":
            auth_calls += 1
            if auth_calls == 2:
                trickle(h, stop)
            else:
                stop.wait(.12)
                reply(h, {"token": "fixture", "expiresInSeconds": 3600})
        else:
            stop.wait(.12)
            reply(h, {"error": "expired"}, status=401)
    with serve(respond) as (url, calls):
        before = time.monotonic()
        assert_timeout(lambda: client_at(url, .4)._request("POST", "/api/Order/place", payload={}, with_auth=True), unknown=False)
        assert time.monotonic() - before < .6  # Not a fresh .4s for reauthentication.
        assert calls == ["/api/Auth/loginKey", "/api/Order/place", "/api/Auth/loginKey"]


def test_prompt_401_can_retry_once_and_success_closes_timer():
    orders = 0
    def respond(h, stop):
        nonlocal orders
        if h.path == "/api/Auth/loginKey":
            reply(h, {"token": "fixture", "expiresInSeconds": 3600})
        else:
            orders += 1
            reply(h, {"success": orders == 2}, status=401 if orders == 1 else 200)
    with serve(respond) as (url, calls):
        assert client_at(url, 2)._request("POST", "/api/Order/place", payload={}, with_auth=True) == {"success": True}
        assert orders == 2
    assert not [t for t in threads() if t.name == "projectx-request-deadline"]


def test_token_cache_contention_uses_request_budget(monkeypatch):
    lock = Lock()
    monkeypatch.setattr(client_module, "_TOKEN_LOCK", lock)
    with lock:
        assert_timeout(lambda: client_at("https://example.invalid", .08).get_access_token(), unknown=False)


def test_concurrent_requests_on_one_client_have_independent_deadlines():
    started = Event()
    results = []
    def respond(h, stop):
        if h.path == "/slow":
            started.set()
            trickle(h, stop)
        else:
            stop.wait(.2)
            reply(h, {"success": True})
    with serve(respond) as (url, calls):
        client = client_at(url, .4)
        def slow():
            try:
                client._request("POST", "/slow", with_auth=False)
            except ProjectXClientError as exc:
                results.append(exc.status_code)
        thread = Thread(target=slow)
        thread.start()
        try:
            assert started.wait(1)
            time.sleep(.25)
            # The first request expires during this request's successful read.
            assert client._request("POST", "/fast", with_auth=False) == {"success": True}
        finally:
            thread.join(timeout=2)
        assert not thread.is_alive()
        assert results == [504]


def test_actual_deadline_leaves_submission_unknown_for_reconciliation():
    from app.services import bot_service
    attempt = SimpleNamespace(raw_request={
        "accountId": 9001, "contractId": "CON.F.US.MNQ.Z26", "type": 2,
        "side": 0, "size": 1, "customTag": "deadline-fixture",
    }, status="pending", provider_order_id=None, rejection_reason=None, raw_response=None)
    def respond(h, stop):
        if h.path == "/api/Auth/loginKey":
            reply(h, {"token": "fixture", "expiresInSeconds": 3600})
        else:
            trickle(h, stop)
    with serve(respond) as (url, calls):
        bot_service._submit_order_attempt(client=client_at(url), order_attempt=attempt)
        assert calls == ["/api/Auth/loginKey", "/api/Order/place"]
        assert attempt.status == "submission_unknown"
        assert "requires reconciliation" in attempt.rejection_reason
        assert attempt.provider_order_id is None


def test_dns_timeouts_have_bounded_workers_and_never_connect_later(monkeypatch):
    release = Event()
    entered = []
    monkeypatch.setattr(transport, "_DNS_SLOTS", BoundedSemaphore(2))
    def slow_dns(*args):
        entered.append(args)
        release.wait(3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1))]
    def forbidden_connect(*args):
        pytest.fail("A timed-out resolver must never initiate a connection")
    monkeypatch.setattr(socket, "getaddrinfo", slow_dns)
    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    before_threads = set(threads())
    try:
        for _ in range(4):
            assert_timeout(lambda: client_at("https://example.invalid", .08).get_access_token(), unknown=False)
        assert len(entered) == 2
    finally:
        release.set()
        for thread in set(threads()) - before_threads:
            if thread.name == "projectx-dns":
                thread.join(timeout=2)
                assert not thread.is_alive()


def test_stalled_tls_handshake_obeys_deadline():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    stop = Event()
    def stall():
        peer, _ = server.accept()
        with peer:
            stop.wait(2)
    thread = Thread(target=stall)
    thread.start()
    try:
        assert_timeout(lambda: client_at(f"https://127.0.0.1:{server.getsockname()[1]}").get_access_token(), unknown=False)
    finally:
        stop.set()
        server.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.fixture
def tls_contexts(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loopback fixture")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
            .not_valid_after(now+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return server, ssl.create_default_context(cafile=str(cert_path))


@pytest.mark.parametrize("slow", [False, True])
def test_verified_tls_success_and_slow_body(monkeypatch, tls_contexts, slow):
    server_context, client_context = tls_contexts
    initialize = transport.DeadlineHTTPSHandler.__init__
    def trusted_fixture(self, deadline):
        initialize(self, deadline)
        self._context = client_context
    monkeypatch.setattr(transport.DeadlineHTTPSHandler, "__init__", trusted_fixture)
    def respond(h, stop):
        if slow:
            trickle(h, stop)
        else:
            reply(h, {"success": True})
    with serve(respond, tls=server_context) as (url, calls):
        client = client_at(url, .35 if slow else 2)
        if slow:
            assert_timeout(lambda: client._request("POST", "/api/Order/place", with_auth=False), unknown=True)
        else:
            assert client._request("POST", "/api/Order/place", with_auth=False) == {"success": True}
        assert calls == ["/api/Order/place"]


def test_untrusted_tls_certificate_is_still_rejected(tls_contexts):
    with serve(lambda h, stop: reply(h, {}), tls=tls_contexts[0]) as (url, calls):
        with pytest.raises(ProjectXClientError):
            client_at(url, 2).get_access_token()
        assert calls == []
