"""Refresh the Expenses calendar in the dedicated 5174 development workspace."""

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.expense_schemas import CombineTrackerSuppressionsOut, ExpenseListOut
from app.payout_schemas import PayoutListOut


STORAGE = Path(__file__).resolve().parents[1] / "storage"
WORKSPACE = STORAGE / "peteamdpc-dev"
DATABASE = WORKSPACE / "topsignal.sqlite3"
HOST_API = "http://127.0.0.1:8000"


def request(session, method, path, **kwargs):
    response = session.request(method, HOST_API + path, timeout=(3, 20), allow_redirects=False, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"peteamdpc returned HTTP {response.status_code} for {path.split('?')[0]}")
    return response


def fetch_rows(session, name, schema):
    rows = {}
    total = None
    while total is None or len(rows) < total:
        response = request(session, "GET", f"/api/{name}?limit=500&offset={len(rows)}")
        page = schema.model_validate(response.json())
        if total is None:
            total = page.total
        if page.total != total or (not page.items and len(rows) < total):
            raise RuntimeError(f"Host {name} changed during pagination. Retry startup.")
        for item in page.items:
            if item.id in rows:
                raise RuntimeError(f"Duplicate host {name} ID during pagination. Retry startup.")
            row = item.model_dump(exclude={"amount"})
            for field in ("created_at", "updated_at"):
                value = row[field]
                if value.tzinfo:
                    row[field] = value.astimezone(timezone.utc).replace(tzinfo=None)
            rows[item.id] = row
    if len(rows) != total:
        raise RuntimeError(f"Incomplete host {name} response.")
    return [rows[key] for key in sorted(rows)]


def fetch_ledgers(session):
    expenses = fetch_rows(session, "expenses", ExpenseListOut)
    payouts = fetch_rows(session, "payouts", PayoutListOut)
    response = request(session, "GET", "/api/expenses/combine-tracker-suppressions")
    suppressed = CombineTrackerSuppressionsOut.model_validate(response.json()).account_ids
    return {"expenses": expenses, "payouts": payouts, "expense_suppressions": [
        {"source": "combine_tracker", "account_id": account_id} for account_id in sorted(suppressed)
    ]}


def download():
    with requests.Session() as session:
        session.trust_env = False
        session.headers["Origin"] = "http://127.0.0.1:4173"
        login = request(session, "POST", "/auth/local/device", json={},
                        headers={"X-TopSignal-Local-Access": "device"}).json()
        session.headers["Authorization"] = "Bearer " + login["access_token"]
        try:
            snapshot = fetch_ledgers(session)
            # Offset pages and the two ledgers come from independent host reads.
            if snapshot != fetch_ledgers(session):
                raise RuntimeError("Host ledgers changed during the read. Retry startup.")
            return snapshot
        finally:
            request(session, "POST", "/auth/local/logout", json={})


def seed_workspace():
    original = STORAGE / "offline" / DATABASE.name
    if DATABASE.exists() or not original.exists():
        return
    with TemporaryDirectory(dir=WORKSPACE) as directory:
        temp = Path(directory) / DATABASE.name
        with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temp)) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError("Local development database copy failed verification.")
        exclusions = original.with_suffix(".deleted-projectx-accounts.json")
        if exclusions.exists():
            shutil.copyfile(exclusions, DATABASE.with_suffix(".deleted-projectx-accounts.json"))
        temp.replace(DATABASE)


def preserve_ledgers(previous, directory):
    payload = json.dumps(previous, sort_keys=True, default=str, ensure_ascii=False).encode()
    digest = hashlib.sha256(payload).hexdigest()
    directory.mkdir(exist_ok=True)
    archive = directory / f"{digest}.json.gz"
    if not archive.exists():
        with TemporaryDirectory(dir=directory) as tmp:
            staged = Path(tmp) / archive.name
            staged.write_bytes(gzip.compress(payload))
            staged.replace(archive)
    if gzip.decompress(archive.read_bytes()) != payload:
        raise RuntimeError("Could not verify the local ledger backup.")


def apply_ledgers(engine, snapshot, archive_dir):
    from app.models import DEFAULT_USER_ID, Expense, ExpenseSuppression, Payout

    tables = [Expense.__table__, Payout.__table__, ExpenseSuppression.__table__]
    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        previous = {}
        scopes = {}
        for table in tables:
            table.create(conn, checkfirst=True)
            scope = table.c.user_id == DEFAULT_USER_ID
            if table.name == "expense_suppressions":
                scope &= table.c.source == "combine_tracker"
            scopes[table.name] = scope
            order = table.c.account_id if table.name == "expense_suppressions" else table.c.id
            previous[table.name] = [dict(row) for row in conn.execute(
                select(table).where(scope).order_by(order)
            ).mappings()]

        comparable = {}
        for name, rows in previous.items():
            excluded = {"user_id", "created_at"} if name == "expense_suppressions" else {"user_id"}
            comparable[name] = [{k: v for k, v in row.items() if k not in excluded} for row in rows]
        if comparable == snapshot:
            return
        preserve_ledgers(previous, archive_dir)
        for table in tables:
            conn.execute(table.delete().where(scopes[table.name]))
            rows = [dict(row, user_id=DEFAULT_USER_ID) for row in snapshot[table.name]]
            if rows:
                conn.execute(table.insert(), rows)


def main():
    url = make_url(os.environ.get("DATABASE_URL", ""))
    if (url.get_backend_name() != "sqlite" or Path(url.database or "").resolve() != DATABASE
            or os.getenv("TOPSIGNAL_OFFLINE_DEV") != "1"):
        raise RuntimeError("Use npm start or npm run dev:local to refresh the 5174 workspace.")

    from app.db import engine
    from app.services.historical_trade_excursions import recorder_lock

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    with recorder_lock(WORKSPACE / "pull.lock") as acquired:
        if not acquired:
            raise RuntimeError("Another local ledger refresh is running.")
        snapshot = download()
        size = len(json.dumps(snapshot, default=str).encode())
        seed = STORAGE / "offline" / DATABASE.name
        seed_bytes = seed.stat().st_size if seed.exists() and not DATABASE.exists() else 0
        if shutil.disk_usage(WORKSPACE).free < seed_bytes + 4 * size:
            raise RuntimeError("Insufficient local disk space for the workspace and ledger refresh.")
        seed_workspace()
        apply_ledgers(engine, snapshot, WORKSPACE / "ledger-backups")
        print(f"[LOCAL] Refreshed from peteamdpc: {len(snapshot['expenses'])} expenses, "
              f"{len(snapshot['payouts'])} payouts.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Validation and SQL exceptions can include complete financial records.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        sys.exit(f"Ledger refresh failed ({message}). Check the private host connection and retry.")
