"""Account-scoped exit-study status and the fixed take-profit comparison.

Presentation only. Reads registrations, saved results, replay-job state and
recorder status for one owner/account; never writes results, never pools other
experiments, and never changes trading configuration.
"""
from datetime import datetime, timezone
import math
import random
from statistics import mean

from .topbot_depth import utc
from .topbot_study import EXPERIMENT_ID, PROTOCOL, metrics

REPORT_VERSION = "tp-baseline-report-v1"
BASELINE_KEY = "1"  # 25-point take-profit, 25-point stop: the current fixed bracket.
MIN_ENTRIES = 30
MIN_SESSIONS = 5
STALE_SECONDS = 30
QUESTION = ("Would a larger fixed take-profit perform better than the current 25-point take-profit, "
            "keeping the stop fixed at 25 points?")

ATTEMPT_LABELS = {
    "blocked": ("blocked", "Blocked before submission · no order sent"),
    "error": ("submission_error", "Submission failed · no fill"),
    "rejected": ("rejected", "Rejected by broker · no fill"),
    "cancelled": ("not_filled", "Cancelled · not filled"),
    "dry_run": ("dry_run", "Dry run · no order"),
    "pending": ("submission_unresolved", "Submission in progress"),
    "submission_unknown": ("submission_unresolved", "Submission outcome unknown · reconciliation required"),
    "submitted": ("awaiting_fill", "Submitted · awaiting fill confirmation"),
}
NOT_TRADES = {"blocked", "submission_error", "rejected", "not_filled", "dry_run"}


def _clean_reason(text):
    if not text:
        return None
    text = " ".join(str(text).split())
    return text[:160]


def display_status(item, rejection_reason=None):
    """What actually happened to an attempt; blocked or failed attempts are not 'awaiting fill'."""
    if item.get("entry_at"):
        status = item.get("stats_status")
        if status == "complete":
            reason = (item.get("exit_reason") or "unknown").replace("_", " ")
            return {"kind": "closed", "label": f"Closed · {reason}", "reason": None}
        if status == "open":
            return {"kind": "open", "label": "Open position", "reason": None}
        if status == "awaiting_exit_fill":
            return {"kind": "awaiting_exit_fill", "label": "Exit fill not yet confirmed", "reason": None}
        return {"kind": "reconciliation_required", "label": f"Reconciliation required · {str(status).replace('_', ' ')}",
                "reason": _clean_reason(item.get("reconciliation", {}).get("reason") if isinstance(item.get("reconciliation"), dict) else None)}
    if item.get("stats_status") == "not_filled":
        return {"kind": "not_filled", "label": "Cancelled · not filled", "reason": _clean_reason(rejection_reason)}
    kind, label = ATTEMPT_LABELS.get(item.get("attempt_status"), ("unknown", "Attempt state unknown"))
    return {"kind": kind, "label": label, "reason": _clean_reason(rejection_reason)}


def _rows(db, owner, account, limit=5000):
    from ..models import BotOrderAttempt
    return db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == owner,
        BotOrderAttempt.account_id == account,
        BotOrderAttempt.raw_request["timeExit"]["runner"]["version"].as_integer() == 1
    ).order_by(BotOrderAttempt.id.desc()).limit(limit+1).all()


def exit_settings(db, owner, account, items, now=None):
    """Settings for future entries (resolved like the entry path) versus each open trade's saved protection."""
    from ..models import BotConfig
    from .topbot_strategy import exit_mode, selected
    from .topbot_runner import runner_plan
    now = now or datetime.now(timezone.utc)
    config = db.query(BotConfig).filter_by(user_id=owner, account_id=account,
               strategy_type="topbot_adaptive").order_by(BotConfig.id.desc()).first()
    future = None
    if config is not None and selected(config.strategy_params):
        params = config.strategy_params or {}
        mode = exit_mode(params, now)
        plan = runner_plan(now, exit_mode=mode)["runner"]
        future = {"exit_mode": mode, "take_profit_points": plan["take_profit_points"],
                  "initial_stop_points": plan["initial_stop_points"],
                  "trail_activation_points": plan["activation_points"] if mode == "trailing" else None,
                  "trail_points": plan["trail_points"] if mode == "trailing" else None,
                  "persistent": params.get("exit_mode") == "fixed",
                  "fixed_bracket_session": params.get("fixed_bracket_session"),
                  "bot_enabled": bool(config.enabled), "applies_to": "new entries",
                  "source": "Bot configuration, resolved by the same rule the entry path uses"}
    open_trades = []
    for item in items:
        if item.get("entry_at") and item.get("stats_status") in {"open", "awaiting_exit_fill"}:
            open_trades.append({k: item.get(k) for k in ("entry_id", "side", "entry_price", "exit_mode", "take_profit_points",
                                "initial_stop_points", "activation_points", "trail_points", "confirmed_stop",
                                "confirmed_target", "stop_verified_at")} |
                               {"source": "Saved with this order at entry; confirmed prices are broker read-backs"})
    return {"future_entries": future, "open_trades": open_trades, "as_of": now.isoformat()}


