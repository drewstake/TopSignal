"""Read-only DOM evidence replay and trade timeline. No app startup or broker access."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("capture", type=Path)
    replay_parser.add_argument("--observation", type=Path, help="JSON containing the saved depth_entry observation")
    replay_parser.add_argument("--threshold", type=float)
    replay_parser.add_argument("--persistence-seconds", type=float)
    replay_parser.add_argument("--allow-code-mismatch", action="store_true", help="Exploration only; cannot certify original decisions")
    timeline = commands.add_parser("timeline")
    timeline.add_argument("audit", type=Path)
    timeline.add_argument("--account", type=int, required=True)
    timeline.add_argument("--entry", type=int, required=True)
    args = parser.parse_args()
    def offline(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.sendto", "socket.sendmsg"}:
            raise RuntimeError("offline_replay_network_disabled")
    sys.addaudithook(offline)
    from app.services.topbot_replay import replay, trade_timeline
    if args.command == "replay":
        result = replay(args.capture, observation=json.loads(args.observation.read_text(encoding="utf-8")) if args.observation else None,
                        threshold=args.threshold, persistence_seconds=args.persistence_seconds,
                        allow_code_mismatch=args.allow_code_mismatch)
    else:
        result = trade_timeline(args.audit, account_id=args.account, entry_id=args.entry)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
