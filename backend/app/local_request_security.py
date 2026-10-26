"""Request boundary for explicitly anonymous, loopback-only development."""
from ipaddress import ip_address
from urllib.parse import urlsplit


def _loopback(value: str) -> bool:
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def anonymous_request_error(request, origin_allowed) -> str | None:
    # Host validation prevents DNS rebinding from turning a hostile website
    # into a same-origin caller. Forwarded headers are not authority here.
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        return "local_host_not_allowed"
    try:
        host = urlsplit("http://" + hosts[0])
        if (host.username is not None or host.password is not None or host.path
                or host.query or host.fragment or not host.hostname
                or (host.port is not None and not 0 < host.port < 65536)
                or (host.hostname.lower() != "localhost" and not _loopback(host.hostname))):
            return "local_host_not_allowed"
    except ValueError:
        return "local_host_not_allowed"
    if request.client is None or not _loopback(request.client.host):
        return "local_client_not_allowed"

    origin = request.headers.get("origin")
    if origin is not None:
        # Even GETs can refresh provider-backed rows. Apply this to every API
        # method, not only methods that conventionally modify records.
        if origin == "null" or not origin_allowed(origin):
            return "local_origin_not_allowed"
    elif request.headers.get("sec-fetch-site", "").lower() not in {"", "same-origin", "none"}:
        return "local_origin_required"
    # No-Origin CLI/supervisor calls are allowed only from loopback to a
    # loopback Host; browser requests still have the Origin/Fetch Metadata gate.
    return None
