import io
from email.message import Message
from urllib import error, request

import pytest

from app.services import credentialed_http as transport


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirects_never_forward_credentials_or_make_a_second_request(monkeypatch, code):
    opener = request.build_opener(transport.NoCredentialRedirect())
    monkeypatch.setattr(opener, "open", lambda *_a, **_k: pytest.fail("redirect forwarded credentials"))
    req = request.Request("https://trusted.invalid/resource", headers={"Authorization": "Bearer fixture", "X-goog-api-key": "fixture"})
    headers = Message()
    headers["Location"] = "https://untrusted.invalid/collect"
    with pytest.raises(error.HTTPError) as caught:
        opener.error("http", req, io.BytesIO(b""), code, "redirect", headers)
    caught.value.close()
    assert caught.value.code == code


@pytest.mark.parametrize("url", [
    "http://example.invalid", "file:///tmp/file", "https://user:password@example.invalid",
    "https://example.invalid?key=secret", "https://example.invalid/#secret",
    "https://example.invalid:99999", "https://example.invalid/white space",
])
def test_invalid_credential_destinations_fail_before_transport(monkeypatch, url):
    monkeypatch.setattr(request, "build_opener", lambda *_a: pytest.fail("unsafe URL opened"))
    with pytest.raises(transport.CredentialedHttpError):
        transport.open_credentialed_request(request.Request(url), timeout=10)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 121, None])
def test_invalid_timeouts_fail_before_transport(monkeypatch, timeout):
    monkeypatch.setattr(request, "build_opener", lambda *_a: pytest.fail("unsafe timeout opened"))
    with pytest.raises(transport.CredentialedHttpError):
        transport.open_credentialed_request(request.Request("https://example.invalid"), timeout=timeout)


def test_only_explicit_loopback_can_use_http():
    for host in ("127.0.0.1", "localhost", "[::1]"):
        transport.validate_credentialed_url(f"http://{host}:54321", allow_loopback_http=True)
        with pytest.raises(transport.CredentialedHttpError):
            transport.validate_credentialed_url(f"http://{host}:54321")
    with pytest.raises(transport.CredentialedHttpError):
        transport.validate_credentialed_url("http://localhost.attacker.invalid", allow_loopback_http=True)


def test_response_read_is_size_and_time_bounded(monkeypatch):
    assert transport.read_bounded(io.BytesIO(b"1234"), max_bytes=4, timeout=5) == b"1234"
    with pytest.raises(transport.CredentialedHttpError, match="too_large"):
        transport.read_bounded(io.BytesIO(b"12345"), max_bytes=4, timeout=5)
    ticks = iter([0, 0, 6])
    monkeypatch.setattr(transport, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError):
        transport.read_bounded(io.BytesIO(b"1234"), max_bytes=4, timeout=5)
