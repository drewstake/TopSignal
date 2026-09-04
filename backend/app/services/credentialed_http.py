"""Small fail-closed transport shared by non-broker credentialed HTTP clients."""

from __future__ import annotations

import math
from time import monotonic
from urllib import parse, request


class CredentialedHttpError(RuntimeError):
    pass


class NoCredentialRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even a same-origin redirect is unexpected for these API operations.
        # Returning None makes urllib raise HTTPError without a second request.
        return None


def validate_credentialed_url(url: str, *, allow_loopback_http: bool = False) -> None:
    try:
        parts = parse.urlsplit(url)
        port = parts.port
        loopback_http = (
            allow_loopback_http
            and parts.scheme == "http"
            and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        valid = (
            bool(parts.hostname)
            and (parts.scheme == "https" or loopback_http)
            and not parts.username and not parts.password
            and not parts.query and not parts.fragment
            and (port is None or 1 <= port <= 65535)
            and not any(character.isspace() for character in url)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise CredentialedHttpError("credentialed_http_url_requires_tls_without_userinfo_or_query")


def validate_timeout(timeout: float) -> float:
    try:
        parsed = float(timeout)
    except (TypeError, ValueError):
        raise CredentialedHttpError("credentialed_http_timeout_must_be_finite_and_between_0_and_120") from None
    if not math.isfinite(parsed) or not 0 < parsed <= 120:
        raise CredentialedHttpError("credentialed_http_timeout_must_be_finite_and_between_0_and_120")
    return parsed


def open_credentialed_request(req: request.Request, *, timeout: float, allow_loopback_http: bool = False):
    validate_credentialed_url(req.full_url, allow_loopback_http=allow_loopback_http)
    timeout = validate_timeout(timeout)
    return request.build_opener(NoCredentialRedirect()).open(req, timeout=timeout)


def read_bounded(response, *, max_bytes: int, timeout: float) -> bytes:
    deadline = monotonic() + validate_timeout(timeout)
    output = bytearray()
    # HTTPResponse.read1 performs a single underlying read, permitting a total
    # read deadline as well as the transport's individual socket timeout.
    read = getattr(response, "read1", response.read)
    while len(output) <= max_bytes:
        if monotonic() >= deadline:
            raise TimeoutError("credentialed_http_body_read_timed_out")
        chunk = read(min(65536, max_bytes + 1 - len(output)))
        if not chunk:
            return bytes(output)
        output.extend(chunk)
    raise CredentialedHttpError("credentialed_http_response_too_large")