def augment_statistics(db, owner, account, stats):
    """Add truthful attempt labels and authoritative exit settings to the statistics payload."""
    from ..models import BotOrderAttempt
    ids = [item["entry_id"] for item in stats.get("items", [])]
    reasons = dict(db.query(BotOrderAttempt.id, BotOrderAttempt.rejection_reason).filter(
        BotOrderAttempt.user_id == owner, BotOrderAttempt.account_id == account, BotOrderAttempt.id.in_(ids)).all()) if ids else {}
    for item in stats.get("items", []):
        item["display_status"] = display_status(item, reasons.get(item["entry_id"]))
    stats["exit_settings"] = exit_settings(db, owner, account, stats.get("items", []))
    stats["account_id"] = account
    return stats


# ---------------------------------------------------------------------------
# Entry states and denominators
# ---------------------------------------------------------------------------

def _enrollments(owner, account):
    from . import topbot_study_store as store
    with store.connection(owner) as db:
        if db is None:
            return {}
        return {r[0]: {"ordinal": r[1], "phase": r[2]} for r in db.execute(
            "SELECT entry,ordinal,phase FROM enrollments WHERE account=? AND experiment=?", (account, EXPERIMENT_ID))}


def classify_entries(owner, account, items, results, job_state, activity, now):
    from .topbot_study_jobs import FAILED_AFTER
    by_result = {r["entry_id"]: r for r in results}
    active = activity.get("active") or {}
    retention = activity.get("retention") or {}
    entries, not_trades, unresolved = [], {}, []
    for item in items:
        snapshot = item.get("strategy_snapshot") or {}
        experiment = snapshot.get("experiment_id")
        status = display_status(item)
        if not item.get("entry_at"):
            if status["kind"] in NOT_TRADES:
                not_trades[status["kind"]] = not_trades.get(status["kind"], 0)+1
            elif experiment:
                unresolved.append({"entry_id": item["entry_id"], "kind": status["kind"], "label": status["label"]})
            continue
        if not experiment:
            continue
        cutoff = utc(snapshot["research_cutoff"]) if snapshot.get("research_cutoff") else None
        row = {"entry_id": item["entry_id"], "entry_at": item.get("entry_at"), "exit_at": item.get("exit_at"),
               "exit_reason": item.get("exit_reason"), "side": item.get("side"), "cutoff": cutoff.isoformat() if cutoff else None,
               "experiment_id": experiment, "strategy_class": item.get("strategy_class")}
        job = job_state.get((account, item["entry_id"], EXPERIMENT_ID)) or {}
        if experiment != EXPERIMENT_ID:
            row["state"] = "prior_experiment"
        elif item["entry_id"] in by_result:
            result = by_result[item["entry_id"]]
            row["state"] = "completed_valid" if result["paired_valid"] else "completed_excluded"
            row["reasons"] = sorted({v.get("reason", v["status"]) for v in result["variants"] if v["status"] != "resolved"})
            row["replay"] = result.get("replay")
        elif cutoff is not None and now < cutoff:
            row["state"] = "observing"
            row["window_seconds_remaining"] = round((cutoff-now).total_seconds())
            row["retention"] = retention.get(item["entry_id"])
        elif active.get("entry_id") == item["entry_id"]:
            row["state"] = "processing"
            row["progress"] = {k: active.get(k) for k in ("phase", "events", "received_at", "started_at")}
        elif job.get("consecutive_failures", 0) >= FAILED_AFTER:
            row["state"] = "failed"
        elif job.get("consecutive_failures", 0) > 0:
            row["state"] = "retrying"
        else:
            row["state"] = "queued"
        if job and row["state"] in {"processing", "queued", "retrying", "failed"}:
            failure = job.get("last_failure") or {}
            row["job"] = {"attempts": job.get("attempts"), "failures": job.get("failures"),
                          "consecutive_failures": job.get("consecutive_failures"), "next_attempt_at": job.get("next_attempt_at"),
                          "last_attempt_at": job.get("last_attempt_at"), "last_outcome": job.get("last_outcome"),
                          "last_failure": {k: failure.get(k) for k in ("category", "type", "code", "phase", "exit_code",
                                           "limit_seconds", "sqlite_code", "frames") if failure.get(k) is not None} or None}
        if row["state"] in {"queued", "processing", "retrying", "failed"} and cutoff is not None:
            row["overdue_seconds"] = round((now-cutoff).total_seconds())
        entries.append(row)
    return entries, not_trades, unresolved


