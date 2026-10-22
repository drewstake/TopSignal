"""Read a bounded tail of local diagnostics, with tenant/account checks per row."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re

from ..bot_diagnostics import DiagnosticFileHandler, safe_fields
from ..models import BotConfig, BotRun

MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_SCAN_RECORDS = 20_000
EVENT_NAME = re.compile(r"(?:bot|projectx|mathematical)_[a-z0-9_]{1,150}\Z")


def diagnostic_path():
    # The production supervisor rewrites this handler's filename to RuntimeRoot.
    # Never accept a filesystem path from an HTTP request.
    for handler in logging.getLogger("app").handlers:
        if isinstance(handler, DiagnosticFileHandler):
            return Path(handler.baseFilename)
    return Path(__file__).resolve().parents[2] / "storage/logs/bot-diagnostics.jsonl"


def _utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _identifier(value):
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return None


def _tail(path):
    rows = []
    remaining = MAX_SCAN_BYTES
    limited = unavailable = found = False
    for index in range(31):
        candidate = path if index == 0 else path.with_name(f"{path.name}.{index}")
        try:
            with candidate.open("rb") as stream:
                found = True
                stream.seek(0, 2)
                size = stream.tell()
                take = min(size, remaining)
                stream.seek(size - take)
                data = stream.read(take)
                remaining -= len(data)
                if take < size:
                    # The first line is partial unless we read from byte zero.
                    data = data.partition(b"\n")[2]
                    limited = True
                for line in reversed(data.splitlines()):
                    if len(rows) >= MAX_SCAN_RECORDS:
                        limited = True
                        break
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            rows.append(row)
                    except (ValueError, UnicodeDecodeError):
                        continue  # A concurrent append/crash can leave a partial line.
        except FileNotFoundError:
            continue  # Rotation may move a file between open attempts.
        except OSError:
            unavailable = True
        if remaining <= 0 or len(rows) >= MAX_SCAN_RECORDS:
            limited = True
            break
    return rows, limited, unavailable, found


def _owned_ids(db, model, ids, *, user_id, account_id):
    owned = set()
    values = sorted(ids)
    for offset in range(0, len(values), 500):
        owned.update(row_id for (row_id,) in db.query(model.id).filter(
            model.id.in_(values[offset:offset + 500]), model.user_id == user_id,
            model.account_id == account_id,
        ).all())
    return owned


def read_bot_logs(db, *, user_id, account_id, hours=24, level=None, run_id=None, search="", limit=100):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    candidates, scan_limited, read_error, found = _tail(diagnostic_path())
    config_ids = {_identifier(row.get("bot_config_id")) for row in candidates}
    run_ids = {_identifier(row.get("bot_run_id")) for row in candidates}
    configs = _owned_ids(db, BotConfig, config_ids - {None}, user_id=user_id, account_id=account_id)
    runs = _owned_ids(db, BotRun, run_ids - {None}, user_id=user_id, account_id=account_id)
    matched = []
    for row in candidates:
        # A numeric broker account alone is not tenant proof; linked database
        # identities or an explicit matching user AND account are required.
        if row.get("user_id") is not None and str(row["user_id"]) != str(user_id):
            continue
        if row.get("account_id") is not None and _identifier(row["account_id"]) != account_id:
            continue
        config_id, row_run_id = _identifier(row.get("bot_config_id")), _identifier(row.get("bot_run_id"))
        if config_id is not None and config_id not in configs:
            continue
        if row_run_id is not None and row_run_id not in runs:
            continue
        if not (config_id in configs or row_run_id in runs or (
            str(row.get("user_id")) == str(user_id) and _identifier(row.get("account_id")) == account_id
        )):
            continue
        if run_id is not None and row_run_id != run_id:
            continue
        name = row.get("event")
        if not isinstance(name, str) or not EVENT_NAME.fullmatch(name):
            continue
        try:
            timestamp = _utc(row["timestamp"])
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
        if not since <= timestamp <= now:
            continue
        severity = row.get("level")
        if severity not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} or (level and severity != level):
            continue
        fields = safe_fields(row)
        fields.pop("user_id", None)
        public = {"timestamp": timestamp.isoformat(), "event": name, "level": severity, **fields}
        if search and search.casefold() not in json.dumps(public).casefold():
            continue
        matched.append(public)
    matched.sort(key=lambda row: row["timestamp"], reverse=True)
    outcomes, risks = Counter(), Counter()
    timings = defaultdict(list)
    for row in matched:
        if row["event"] == "bot_evaluation_completed":
            outcomes[str(row.get("evaluation_status", "unknown"))] += 1
            codes = row.get("risk_codes")
            if isinstance(codes, list):
                risks.update(code for code in codes if isinstance(code, str))
        if row["event"] in {"bot_evaluation_timing", "bot_order_submission_finished", "bot_evaluation_stage_timing"}:
            duration = row.get("duration_ms")
            if isinstance(duration, (int, float)) and duration >= 0:
                timings[("stage:" + str(row.get("stage", "unknown"))) if row["event"] == "bot_evaluation_stage_timing" else row["event"]].append(duration)
    return {
        "account_id": account_id, "generated_at": now.isoformat(), "since": since.isoformat(),
        "status": "read_error" if read_error else "available" if found else "not_configured",
        "scan_limited": scan_limited, "has_more": len(matched) > limit,
        "entries": matched[:limit],
        "summary": {
            "matched_events": len(matched),
            "errors": sum(row["level"] in {"ERROR", "CRITICAL"} for row in matched),
            "warnings": sum(row["level"] == "WARNING" for row in matched),
            "evaluation_outcomes": dict(outcomes.most_common()),
            "risk_blocks": dict(risks.most_common(10)),
            "timings": {name: {"count": len(values), "mean_ms": round(sum(values) / len(values), 3),
                                "max_ms": round(max(values), 3)} for name, values in timings.items()},
        },
    }
