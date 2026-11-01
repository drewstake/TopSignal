"""Provision local login for the existing owner of an account.

Run on the worker as an administrator after securing the destination directory.
Outputs a one-time code to an exclusively-created file, never stdout. The owner
chooses their password in the app. Does not modify cloud records or bot state.
"""
from pathlib import Path
import argparse
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", type=int, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--code-file", type=Path, required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from app.db import SessionLocal
    from app.models import Account
    from app.local_auth import provision
    if args.code_file.exists() or not args.code_file.is_absolute():
        parser.error("Code file must be a new absolute path in a protected directory")
    with SessionLocal() as db:
        account = db.query(Account).filter_by(provider="projectx", external_id=str(args.account)).one()
        owner = str(account.user_id)
    args.code_file.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(args.code_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as out:
        code = provision(args.store, owner, None)
        out.write(code + "\n")
    print("Local login provisioned for the existing account owner. Setup code expires in 24 hours.")


if __name__ == "__main__":
    main()