def readiness(entries, not_trades, unresolved, enrollments):
    current = [e for e in entries if e["experiment_id"] == EXPERIMENT_ID]
    counts = {k: sum(e["state"] == k for e in current) for k in
              ("observing", "queued", "processing", "retrying", "failed", "completed_valid", "completed_excluded")}
    registered = len(current)
    processed = counts["completed_valid"]+counts["completed_excluded"]
    overdue = [e for e in current if e["state"] in {"queued", "processing", "retrying", "failed"}]
    oldest = min(overdue, key=lambda e: e["cutoff"], default=None)
    return {"experiment_id": EXPERIMENT_ID, "registered_filled_entries": registered,
            "enrolled_registrations": len(enrollments), "counts": counts,
            "pending": registered-processed, "processed": processed,
            "processed_coverage": {"numerator": counts["completed_valid"], "denominator": processed,
                                   "rate": counts["completed_valid"]/processed if processed else None,
                                   "label": "Valid paired comparisons ÷ processed entries"},
            "overall_usable": {"numerator": counts["completed_valid"], "denominator": registered,
                               "rate": counts["completed_valid"]/registered if registered else None,
                               "label": "Valid paired comparisons ÷ all registered filled entries (pending included)"},
            "deferred": counts["retrying"]+counts["failed"],
            "oldest_overdue": {"entry_id": oldest["entry_id"], "cutoff": oldest["cutoff"],
                               "overdue_seconds": oldest["overdue_seconds"], "state": oldest["state"]} if oldest else None,
            "prior_experiment_entries": sum(e["state"] == "prior_experiment" for e in entries),
            "unfilled_submissions": unresolved, "not_trades": not_trades}


# ---------------------------------------------------------------------------
# 25-point take-profit baseline comparison
# ---------------------------------------------------------------------------

def _bootstrap(rows, field, alpha, resamples=4000, seed=24011):
    sessions = {}
    for row in rows:
        sessions.setdefault(row["session"], []).append(row[field])
    if len(sessions) < MIN_SESSIONS:
        return None
    rng = random.Random(seed)
    groups = list(sessions.values())
    estimates = sorted(mean([v for g in rng.choices(groups, k=len(groups)) for v in g]) for _ in range(resamples))
    low = max(0, math.floor(resamples*alpha/2)-1)
    return [estimates[low], estimates[resamples-1-low]]


def _variant(result, key):
    return next(v for v in result["variants"] if v["key"] == key)


