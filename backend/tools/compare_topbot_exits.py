"""Offline, network-disabled worker for a registered exit-only observation."""
import argparse
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def forbidden(*args, **kwargs):
    raise RuntimeError("Research worker has no network access")


def main():
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    args = parser.parse_args()
    payload = json.loads(sys.stdin.read(32769))
    from app.services.topbot_study import simulate
    from app.services.topbot_study_store import quotes
    from app.services.topbot_depth import utc
    entry = payload["entry"]
    try:
        if not args.capture.is_file():
            raise FileNotFoundError("Registered capture is absent")
        result = simulate(entry, quotes(args.capture, payload["owner"], payload["contract"],
                          utc(entry["entry_at"]), utc(entry["strategy_snapshot"]["research_cutoff"])))
        result["capture_id"] = args.capture.stem
    except (FileNotFoundError, ValueError) as exc:
        result = simulate(entry, [], gap_reason="evidence_unavailable_"+type(exc).__name__)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
