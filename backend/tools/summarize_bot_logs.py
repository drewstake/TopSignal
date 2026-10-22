"""Read local JSONL diagnostics without importing the app or contacting a broker."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def summarize(paths, *, run_id=None, correlation_id=None, since=None):
    events, statuses, risks, errors = Counter(), Counter(), Counter(), Counter()
    timings = defaultdict(lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0})
    invalid = matched = 0
    first = last = None
    health = None
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    when = utc(row["timestamp"])
                    name = row["event"]
                    if not isinstance(name, str):
                        raise ValueError("Invalid event")
                except (ValueError, KeyError, TypeError, AttributeError):
                    invalid += 1
                    continue
                if since and when < since:
                    continue
                if run_id is not None and str(row.get("bot_run_id")) != str(run_id):
                    continue
                if correlation_id is not None and row.get("correlation_id") != correlation_id:
                    continue
                matched += 1
                first = min(first, when) if first else when
                last = max(last, when) if last else when
                events[name] += 1
                if row.get("level") in {"ERROR", "CRITICAL"}:
                    errors[name] += 1
                if name == "bot_evaluation_completed":
                    statuses[str(row.get("evaluation_status", "unknown"))] += 1
                    risks.update(str(code) for code in (row.get("risk_codes") or []))
                if name in {"bot_evaluation_timing", "bot_order_submission_finished", "bot_evaluation_stage_timing"}:
                    duration = row.get("duration_ms")
                    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
                        item = timings['stage:' + str(row.get('stage', 'unknown')) if name == 'bot_evaluation_stage_timing' else name]
                        item["count"] += 1
                        item["total_ms"] += duration
                        item["max_ms"] = max(item["max_ms"], duration)
                if name in {"bot_worker_heartbeat", "bot_worker_state_changed"}:
                    if health is None or when >= utc(health["timestamp"]):
                        health = {key: row.get(key) for key in (
                            "timestamp", "worker_id", "state", "provider_status", "owns_lease",
                            "active_runs", "retrying_runs", "unresolved_live_submissions", "last_error_code",
                            "last_cycle_completed_at", "last_success_at")}
    return {
        "files_read": len(paths), "records_matched": matched, "invalid_lines": invalid,
        "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
        "events": dict(events.most_common()), "evaluation_outcomes": dict(statuses.most_common()),
        "risk_blocks": dict(risks.most_common()), "errors": dict(errors.most_common()),
        "timings": {name: {"count": item["count"],
                           "mean_ms": round(item["total_ms"] / item["count"], 3),
                           "max_ms": round(item["max_ms"], 3)} for name, item in timings.items()},
        "latest_worker_health": health,
        "note": "Observed log events only; evaluation outcomes may precede commit. Broker fills and saved audit rows remain authoritative.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--correlation-id")
    parser.add_argument("--since", type=utc, help="ISO timestamp; naive timestamps are interpreted as UTC")
    args = parser.parse_args()
    paths = sorted(path for path in args.log_dir.glob("bot-diagnostics.jsonl*")
                   if path.name == "bot-diagnostics.jsonl" or path.suffix[1:].isdigit())
    if not paths:
        parser.error("No bot-diagnostics.jsonl files found in the log directory")
    print(json.dumps(summarize(paths, run_id=args.run_id, correlation_id=args.correlation_id,
                               since=args.since), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
