"""Read production logs locally or over SSH; never import or start the app."""
from __future__ import annotations

import argparse
import codecs
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time


STREAMS = {
    "research": "research-observations.jsonl",
    "app": "topsignal.log",
    "bot": "bot-diagnostics.jsonl",
    "supervisor": "supervisor.log",
    "access": "access.log",
    "frontend": "frontend-access.log",
    "security-out": "security-check.stdout.log",
    "security-error": "security-check.stderr.log",
    "migration-out": "migration.stdout.log",
    "migration-error": "migration.stderr.log",
}
READ_LIMIT = 1024 * 1024


class LogReader:
    """Reopen by name each poll so Windows writers can rename rotated files."""

    def __init__(self, path: Path, tail: int):
        self.path = path
        self.tail = tail
        self.identity = None
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def read(self) -> tuple[str, str | None]:
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            initial = self.identity is None
            replaced = not initial and (identity != self.identity or stat.st_size < self.offset)
            notice = None
            if initial or replaced:
                self.decoder.reset()
                self.offset = 0
                if replaced:
                    notice = "Log rotated or truncated; following the current file."
            self.identity = identity
            if initial:
                start = max(0, stat.st_size - READ_LIMIT)
                stream.seek(start)
                data = stream.read(READ_LIMIT)
                if start:
                    # Discard a potentially partial first line/UTF-8 character.
                    data = data.partition(b"\n")[2]
                    notice = "Initial tail limited to the newest 1 MiB."
                data = b"".join(data.splitlines(keepends=True)[-self.tail:]) if self.tail else b""
            else:
                stream.seek(self.offset)
                data = stream.read(READ_LIMIT)
            self.offset = stream.tell()
            return self.decoder.decode(data), notice


def inventory(log_dir: Path) -> None:
    print(f"Log directory: {log_dir}")
    print("Timestamps are UTC. Missing files mean no log has been written here yet.")
    for name, filename in STREAMS.items():
        path = log_dir / filename
        try:
            stat = path.stat()
        except FileNotFoundError:
            print(f"{name:16} MISSING  {filename}")
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        rotations = [p for p in log_dir.glob(filename + ".*") if p.suffix[1:].isdigit()]
        print(f"{name:16} {stat.st_size:10} bytes  {modified}  {filename}  ({len(rotations)} backups)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=Path(r"C:\ProgramData\TopSignal\runtime"))
    parser.add_argument("--stream", choices=STREAMS, default="app")
    parser.add_argument("--tail", type=int, default=100)
    parser.add_argument("--list", action="store_true", help="Show log presence, size, and freshness")
    parser.add_argument("--follow", action="store_true", help="Wait for new output; Ctrl+C disconnects the reader only")
    parser.add_argument("--duration", type=float, help="Stop following after this many seconds")
    args = parser.parse_args()
    if not 0 <= args.tail <= 10000:
        parser.error("--tail must be between 0 and 10000")
    if args.duration is not None and (not 0 < args.duration <= 86400 or not args.follow):
        parser.error("--duration requires --follow and must be between 0 and 86400 seconds")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    log_dir = args.runtime_root / "logs"
    try:
        if args.list:
            inventory(log_dir)
            return 0
        reader = LogReader(log_dir / STREAMS[args.stream], args.tail)
        deadline = time.monotonic() + args.duration if args.duration else None
        waiting = False
        while True:
            try:
                output, notice = reader.read()
                if waiting:
                    print("[log reader] Log is available again.", file=sys.stderr, flush=True)
                waiting = False
                if notice:
                    print(f"[log reader] {notice}", file=sys.stderr, flush=True)
                print(output, end="", flush=True)
            except FileNotFoundError:
                if not waiting:
                    print(f"[log reader] No log at {reader.path}. No records have been written for this stream yet.", file=sys.stderr, flush=True)
                waiting = True
                if not args.follow:
                    return 2
            if not args.follow or (deadline is not None and time.monotonic() >= deadline):
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        print(f"[log reader] Cannot read logs: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
