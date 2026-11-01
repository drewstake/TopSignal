"""Calculate saved trade excursions and optionally enable the independent recorder.

Run from backend: .venv/Scripts/python tools/backfill_trade_excursions.py --account 123 --enable-tracking
No orders, bot settings, historical cloud rows, or credentials are changed.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", type=int, required=True)
    parser.add_argument("--owner", help="Required if the external account ID has multiple owners")
    parser.add_argument("--enable-tracking", action="store_true")
    parser.add_argument("--live", action="store_true", help="Use the ProjectX live historical market-data feed")
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.models import Account
    from app.main import _projectx_client_for_user_without_open_transaction
    from app.services.historical_trade_excursions import backfill, enable_tracking, location
    with SessionLocal() as db:
        query = db.query(Account).filter(Account.provider == "projectx", Account.external_id == str(args.account))
        if args.owner:
            query = query.filter(Account.user_id == args.owner)
        accounts = query.all()
        if len(accounts) != 1:
            parser.error("Account must resolve to exactly one owner; supply --owner if necessary")
        owner = str(accounts[0].user_id)
        if accounts[0].trade_data_source != "projectx":
            parser.error("This recorder requires a connected ProjectX account")
        client = _projectx_client_for_user_without_open_transaction(db, user_id=owner)
    result = backfill(owner, args.account, client=client, session_factory=SessionLocal, live=args.live)
    print(result)
    print("Local result store:", location())
    if args.enable_tracking:
        enable_tracking(owner, args.account, live=args.live)
        print("Read-only tracking enabled; the running API starts the recorder within five seconds.")


if __name__ == "__main__":
    main()
