"""Inspect and explicitly verify the local recording destination; never enrolls accounts.

Run on peteamdpc after inspecting Get-Volume/Get-Partition/Get-Disk. No trading
operations or production deployment are performed by this command.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-host")
    parser.add_argument("--verify-root")
    parser.add_argument("--verify-drive")
    args = parser.parse_args()
    # Load the same deployment environment as the API without starting it.
    from app import db as _db  # noqa: F401
    from app.services import trade_progress_store as store
    print(json.dumps(store.disk_report(), indent=2))
    supplied = (args.verify_host, args.verify_root, args.verify_drive)
    if any(supplied):
        if not all(supplied):
            parser.error("All three --verify-* arguments are required")
        result = store.verify_and_register(expected_host=args.verify_host, expected_root=args.verify_root,
                                           expected_drive=args.verify_drive)
        print(json.dumps(dict(verified=result), indent=2))


if __name__ == "__main__":
    main()
