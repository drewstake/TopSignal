"""Read-only, bounded operational history. Never expose raw server log text."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re

from .bot_log_reader import diagnostic_path, _tail, _utc

SUPERVISOR_BYTES = 512 * 1024
SUPERVISOR_PATTERNS = [
    (r"Starting TopSignal backend on 127\.0\.0\.1:\d+ and control UI on 127\.0\.0\.1:\d+\.", "INFO", "host_start", "Backend and dashboard starting."),
    (r"Liveness probe failed \(\d+/\d+\)\.", "ERROR", "backend_probe_failed", "The host could not reach the backend."),
    (r"Frontend probe failed \(\d+/\d+\)\.", "ERROR", "dashboard_probe_failed", "The host could not reach the dashboard."),
    (r"Worker health has been unhealthy for \d+ seconds\. This local log is not an external alert\.", "WARNING", "worker_unhealthy", "The bot worker failed its health check."),
    (r"Readiness is degraded; inspect authenticated runtime status and application logs\. Readiness is an operator signal and does not itself trigger restart\.", "WARNING", "readiness_degraded", "Backend readiness is degraded. Review the account logs below."),
    (r"(?:child process exit|backend exited with code -?\d+|frontend server exited with code -?\d+|persistent backend liveness failure|persistent frontend failure|persistent worker-health failure after \d+ seconds \(single bounded restart\)); restarting both child processes after [\d.]+ seconds\.", "WARNING", "host_restart", "The host is restarting the backend and dashboard after a failure."),
    (r"Stopped by operator; persistent STOP latch prevents restart after reboot\.", "INFO", "host_stopped", "The backend and dashboard were stopped by an operator."),
    (r"Graceful backend shutdown timed out; forcing process-tree termination\. Broker exposure must be inspected\.", "ERROR", "shutdown_timeout", "Backend shutdown timed out; the host stopped the process."),
]
FEED_STATES = {
    "connected": ("INFO", "Market-feed subscription connected."),
    "connecting": ("INFO", "Connecting to the market feed."),
    "reconnecting": ("WARNING", "Market feed interrupted; reconnecting."),
    "unavailable": ("ERROR", "Market feed unavailable."),
    "disconnected": ("WARNING", "Market feed disconnected."),
    "market_closed": ("INFO", "Market session closed; feed paused."),
}


def _entry(timestamp, source, event, level, message):
    content = f"{timestamp}|{source}|{event}|{message}"
    return dict(id=sha256(content.encode()).hexdigest()[:20], timestamp=timestamp,
                source=source, event=event, level=level, message=message)


def read_system_logs(*, user_id, hours=24, limit=200):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    path = diagnostic_path()
    entries = []
    found = failed = limited = False
    remaining = SUPERVISOR_BYTES
    # File paths are derived from the configured handler, never request input.
    for index in range(4):
        name = "supervisor.log" + (f".{index}" if index else "")
        try:
            with path.with_name(name).open("rb") as stream:
                found = True
                stream.seek(0, 2)
                size = stream.tell()
                take = min(size, remaining)
                stream.seek(size - take)
                data = stream.read(take)
                remaining -= len(data)
                if take < size:
                    limited = True
                    data = data.partition(b"\n")[2]
                for line in data.decode("utf-8-sig", errors="replace").splitlines():
                    timestamp, _, message = line.partition(" ")
                    try:
                        stamp = _utc(timestamp)
                    except (ValueError, TypeError, OverflowError):
                        continue
                    if not since <= stamp <= now:
                        continue
                    for pattern, level, event, public in SUPERVISOR_PATTERNS:
                        if re.fullmatch(pattern, message):
                            entries.append(_entry(stamp.isoformat(), "Host", event, level, public))
                            break
        except FileNotFoundError:
            continue
        except OSError:
            failed = True
        if remaining <= 0:
            limited = True
            break
    rows, feed_limited, feed_failed, feed_found = _tail(path)
    for row in rows:
        # Feed events require an explicit tenant identity. No global worker,
        # account, exception message, traceback or free-form log text is exposed.
        if row.get("user_id") != user_id or row.get("event") != "projectx_feed_state":
            continue
        state = row.get("state")
        if not isinstance(state, str) or state not in FEED_STATES:
            continue
        try:
            stamp = _utc(row["timestamp"])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if not since <= stamp <= now:
            continue
        level, message = FEED_STATES[state]
        source = "Order book" if row.get("stream_kind") == "depth" else "Market quotes"
        entries.append(_entry(stamp.isoformat(), source, state, level, message))
    entries = sorted({row["id"]: row for row in entries}.values(), key=lambda row: row["timestamp"], reverse=True)
    return dict(generated_at=now.isoformat(), entries=entries[:limit], has_more=len(entries) > limit,
                scan_limited=limited or feed_limited,
                sources={"host": "read_error" if failed else "available" if found else "not_configured",
                         "market_feed": "read_error" if feed_failed else "available" if feed_found else "not_configured"})
