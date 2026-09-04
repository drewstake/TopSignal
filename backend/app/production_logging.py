"""Bounded, credential-safe production log formatting."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit, urlunsplit

from uvicorn.logging import AccessFormatter


_URL = re.compile(r"(?:https?|wss?|postgres(?:ql)?(?:\+\w+)?):\/\/[^\s'\"<>]+", re.I)
_SECRET = re.compile(
    r"((?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|"
    r"client[_-]?secret|credential|encryption[_-]?key|token)\s*['\"]?\s*[:=]\s*)"
    r"(?:Bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", re.I,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]+", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def redact(message: str) -> str:
    """Drop URL userinfo/queries and conventional secret values, including exceptions."""
    def safe_url(match: re.Match[str]) -> str:
        try:
            parts = urlsplit(match.group())
            return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
        except ValueError:
            return "[redacted-url]"

    message = _URL.sub(safe_url, message)
    message = _BEARER.sub("Bearer [REDACTED]", message)
    message = _JWT.sub("[REDACTED-JWT]", message)
    message = _SECRET.sub(r"\1[REDACTED]", message)
    return message


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class SafeAccessFormatter(AccessFormatter):
    def formatMessage(self, record: logging.LogRecord) -> str:
        # Uvicorn supplies a five-item positional tuple; never persist query
        # strings (auth callbacks and signed links can contain credentials).
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, target, version, status = record.args
            safe_record = logging.makeLogRecord(record.__dict__.copy())
            safe_record.args = (client, method, str(target).split("?", 1)[0].split("#", 1)[0], version, status)
            return redact(super().formatMessage(safe_record))
        return redact(super().formatMessage(record))
