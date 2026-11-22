import gzip
import json
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError

from app.models import Payout
from tools.pull_host_ledgers import apply_ledgers


def test_repeat_pull_preserves_edits_and_rolls_back_bad_snapshot(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    backups = tmp_path / "backups"
    payout = dict(id=5, payout_date=date(2026, 11, 1), amount_cents=500,
                  currency="USD", notes=None, created_at=datetime(2026, 11, 1),
                  updated_at=datetime(2026, 11, 1))
    snapshot = {"expenses": [], "payouts": [payout], "expense_suppressions": []}
    apply_ledgers(engine, snapshot, backups)
    first_backups = set(backups.iterdir())
    apply_ledgers(engine, snapshot, backups)
    assert set(backups.iterdir()) == first_backups

    with engine.begin() as conn:
        conn.execute(Payout.__table__.update().values(notes="local edit"))
    with pytest.raises(IntegrityError):
        apply_ledgers(engine, {**snapshot, "payouts": [payout, payout]}, backups)
    with engine.connect() as conn:
        assert conn.execute(select(Payout.notes)).scalar_one() == "local edit"

    apply_ledgers(engine, snapshot, backups)
    with engine.connect() as conn:
        assert conn.execute(select(Payout.notes)).scalar_one() is None
    archived, = set(backups.iterdir()) - first_backups
    assert json.loads(gzip.decompress(archived.read_bytes()))["payouts"][0]["notes"] == "local edit"
