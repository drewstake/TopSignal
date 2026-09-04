"""Validate effective PostgreSQL transport configuration before any connection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ROUTING_QUERY_KEYS = {"host", "hostaddr", "port", "service", "servicefile", "dbname", "user", "password"}
_ROUTING_ENV_KEYS = {"PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"}


def validate_postgres_tls_url(
    value: str, *, label: str = "DATABASE_URL", environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Require hostname-verified TLS with an explicit CA for non-loopback hosts.

    Return libpq keyword overrides so ambient SSL/GSS settings cannot weaken a
    validated remote URL. Error messages intentionally never include URL values.
    """
    environment = os.environ if environ is None else environ
    try:
        parts = urlsplit(value)
        port = parts.port
        if (
            parts.scheme not in {"postgres", "postgresql", "postgresql+psycopg"}
            or not parts.hostname or not parts.path.lstrip("/") or parts.fragment
            or any(character.isspace() or ord(character) < 32 for character in value)
            or (port is not None and not 1 <= port <= 65535)
            or "," in parts.hostname or "%" in parts.hostname
        ):
            raise ValueError("invalid URL")
        entries = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        query: dict[str, str] = {}
        for key, item in entries:
            normalized = key.lower()
            if key != normalized or normalized in query or normalized in _ROUTING_QUERY_KEYS:
                raise ValueError("ambiguous or overridden connection target")
            query[normalized] = item
        if any(environment.get(name, "").strip() for name in _ROUTING_ENV_KEYS):
            raise ValueError("ambient connection target override")
    except (TypeError, ValueError):
        raise RuntimeError(f"{label}: production PostgreSQL URL requires one explicit host/database without duplicate or routing overrides") from None

    if parts.hostname.lower() in _LOOPBACK_HOSTS:
        return {}
    if query.get("sslmode") != "verify-full":
        raise RuntimeError(f"{label}: remote production PostgreSQL requires sslmode=verify-full")
    if query.get("gssencmode", "disable") != "disable":
        raise RuntimeError(f"{label}: remote production PostgreSQL requires gssencmode=disable to enforce TLS")
    ca_value = query.get("sslrootcert", "")
    try:
        ca_path = Path(ca_value)
        if not ca_value or not ca_path.is_absolute() or not ca_path.is_file():
            raise ValueError("CA file required")
        with ca_path.open("rb") as certificate_file:
            if not certificate_file.read(1):
                raise ValueError("empty CA file")
    except (OSError, ValueError):
        raise RuntimeError(f"{label}: remote production PostgreSQL requires sslrootcert naming a readable absolute CA file") from None
    return {"sslmode": "verify-full", "sslrootcert": str(ca_path), "gssencmode": "disable"}


def production_database_connect_args(value: str, *, label: str = "DATABASE_URL") -> dict[str, str]:
    if os.getenv("TOPSIGNAL_ENV", "").strip().lower() != "production":
        return {}
    return validate_postgres_tls_url(value, label=label)


def validate_production_database_configuration() -> None:
    if os.getenv("TOPSIGNAL_ENV", "").strip().lower() != "production":
        return
    for name in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if value:
            validate_postgres_tls_url(value, label=name)