def tp_baseline(results, items_by_id=None, *, phase="exploratory"):
    """Paired differences of each larger fixed target against the 25-point target on identical entries."""
    cohort = [r for r in results if r.get("phase") == phase]
    valid = [r for r in cohort if r.get("paired_valid")]
    comparisons = len(PROTOCOL["ratios"])-1
    alpha = .05/comparisons
    sessions = len({r["session"] for r in valid})

    def summary(key):
        rows = [{**_variant(r, key), "session": r["session"]} for r in valid]
        return {"dollars": metrics(rows), "r": metrics(rows, "realized_r"),
                "take_profit_exits": sum(v.get("exit_reason") == "take_profit" for v in rows),
                "stop_exits": sum(v.get("exit_reason") in {"initial_stop", "trailing_stop"} for v in rows)}

    def difference(key):
        rows = []
        for r in valid:
            larger, base = _variant(r, key), _variant(r, BASELINE_KEY)
            rows.append({"entry_id": r["entry_id"], "session": r["session"],
                         "net_pnl_usd": larger["net_pnl_usd"]-base["net_pnl_usd"],
                         "realized_r": larger["realized_r"]-base["realized_r"],
                         "baseline_outcome": base.get("exit_reason"), "variant_outcome": larger.get("exit_reason")})
        dollars = metrics(rows)
        adjusted = _bootstrap(rows, "net_pnl_usd", alpha)
        if len(rows) < MIN_ENTRIES or sessions < MIN_SESSIONS:
            verdict = "insufficient_data"
        elif adjusted is None:
            verdict = "insufficient_data"
        elif adjusted[0] > 0:
            verdict = "higher_in_sample"
        elif adjusted[1] < 0:
            verdict = "lower_in_sample"
        else:
            verdict = "no_clear_difference"
        changed = [x for x in rows if x["baseline_outcome"] != x["variant_outcome"]]
        return {"entries": len(rows), "sessions": sessions,
                "mean_difference_usd": dollars["expectancy"], "total_difference_usd": dollars["net"],
                "mean_difference_r": metrics(rows, "realized_r")["expectancy"],
                "entries_better": dollars["wins"], "entries_worse": dollars["losses"], "entries_same": dollars["breakevens"],
                "interval_95_pointwise_usd": dollars["interval_95"],
                "interval_adjusted_usd": adjusted,
                "baseline_target_became_stop": sum(x["baseline_outcome"] == "take_profit" and x["variant_outcome"] != "take_profit" for x in changed),
                "verdict": verdict}

    series = []
    for ratio in PROTOCOL["ratios"]:
        key = str(ratio)
        row = {"key": key, "ratio": ratio, "target_points": 25*ratio, "stop_points": PROTOCOL["stop_points"],
               "outcomes": summary(key)}
        if key != BASELINE_KEY:
            row["versus_baseline"] = difference(key)
        series.append(row)
    trailing = {"key": "trailing", "outcomes": summary("trailing"), "versus_baseline": difference("trailing"),
                "label": "Trailing reference (25 stop / +25 activation / 25 trail). Not the requested baseline."}
    partial = []
    for r in cohort:
        if r.get("paired_valid"):
            continue
        resolved = {v["key"]: {"exit_reason": v.get("exit_reason"), "net_pnl_usd": v.get("net_pnl_usd")}
                    for v in r["variants"] if v["status"] == "resolved"}
        unresolved = {v["key"]: v.get("reason", v["status"]) for v in r["variants"] if v["status"] != "resolved"}
        partial.append({"entry_id": r["entry_id"], "session": r["session"], "resolved": resolved, "unresolved": unresolved})
    agreement = None
    if items_by_id:
        compared = []
        for r in valid:
            actual = items_by_id.get(r["entry_id"]) or {}
            if actual.get("strategy_class") == "fixed" and actual.get("exit_reason") in {"take_profit", "initial_stop"}:
                compared.append((r["entry_id"], actual["exit_reason"], _variant(r, BASELINE_KEY).get("exit_reason")))
        agreement = {"compared": len(compared), "same_exit_reason": sum(a == b for _, a, b in compared),
                     "disagreements": [{"entry_id": i, "actual": a, "simulated": b} for i, a, b in compared if a != b][:20],
                     "basis": "Actual fixed-bracket broker exits versus the simulated 25-point baseline on the same entries"}
    return {"report_version": REPORT_VERSION, "question": QUESTION, "experiment_id": EXPERIMENT_ID, "phase": phase,
            "baseline": {"key": BASELINE_KEY, "target_points": 25, "stop_points": PROTOCOL["stop_points"],
                         "label": "Current fixed bracket: 25-point take-profit, 25-point stop"},
            "cohort": {"valid_paired_entries": len(valid), "excluded_entries": len(cohort)-len(valid), "sessions": sessions,
                       "includes_losing_trades": True},
            "series": series, "trailing_reference": trailing,
            "partial_observations": partial[:200], "baseline_agreement": agreement,
            "minimums": {"entries": MIN_ENTRIES, "sessions": MIN_SESSIONS},
            "interval_methods": {"pointwise": "95% session-cluster bootstrap (1,000 resamples), unavailable below 5 sessions",
                                 "adjusted": f"Bonferroni-adjusted {100*(1-alpha):.3f}% session-cluster bootstrap "
                                             f"(4,000 resamples) for {comparisons} larger targets"},
            "caveats": [
                "Every comparison uses the same valid entries, including losing trades, with $1.72 round-turn costs.",
                "Targets and stops resolve in recorded chronological order: a stop reached first ends the trade even if the target is touched later.",
                "Exploratory registrations only; the held-out validation cohort is not used here.",
                "Excluded entries are not pooled. Partial per-trade observations are listed separately.",
                "A higher in-sample mean is not proof that a larger target is better; more entries narrow uncertainty but do not settle the question by themselves.",
                "Simulated fills use a model (latency, one-tick trade-through, adverse stop tick) and may differ from broker fills."]}


