"""Deadline-bound urllib transport; no broker I/O outlives the calling thread."""
from __future__ import annotations

from functools import partial
from http.client import HTTPConnection, HTTPSConnection
import socket
from threading import BoundedSemaphore, Event, Lock, Thread, Timer
from time import monotonic
from urllib import request


# OS name resolution cannot be interrupted portably. Only resolution runs in
# background threads, with no queue and a process-wide bound. An expired caller
# never consumes a late DNS result or connects/sends from the resolver thread.
_DNS_SLOTS = BoundedSemaphore(4)


def _close_socket(sock: socket.socket) -> None:
    try:
        # close() alone does not interrupt a read through HTTPResponse.makefile.
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        sock.close()


class RequestDeadline:
    def __init__(self, seconds: float):
        self.expires_at = monotonic() + seconds
        self._lock = Lock()
        self._sockets: list[socket.socket] = []
        self._closed = False
        self._timer: Timer | None = None

    @property
    def expired(self) -> bool:
        return monotonic() >= self.expires_at

    def remaining(self) -> float:
        remaining = self.expires_at - monotonic()
        if remaining <= 0 or self._closed:
            raise TimeoutError("ProjectX total request deadline exceeded")
        return remaining

    def register(self, sock: socket.socket) -> None:
        with self._lock:
            if self._closed or self.expired:
                _close_socket(sock)
                raise TimeoutError("ProjectX total request deadline exceeded")
            self._sockets.append(sock)
            if self._timer is None:
                self._timer = Timer(self.remaining(), self._interrupt)
                self._timer.name = "projectx-request-deadline"
                self._timer.daemon = True
                self._timer.start()

    def _interrupt(self) -> None:
        with self._lock:
            self._closed = True
            sockets, self._sockets = self._sockets, []
        for sock in sockets:
            _close_socket(sock)

    def __enter__(self) -> RequestDeadline:
        return self

    def __exit__(self, *_args) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer.join()
        self._interrupt()

    def resolve(self, host: str, port: int) -> list:
        slots = _DNS_SLOTS
        if not slots.acquire(timeout=self.remaining()):
            raise TimeoutError("ProjectX DNS deadline exceeded")
        ready = Event()
        result: list = []
        failures: list[Exception] = []

        def resolve_only() -> None:
            try:
                result.extend(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
            except Exception as exc:
                failures.append(exc)
            finally:
                slots.release()
                ready.set()

        resolver = Thread(target=resolve_only, name="projectx-dns", daemon=True)
        try:
            resolver.start()
        except BaseException:
            slots.release()
            raise
        if not ready.wait(self.remaining()):
            raise TimeoutError("ProjectX DNS deadline exceeded")
        self.remaining()  # A late DNS answer must never result in a connection.
        if failures:
            raise failures[0]
        if not result:
            raise OSError("ProjectX DNS returned no addresses")
        return result

    def connect(self, address, timeout=None, source_address=None) -> socket.socket:
        host, port = address
        last_error = None
        for family, kind, protocol, _, destination in self.resolve(host, port):
            self.remaining()
            sock = socket.socket(family, kind, protocol)
            try:
                self.register(sock)
                sock.settimeout(self.remaining())
                if source_address:
                    sock.bind(source_address)
                sock.connect(destination)
                self.remaining()
                return sock
            except OSError as exc:
                last_error = exc
                _close_socket(sock)
                self.remaining()
        raise last_error or OSError("ProjectX connection failed")


class _DeadlineHTTPConnection(HTTPConnection):
    def __init__(self, *args, deadline: RequestDeadline, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = deadline.connect


class _DeadlineHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, deadline: RequestDeadline, **kwargs):
        super().__init__(*args, **kwargs)
        self._deadline = deadline
        self._create_connection = deadline.connect

    def connect(self):
        # Preserve stdlib proxy CONNECT, certificate/hostname verification and
        # SNI, but register the SSL socket before its first blocking handshake.
        HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self._tunnel_host or self.host,
            do_handshake_on_connect=False,
        )
        self._deadline.register(self.sock)
        self.sock.settimeout(self._deadline.remaining())
        self.sock.do_handshake()
        self._deadline.remaining()


class DeadlineHTTPHandler(request.HTTPHandler):
    def __init__(self, deadline: RequestDeadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, req):
        return self.do_open(partial(_DeadlineHTTPConnection, deadline=self.deadline), req)


class DeadlineHTTPSHandler(request.HTTPSHandler):
    def __init__(self, deadline: RequestDeadline):
        super().__init__()
        self.deadline = deadline

    def https_open(self, req):
        return self.do_open(
            partial(_DeadlineHTTPSConnection, deadline=self.deadline), req, context=self._context,
        )
