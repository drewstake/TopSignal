from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.databento_ingestion import import_databento_archives  # noqa: E402
from app.services.databento_market_data import rebuild_volume_roll_schedule  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently import Databento MNQ definition/OHLCV DBN+zstd ZIP archives "
            "and rebuild the no-lookahead continuous-contract roll schedule."
        )
    )
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--root", default="MNQ")
    return parser


def main() -> int:
    args = _parser().parse_args()
    with SessionLocal() as db:
        results = import_databento_archives(
            db,
            args.archives,
            commit_batches=True,
        )
        decisions = rebuild_volume_roll_schedule(db, root_symbol=args.root)
        db.commit()
    print(
        json.dumps(
            {
                "imports": [result.__dict__ for result in results],
                "root_symbol": str(args.root).upper(),
                "roll_schedule_sessions": len(decisions),
                "rolls": sum(
                    decision.from_instrument_id is not None
                    and decision.from_instrument_id != decision.instrument_id
                    for decision in decisions
                ),
                "first_session": decisions[0].trading_date.isoformat(),
                "last_session": decisions[-1].trading_date.isoformat(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