# ---------------------------------------------------------------------------
# Combined endpoints
# ---------------------------------------------------------------------------

def comparison_report(db, owner, account, **filters):
    """The existing comparison plus account-scoped readiness, health and the TP baseline."""
    from .topbot_runner import statistics_item
    from .topbot_study_store import comparison, read
    from . import topbot_study_jobs as jobs
    from .topbot_study_worker import account_activity
    now = datetime.now(timezone.utc)
    rows = _rows(db, owner, account)
    truncated = len(rows) > 5000
    items = [statistics_item(row) for row in rows[:5000]]
    db.rollback()
    result = comparison(owner, account, items, **filters)
    result["actual_scope"] = "Most recent 5,000 runner entries; dates use UTC entry dates"
    result["actual_truncated"] = truncated
    results = read(owner, account)
    activity = account_activity(owner, account)
    entries, not_trades, unresolved = classify_entries(owner, account, items, results, jobs.jobs(owner, account), activity, now)
    state = readiness(entries, not_trades, unresolved, _enrollments(owner, account))
    result["readiness"] = state
    result["research_health"] = {**activity["reader"], "account_deferred": state["deferred"],
                                 "account_overdue": state["counts"]["queued"]+state["counts"]["processing"]+state["counts"]["retrying"]+state["counts"]["failed"],
                                 "oldest_overdue": state["oldest_overdue"], "scope": "this account"}
    baseline_results = [r for r in results
        if (not filters.get("version") or r.get("strategy_version_id") == filters["version"])
        and (not filters.get("strategy") or r.get("strategy_class") == filters["strategy"])
        and (not filters.get("start") or (r.get("entry_at") or "")[:10] >= filters["start"])
        and (not filters.get("end") or (r.get("entry_at") or "")[:10] <= filters["end"])]
    result["tp_baseline"] = tp_baseline(baseline_results, {i["entry_id"]: i for i in items})
    result["account_id"] = account
    result["as_of"] = now.isoformat()
    return result


def _capture_progress(owner, contract, now, samples={}):
    from .topbot_depth import feed
    with feed.lock:
        stream = feed.streams.get((str(owner), contract))
        capture = stream["state"].capture if stream else None
        retain_until = stream.get("retain_until") if stream else None
    if capture is None:
        return {"state": "not_subscribed" if stream is None else "unavailable", "retain_until": retain_until.isoformat() if retain_until else None}
    status = capture.status()
    from time import monotonic
    key = (str(owner), contract, capture.capture_id)
    previous = samples.get(key)
    samples[key] = (monotonic(), status["durable_index"])
    if len(samples) > 64:
        samples.pop(next(iter(samples)))
    committed = status.get("last_commit_completed_at")
    age = (now-utc(committed)).total_seconds() if committed else None
    lag = status["produced_index"]-status["durable_index"]
    if previous is None:
        progress = "first_sample"
    elif status["durable_index"] > previous[1]:
        progress = "advancing"
    elif lag > 0:
        progress = "not_advancing"
    else:
        progress = "idle_no_new_events"
    return {"state": status["state"], "capture_id": capture.capture_id, "durable_index": status["durable_index"],
            "produced_index": status["produced_index"], "uncommitted_events": lag,
            "last_commit_completed_at": committed, "durable_age_seconds": round(age, 3) if age is not None else None,
            "durable_progress": progress, "dropped_events": status["dropped_events"], "last_drop": status["last_drop"],
            "queued_events": status["queued_events"], "last_commit_seconds": status["last_commit_seconds"],
            "max_commit_seconds": status["max_commit_seconds"], "error_category": status["error_category"],
            "retain_until": retain_until.isoformat() if retain_until else None,
            "stale": age is None or age > STALE_SECONDS}


