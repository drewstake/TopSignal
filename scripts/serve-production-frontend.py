"""Serve the prebuilt TopSignal control UI on loopback only.

This intentionally small server exists so the production task does not use
Vite's development or preview servers. The release tree is expected to be
read-only to the service identity.
"""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class BoundedFrontendServer(ThreadingHTTPServer):
    """Cap slow/local clients so a browser cannot exhaust threads or sockets."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, *args, max_connections: int = 32, **kwargs):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class TopSignalFrontendHandler(SimpleHTTPRequestHandler):
    server_version = "TopSignalLocal"
    sys_version = ""
    access_logger: logging.Logger
    content_security_policy: str
    allowed_host: str

    def _host_is_allowed(self) -> bool:
        return self.headers.get("Host", "").strip().lower() == self.allowed_host

    def do_GET(self) -> None:
        if not self._host_is_allowed():
            self.send_error(421, "Loopback Host header required")
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self._host_is_allowed():
            self.send_error(421, "Loopback Host header required")
            return
        super().do_HEAD()

    def _request_path(self) -> str:
        return unquote(urlsplit(self.path).path)

    def _safe_candidate(self) -> Path | None:
        relative = self._request_path().lstrip("/").replace("/", os.sep)
        root = Path(self.directory).resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def send_head(self):  # type: ignore[no-untyped-def]
        candidate = self._safe_candidate()
        if candidate is None:
            self.send_error(404)
            return None
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if candidate.is_file():
            original_path = self.path
            self.path = "/" + candidate.relative_to(Path(self.directory).resolve()).as_posix()
            try:
                return super().send_head()
            finally:
                self.path = original_path

        # React Router routes are served by index.html. Missing assets retain a
        # real 404 so deployment mistakes do not masquerade as HTML responses.
        if "." not in Path(self._request_path()).name:
            original_path = self.path
            self.path = "/index.html"
            try:
                return super().send_head()
            finally:
                self.path = original_path
        self.send_error(404)
        return None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", self.content_security_policy)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Do not persist query strings; OAuth or other control URLs may carry
        # sensitive values there. The browser fragment is never sent over HTTP.
        request_path = self._request_path().replace("\r", "").replace("\n", "")
        self.access_logger.info("%s %s", self.client_address[0], request_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a TopSignal production frontend on loopback")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--log-file", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("The production frontend host must be exactly 127.0.0.1")
    for label, value in (("port", args.port), ("api-port", args.api_port)):
        if value < 1 or value > 65535:
            raise SystemExit(f"{label} must be between 1 and 65535")

    directory = Path(args.directory).resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"Prebuilt frontend index was not found in {directory}")

    log_file = Path(args.log_file).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    access_logger = logging.getLogger("topsignal.frontend.access")
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False
    handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    access_logger.handlers[:] = [handler]

    api_http = f"http://127.0.0.1:{args.api_port}"
    api_ws = f"ws://127.0.0.1:{args.api_port}"
    TopSignalFrontendHandler.access_logger = access_logger
    TopSignalFrontendHandler.allowed_host = f"127.0.0.1:{args.port}"
    TopSignalFrontendHandler.content_security_policy = "; ".join(
        (
            "default-src 'self'",
            "base-uri 'self'",
            f"connect-src 'self' {api_http} {api_ws} https://*.supabase.co wss://*.supabase.co",
            "font-src 'self' data:",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "img-src 'self' data: blob:",
            "object-src 'none'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
        )
    )

    def handler_factory(*handler_args, **handler_kwargs):  # type: ignore[no-untyped-def]
        return TopSignalFrontendHandler(
            *handler_args,
            directory=str(directory),
            **handler_kwargs,
        )

    server = BoundedFrontendServer((args.host, args.port), handler_factory)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
