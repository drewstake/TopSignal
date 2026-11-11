"""Synthetic sizing in a disposable directory; never touches configured recordings."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from app.services import trade_progress_store as store
    from app.services.trade_progress import CADENCE, ProgressEngine
    previous = os.environ.get("TOPSIGNAL_DATABENTO_CACHE_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="topsignal-progress-sizing-") as directory:
            os.environ["TOPSIGNAL_DATABENTO_CACHE_DIR"] = directory
            at = 1700000000
            engine = ProgressEngine("synthetic-owner", 1, {"MNQ": 2}, clock=lambda: at)
            engine.connected = {"market": True, "user": True}
            engine.reconciled = True
            engine.position(dict(id="synthetic",accountId=1,contractId="CON.F.US.MNQ.Z26",
                                 creationTimestamp=at,netQty=2,averagePrice=21000,timestamp=at), at)
            engine.flush()
            peak_wal = 0
            duration_seconds = 24 * 60 * 60
            periodic_samples = duration_seconds // CADENCE
            # Hold an idle connection, like concurrent readers, to retain WAL
            # allocation between writes. Never pin a long-running read snapshot.
            with closing(sqlite3.connect(store.location())) as db:
                db.execute("PRAGMA journal_mode=WAL")
                for i in range(periodic_samples):
                    at += CADENCE
                    engine.quote(dict(contractId="CON.F.US.MNQ.Z26",lastPrice=21000+(i%41)-20,timestamp=at),at)
                    engine.tick(at)
                    if (i+1)%360==0:
                        engine.flush()
                        peak_wal=max(peak_wal,Path(str(store.location())+"-wal").stat().st_size)
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                samples=db.execute("SELECT count(*) FROM trade_progress_samples").fetchone()[0]
                size=store.location().stat().st_size
                try:
                    indexes=db.execute("SELECT coalesce(sum(pgsize),0) FROM dbstat WHERE name IN (SELECT name FROM sqlite_master WHERE type='index')").fetchone()[0]
                except sqlite3.OperationalError:
                    indexes=None  # Windows SQLite may omit dbstat; total size still includes every index.
                result=dict(synthetic=True,sample_interval_seconds=CADENCE,duration_seconds=duration_seconds,
                            periodic_samples=periodic_samples,total_samples=samples,database_bytes=size,
                            index_bytes=indexes,indexes_included=True,peak_wal_bytes=peak_wal,bytes_per_sample=size/samples,
                            estimated_bytes_per_open_hour=size/24,estimated_bytes_per_24h_position_year=size*365)
                print(json.dumps(result,indent=2))
    finally:
        if previous is None:
            os.environ.pop("TOPSIGNAL_DATABENTO_CACHE_DIR",None)
        else:
            os.environ["TOPSIGNAL_DATABENTO_CACHE_DIR"] = previous


if __name__ == "__main__":
    main()
