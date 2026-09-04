import os
import subprocess
import sys
from urllib.parse import quote

import pytest

from app.database_security import (
    production_database_connect_args,
    validate_postgres_tls_url,
    validate_production_database_configuration,
)


@pytest.fixture
def ca_file(tmp_path):
    # Configuration validation checks an explicit readable trust file. libpq
    # remains responsible for parsing certificates and verifying the peer.
    path = tmp_path / "provider-ca.pem"
    path.write_bytes(b"test CA fixture; never used for a connection")
    return path


def tls_url(ca_file, *, driver="postgresql+psycopg", host="db.example.invalid"):
    return f"{driver}://user:private-password@{host}/topsignal?sslmode=verify-full&sslrootcert={quote(str(ca_file), safe='')}"


@pytest.mark.parametrize("mode", [None, "disable", "allow", "prefer", "require", "verify-ca"])
def test_remote_database_requires_hostname_verified_tls(mode):
    url = "postgresql+psycopg://user:private-password@db.invalid/topsignal"
    if mode:
        url += f"?sslmode={mode}"
    with pytest.raises(RuntimeError, match="sslmode=verify-full") as caught:
        validate_postgres_tls_url(url, environ={})
    assert "private-password" not in str(caught.value)


def test_remote_database_requires_readable_explicit_ca(tmp_path):
    for path in ("", "system", "relative.pem", str(tmp_path / "missing.pem"), str(tmp_path)):
        url = "postgresql://user:private-password@db.invalid/topsignal?sslmode=verify-full&sslrootcert=" + quote(path, safe="")
        with pytest.raises(RuntimeError, match="readable absolute CA file") as caught:
            validate_postgres_tls_url(url, environ={})
        assert "private-password" not in str(caught.value)


@pytest.mark.parametrize("driver", ["postgresql", "postgres", "postgresql+psycopg"])
def test_remote_database_forces_verified_tls_and_disables_gss_bypass(ca_file, driver):
    options = validate_postgres_tls_url(tls_url(ca_file, driver=driver), environ={"PGSSLMODE": "disable", "PGGSSENCMODE": "prefer"})
    assert options == {"sslmode": "verify-full", "sslrootcert": str(ca_file), "gssencmode": "disable"}


@pytest.mark.parametrize("extra", [
    "host=remote.invalid", "hostaddr=203.0.113.1", "port=6543", "service=another",
    "servicefile=another", "dbname=another", "user=another", "password=another",
    "sslmode=disable", "sslrootcert=another", "HOST=remote.invalid",
])
def test_query_overrides_and_duplicate_security_options_are_rejected(ca_file, extra):
    with pytest.raises(RuntimeError, match="without duplicate or routing overrides"):
        validate_postgres_tls_url(tls_url(ca_file) + "&" + extra, environ={})


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_exact_loopback_may_run_without_tls_but_cannot_override_target(host):
    url = f"postgresql+psycopg://user:fixture@{host}/topsignal"
    assert validate_postgres_tls_url(url, environ={}) == {}
    with pytest.raises(RuntimeError, match="routing overrides"):
        validate_postgres_tls_url(url + "?hostaddr=203.0.113.1", environ={})
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        with pytest.raises(RuntimeError, match="routing overrides"):
            validate_postgres_tls_url(url, environ={name: "override"})


@pytest.mark.parametrize("host", ["localhost.attacker.invalid", "127.0.0.2", "127.1", "localhost,remote.invalid", "%31%32%37.0.0.1"])
def test_loopback_lookalikes_never_bypass_remote_tls(host):
    with pytest.raises(RuntimeError):
        validate_postgres_tls_url(f"postgresql://user:fixture@{host}/topsignal", environ={})


def test_gss_encryption_cannot_replace_required_tls(ca_file):
    with pytest.raises(RuntimeError, match="gssencmode=disable"):
        validate_postgres_tls_url(tls_url(ca_file) + "&gssencmode=prefer", environ={})


def test_production_validates_both_application_and_migration_urls(monkeypatch, ca_file):
    monkeypatch.setenv("TOPSIGNAL_ENV", "production")
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", tls_url(ca_file))
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://user:private-password@db.invalid/topsignal")
    with pytest.raises(RuntimeError, match="MIGRATION_DATABASE_URL.*verify-full"):
        validate_production_database_configuration()
    monkeypatch.setenv("MIGRATION_DATABASE_URL", tls_url(ca_file, driver="postgresql"))
    validate_production_database_configuration()
    assert production_database_connect_args(tls_url(ca_file))["sslmode"] == "verify-full"
    monkeypatch.setenv("TOPSIGNAL_ENV", "development")
    assert production_database_connect_args("postgresql://localhost/db") == {}


def test_production_engine_import_rejects_insecure_remote_url_before_engine_creation():
    env = dict(os.environ, TOPSIGNAL_ENV="production", PYTHON_DOTENV_DISABLED="1", DATABASE_URL="postgresql+psycopg://user:private-password@db.invalid/topsignal", MIGRATION_DATABASE_URL="")
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        env.pop(name, None)
    source = "import sqlalchemy; sqlalchemy.create_engine = lambda *a, **k: (_ for _ in ()).throw(AssertionError('engine creation reached')); import app.db"
    result = subprocess.run([sys.executable, "-c", source], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert "requires sslmode=verify-full" in result.stderr
    assert "engine creation reached" not in result.stderr
    assert "private-password" not in result.stderr


def test_engine_options_force_the_validated_remote_tls_policy(monkeypatch, ca_file):
    from app.db import _build_engine_options
    monkeypatch.setenv("TOPSIGNAL_ENV", "production")
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    options = _build_engine_options(tls_url(ca_file))
    assert options["connect_args"]["sslmode"] == "verify-full"
    assert options["connect_args"]["sslrootcert"] == str(ca_file)
    assert options["connect_args"]["gssencmode"] == "disable"
