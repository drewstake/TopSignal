"""Supervise release-owned quote replay. The child has no broker connection."""
import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta
import logging
import os
from pathlib import Path
import subprocess
import sys

from . import blocked_trades
from .topbot_depth import feed

logger = logging.getLogger(__name__)


def retain_captures():
    # Only the exact worker capture can resolve the trade. Extend it in short
    # increments while replay is outstanding, even when the account goes idle.
    if not blocked_trades.path().exists():
        return
    with closing(blocked_trades.connect(readonly=True)) as db:
        rows = db.execute("""SELECT DISTINCT owner,contract,json_extract(snapshot,'$.capture') AS capture
                             FROM signals WHERE status IN ('open','queued')""").fetchall()
    until = datetime.now(UTC) + timedelta(seconds=45)
    with feed.lock:
        for row in rows:
            stream = feed.streams.get((row["owner"], row["contract"]))
            capture = stream["state"].capture if stream else None
            if capture and capture.capture_id == row["capture"]:
                stream["retain_until"] = max(stream.get("retain_until", until), until)


async def supervise():
    if os.getenv("TOPSIGNAL_BOT_WORKER_ENABLED", "false").lower() != "true":
        return
    flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    while True:
        child = None
        try:
            child = subprocess.Popen([sys.executable, "-B", "-m", "app.services.blocked_replay"],
                stdin=subprocess.PIPE, cwd=str(Path(__file__).resolve().parents[2]), creationflags=flags)
            while child.poll() is None:
                await asyncio.sleep(10)
                await asyncio.to_thread(retain_captures)
        except Exception:
            logger.exception("blocked_replay_supervision_failed")
        finally:
            if child and child.stdin:
                child.stdin.close()
                try:
                    await asyncio.to_thread(child.wait, timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    await asyncio.to_thread(child.wait)
        await asyncio.sleep(5)
