"""First install. Run on the authority host with an ACL-protected destination directory."""

import argparse
import json
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database_security import validate_postgres_tls_url
from app.scanner import store
from app.scanner.config import Options
from app.scanner.worker import AUTHORITY

ROLE = "topsignal_scanner"


def provision(runtime, config, contact):
    env = dotenv_values(runtime, interpolate=False)
    raw = env["MIGRATION_DATABASE_URL"]
    validate_postgres_tls_url(raw)
    admin = make_url(raw).set(drivername="postgresql+psycopg")
    app = make_url(env["DATABASE_URL"])
    if (
        admin.host not in {"localhost", "127.0.0.1", "::1"}
        or admin.database != app.database
        or admin.port != app.port
        or admin.host != app.host
    ):
        raise RuntimeError("Both connections must name the same local database")
    password = secrets.token_urlsafe(36)
    settings = {
        "database_url": admin.set(username=ROLE, password=password).render_as_string(
            hide_password=False
        ),
        "sec_user_agent": contact,
    }
    Options.model_validate(settings)
    engine = create_engine(
        admin, hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout = '4s'"))
        authority = c.execute(
            text(
                "SELECT source_id,active FROM topsignal_local.authority WHERE singleton"
            )
        ).first()
        if not authority or str(authority[0]) != AUTHORITY or not authority[1]:
            raise ValueError("Wrong authority")
        print(
            "Database bytes before scanner setup:",
            c.scalar(text("SELECT pg_database_size(current_database())")),
        )
        raw_conn = c.connection.driver_connection
        verifier = raw_conn.pgconn.encrypt_password(
            password.encode(), ROLE.encode(), b"scram-sha-256"
        ).decode()
        # Never adopt an existing login or overwrite an operator's secret file.
        raw_conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT CONNECTION LIMIT 8 PASSWORD {}"
            ).format(sql.Identifier(ROLE), sql.Literal(verifier))
        )
        store.meta.create_all(c)
        tables = sql.SQL(",").join(
            sql.Identifier("topsignal_local", t.name) for t in store.meta.sorted_tables
        )
        raw_conn.execute("GRANT USAGE ON SCHEMA topsignal_local TO topsignal_scanner")
        raw_conn.execute(
            "GRANT SELECT ON topsignal_local.authority TO topsignal_scanner"
        )
        raw_conn.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE ON {} TO topsignal_scanner").format(
                tables
            )
        )
        raw_conn.execute(
            sql.SQL("GRANT SELECT ON {} TO {},topsignal_backup").format(
                tables, sql.Identifier(app.username)
            )
        )
        for t in (store.events, store.jobs, store.usage):
            seq = sql.Identifier("topsignal_local", t.name + "_id_seq")
            raw_conn.execute(
                sql.SQL("GRANT USAGE ON SEQUENCE {} TO topsignal_scanner").format(seq)
            )
            raw_conn.execute(
                sql.SQL("GRANT SELECT ON SEQUENCE {} TO topsignal_backup").format(seq)
            )
        with config.open("x", encoding="utf8") as f:
            json.dump(settings, f, indent=2)
    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--sec-user-agent", default="")
    args = parser.parse_args()
    try:
        provision(args.runtime_env, args.config, args.sec_user_agent)
    except Exception as exc:
        raise SystemExit(
            f"Scanner provisioning failed ({type(exc).__name__}); inspect the new role and protected config before retry."
        ) from None