def research_status(db, owner, account):
    """Four separate sections: position recording, post-exit recording, analysis, backup."""
    from .topbot_runner import statistics_item
    from .topbot_study_store import read
    from . import topbot_study_jobs as jobs
    from . import trade_progress_store as progress_store
    from .topbot_study_worker import account_activity
    from .topbot_replay_index import mirror_status
    from .market_archive import backup_status
    from ..models import BotConfig
    now = datetime.now(timezone.utc)
    rows = _rows(db, owner, account, limit=1000)
    items = [statistics_item(row) for row in rows[:1000]]
    config = db.query(BotConfig).filter_by(user_id=owner, account_id=account,
               strategy_type="topbot_adaptive").order_by(BotConfig.id.desc()).first()
    contract = config.contract_id if config else None
    db.rollback()
    activity = account_activity(owner, account)
    results = read(owner, account)
    entries, not_trades, unresolved = classify_entries(owner, account, items, results, jobs.jobs(owner, account), activity, now)
    state = readiness(entries, not_trades, unresolved, _enrollments(owner, account))

    try:
        position = progress_store.status(owner, account)
        heartbeat = position.get("heartbeat")
        position["heartbeat_at"] = datetime.fromtimestamp(heartbeat, timezone.utc).isoformat() if heartbeat else None
        position["age_seconds"] = round(now.timestamp()-heartbeat, 3) if heartbeat else None
        position["stale"] = position["age_seconds"] is None or position["age_seconds"] > STALE_SECONDS
        position.pop("files", None)
    except Exception as exc:
        position = {"enrolled": None, "healthy": False, "error": "status_unavailable_" + type(exc).__name__, "stale": True}
    position["scope"] = "Open-position path from entry to exit (1-second samples); not post-exit research"

    capture = _capture_progress(owner, contract, now) if contract else {"state": "no_strategy_contract"}
    windows = []
    for e in entries:
        if e["state"] != "observing":
            continue
        windows.append({"entry_id": e["entry_id"], "exit_at": e["exit_at"], "exit_reason": e["exit_reason"],
                        "cutoff": e["cutoff"], "seconds_remaining": e["window_seconds_remaining"],
                        "position_closed": bool(e["exit_at"]),
                        "retention": e.get("retention") or {"state": "not_yet_checked"},
                        "capture_matches_entry": None})
    capture_by_entry = {}
    for row in rows[:1000]:
        evidence = (row.raw_request or {}).get("entryEvidence") or {}
        ref = (evidence.get("pre_submit") or evidence.get("signal") or {}).get("evidence") or {}
        capture_by_entry[row.id] = ref.get("capture_id")
    for window in windows:
        registered = capture_by_entry.get(window["entry_id"])
        if registered and capture.get("capture_id"):
            # A different live capture means the recorder restarted inside this window: a gap the replay will exclude.
            window["capture_matches_entry"] = registered == capture["capture_id"]
    post_exit_problems = []
    if windows and capture.get("state") not in {"recording"}:
        post_exit_problems.append("worker_capture_not_recording")
    if windows and capture.get("stale"):
        post_exit_problems.append("no_recent_durable_commit")
    if any(w["retention"].get("state") == "failed" for w in windows):
        post_exit_problems.append("retention_failed")
    if any(w["capture_matches_entry"] is False for w in windows):
        post_exit_problems.append("capture_restarted_inside_window")

    backup = backup_status()
    backup.pop("destination", None)
    backup["verified"] = backup.get("status") in {"verified", "succeeded", "ok"} and not backup.get("stale")
    backup["state"] = ("verified" if backup["verified"] else "stale_unverified" if backup.get("status") != "failed"
                       else "failed_stale")

    reader = dict(activity["reader"])
    reader["checkpoint_reader"] = {k: v for k, v in mirror_status().items() if k in {"available", "reason"}}
    return {"account_id": account, "as_of": now.isoformat(),
            "position_recording": position,
            "post_exit_recording": {"open_windows": windows, "capture": capture, "problems": post_exit_problems,
                                    "scope": "Market data retained after each exit until the study cutoff (entry + 2 h or session close)"},
            "analysis": {"reader": reader, "active": activity["active"], "readiness": state,
                         "entries": sorted(entries, key=lambda e: e["entry_id"], reverse=True)[:200]},
            "backup": backup}
