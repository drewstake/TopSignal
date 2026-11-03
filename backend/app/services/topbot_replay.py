"""Offline replay of the worker's observed DOM, with explicit evidence limits.

Reproduces signal state, not broker fills, queue position, or account risk state.
"""
from collections import Counter
import json
from pathlib import Path
import sqlite3

from .topbot_depth import DepthState, utc
from .topbot_evidence import binding, recorded_batches
from .topbot_strategy import qualifying_action
from .topbot_execution_trace import delta_ms


def replay(path, *, observation=None, threshold=None, persistence_seconds=None, allow_code_mismatch=False):
    if threshold is not None and not 0 <= threshold <= .9:
        raise ValueError("invalid_threshold")
    if persistence_seconds is not None and not 0 <= persistence_seconds <= 300:
        raise ValueError("invalid_persistence")
    rows = recorded_batches(path)
    try:
        header = next(rows)
        meta = header["metadata"]
        recorded_code = meta["implementation"]["code_sha256"]
        code_matches = bool(recorded_code) and recorded_code == binding()["code_sha256"]
        if not code_matches and not allow_code_mismatch:
            raise ValueError("implementation_mismatch_use_the_recorded_release")
        reference = (observation or {}).get("evidence", {})
        if observation is not None and reference.get("capture_id") != meta["capture_id"]:
            raise ValueError("observation_capture_mismatch")
        state = DepthState(meta["contract_id"])
        counts, actions, examples = Counter(), Counter(), []
        last, gaps, matched = 0, [], None
        continuous = True
        first_at = last_at = None
        for row in rows:
            index = row["event_index"]
            if index != last+1:
                continuous = False
                if len(gaps) < 100: gaps.append({"first_missing": last+1, "last_missing": index-1})
            event, at = row["event"], utc(row["received_at"])
            first_at, last_at = first_at or row["received_at"], row["received_at"]
            if event["event"] == "parameters":
                event = {**event, "data": {**event["data"]}}
                if threshold is not None: event["data"]["threshold"] = threshold
                if persistence_seconds is not None: event["data"]["persistence_seconds"] = persistence_seconds
            state.apply(event, at)
            counts[event["event"]] += 1
            for transition in state.transitions:
                counts[transition["kind"]] += 1
                if transition["kind"] == "episode_qualified":
                    signal = qualifying_action(state.snapshot(at), {"imbalance_threshold": state.threshold,
                                                "persistence_seconds": state.persistence_required})
                    actions[signal] += 1
                    if len(examples) < 100: examples.append({**transition, "action": signal, "event_index": index})
            if observation is not None and index == reference.get("event_index"):
                snapshot = state.snapshot(utc(observation["checked_at"]))
                same_hash = state.evidence_snapshot()["state_sha256"] == reference.get("state_sha256")
                fields = ("ready", "bid", "ask", "bid_total", "ask_total", "imbalance", "observed_at",
                          "episode_at", "persistence_seconds", "bid_levels", "ask_levels", "qualification_reset")
                differences = [k for k in fields if snapshot.get(k) != observation.get(k)]
                matched = {"event_index": index, "state_hash_matches": same_hash, "differing_fields": differences,
                           "verified": code_matches and continuous and same_hash and not differences
                                       and threshold is None and persistence_seconds is None}
                last = index
                break
            last = index
        if observation is not None and matched is None:
            raise ValueError("observation_event_missing_or_not_durable")
        return {"schema": "topbot-dom-replay-v1", "capture_id": meta["capture_id"], "contract_id": meta["contract_id"],
                "implementation_matches": code_matches, "recording_status": header["status"],
                "continuous_prefix": continuous, "capture_gap_examples": gaps, "verified_through_index": last,
                "first_received_at": first_at, "last_received_at": last_at, "counts": dict(counts),
                "qualifying_episodes_by_action": dict(actions), "episode_examples": examples,
                "observation_verification": matched,
                "parameter_overrides": {"threshold": threshold, "persistence_seconds": persistence_seconds},
                "limitations": ["Normalized worker events, not a certified complete exchange book.",
                    "Qualifying episodes are signals, not executed trades. Account gates and broker fills are not simulated.",
                    "Recorded feed gaps remain gaps. A capture gap invalidates exact reproduction after that gap.",
                    "An active or interrupted capture may have an uncommitted tail; only committed batches are read."]}
    finally:
        rows.close()


def trade_timeline(path, *, account_id, entry_id):
    """Read-only, account-scoped timeline; includes linked evaluation evidence."""
    db = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = db.execute("""SELECT seq,at,kind,operation,payload FROM events WHERE account=?
            AND (json_extract(payload,'$.entry_id')=? OR json_extract(payload,'$.order_attempt_id')=?)
            ORDER BY seq LIMIT 20001""", (account_id, entry_id, entry_id)).fetchall()
        if len(rows) > 20000: raise ValueError("timeline_limit_exceeded")
        items = [dict(seq=r[0], at=r[1], kind=r[2], operation=r[3], details=json.loads(r[4])) for r in rows]
        correlations = {x["details"].get("correlation_id") for x in items}-{None}
        for correlation in correlations:
            extra = db.execute("""SELECT seq,at,kind,operation,payload FROM events WHERE account=?
                AND json_extract(payload,'$.correlation_id')=?
                AND kind IN ('evaluation_started','evaluation_result','evaluation_error','evaluation_committed')
                ORDER BY seq LIMIT 101""", (account_id, correlation)).fetchall()
            if len(extra)>100: raise ValueError("correlation_limit_exceeded")
            items.extend(dict(seq=r[0], at=r[1], kind=r[2], operation=r[3], details=json.loads(r[4])) for r in extra)
        items = sorted({r["seq"]:r for r in items}.values(), key=lambda r:r["seq"])
        operations, stages = {}, []
        for item in items:
            if item["kind"] == "broker_request": operations[item["operation"]] = item
            elif item["kind"] in {"broker_acknowledged", "broker_outcome_unconfirmed", "stop_change_verified"}:
                start = operations.get(item["operation"])
                if start:
                    a, b = start["details"], item["details"]
                    same_process = a.get("process_session_id") and a.get("process_session_id") == b.get("process_session_id")
                    elapsed = ((b["monotonic_ns"]-a["monotonic_ns"])/1e6
                               if same_process and "monotonic_ns" in a and "monotonic_ns" in b else None)
                    stages.append({"operation": item["operation"], "action": a.get("action"), "end_kind": item["kind"],
                                   "record_to_record_ms": round(elapsed,3) if elapsed is not None else None,
                                   "wall_clock_delta_ms": delta_ms(item["at"], start["at"]),
                                   "request_timing": b.get("timing")})
        return {"account_id": account_id, "entry_id": entry_id, "events": items, "operations": stages,
                "coverage": "Only retained events; absent stages are unknown, never zero-duration."}
    finally:
        db.close()
