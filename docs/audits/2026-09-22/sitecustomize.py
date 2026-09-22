"""Audit-only guard: prevent Python processes from contacting external services."""
import socket

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _check(address):
    if isinstance(address, tuple) and address[0] not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Audit network guard rejected non-loopback connection")


def connect(self, address):
    _check(address)
    return _connect(self, address)


def connect_ex(self, address):
    _check(address)
    return _connect_ex(self, address)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
