"""Account-scoped report built from existing fills, exploratory enrollments and prospective anchors."""

import json
import math
import time
from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from imbalance_study import HORIZONS, directory
from track_imbalances import iso, pg


def version_label(trade, entry, plan, limits, quantity):
    parts = []
    if entry.get("revision") == "dom_contrarian_v1":
        direction = entry.get("imbalance_direction", "contrarian")
        if direction in ("following", "contrarian"):
            parts.append("Follow imbalance" if direction == "following" else "Contrarian")
    if quantity:
        symbol = trade["contract"].split(".")
        parts.append(f"{float(quantity):g} {symbol[-2]}" if len(symbol) >= 2 else f"{float(quantity):g} contracts")
    mode = trade["mode"]
    stop, target = plan.get("initial_stop_points"), plan.get("take_profit_points")
    if mode == "fixed" and stop and stop == target:
        parts.append(f"{stop:g}-pt fixed bracket")
    elif mode == "fixed" and stop and target:
        parts.append(f"{stop:g}-pt stop / {target:g}-pt target")
    elif mode == "trailing" and plan.get("trail_points"):
        parts.append(f"{plan['trail_points']:g}-pt trailing stop")
    else:
        parts.append({"fixed": "Fixed bracket", "trailing": "Trailing stop"}.get(mode, "Saved plan"))
    profit, loss = limits.get("daily_profit_target"), limits.get("max_daily_loss")
    if profit is not None and loss is not None:
        profit, loss = float(profit), float(loss)
        parts.append(f"±${profit:g}/day" if profit == loss else f"+${profit:g} / −${loss:g} daily")
    return " · ".join(parts)


def execution_rows(db, owner, account, config):
    from app.services.topbot_runner import statistics_item

    sql = """SELECT id,side,status,contract_id,size,raw_request->'timeExit'->'runner' AS runner,
       raw_request->'strategySnapshot'->>'strategy_version_id' AS version,
       raw_request->'entryEvidence'->'implementation' AS implementation,
       raw_request->'entryEvidence'->'signal'->>'episode_at' AS episode,
       raw_request->'entryEvidence'->'signal'->'evidence'->>'capture_id' AS capture,
       raw_request->'strategySnapshot'->'rules' AS rules,
       raw_request->'strategySnapshot'->'risk_limits' AS limits,
       raw_request->'strategySnapshot'->'quantity' AS planned_quantity
       FROM bot_order_attempts WHERE user_id=%s AND account_id=%s AND bot_config_id=%s
       AND raw_request->'timeExit'->'runner'->>'version'='1' ORDER BY id DESC LIMIT 5001"""
    rows = db.execute(sql, (owner, account, config)).fetchall()
    actual = []
    first_by_version = {}
    for id_, side, status, contract, size, runner, version, impl, episode, capture, rules, limits, quantity in rows[:5000]:
        request = {
            "timeExit": {"runner": runner},
            "strategySnapshot": {"strategy_version_id": version} if version else {},
            "entryEvidence": {"implementation": impl},
        }
        item = statistics_item(
            SimpleNamespace(
                id=id_, side=side, status=status, contract_id=contract, size=size, raw_request=request
            )
        )
        if not item.get("entry_at"):
            continue
        trace = item.get("execution") or {}
        net = item.get("net_pnl_usd")
        closed = (
            item.get("stats_status") == "complete" and isinstance(net, (int, float)) and math.isfinite(net)
        )
        actual.append(
            {
                "id": id_,
                "side": side,
                "mode": item["strategy_class"],
                "version": item["strategy_version_id"],
                "session": item.get("session"),
                "entry": item["entry_at"],
                "exit": item.get("exit_at"),
                "net": net if closed else None,
                "costs": item.get("fees_usd") if closed else None,
                "fill": item.get("entry_price"),
                "submit": trace.get("reference_price"),
                "episode": episode,
                "capture": capture,
                "contract": contract,
            }
        )
        trade = actual[-1]
        first = first_by_version.get(trade["version"])
        at = datetime.fromisoformat(trade["entry"])
        if first is None or (at, id_) < first[:2]:
            rules = rules or {}
            first_by_version[trade["version"]] = (at, id_, trade, rules.get("entry") or {},
                                                  rules.get("exit") or runner or {}, limits or {}, quantity or size)
    labels = {}
    for version, (at, _, trade, entry, plan, limits, quantity) in first_by_version.items():
        label = version_label(trade, entry, plan, limits, quantity)
        et = at.astimezone(ZoneInfo("America/New_York"))
        labels[version] = f"{et:%b} {et.day}, {et.year} {et:%H:%M} ET · {label}"
    # Distinguish two revisions first traded in the same minute.
    occurrences = Counter(labels.values())
    for version, label in labels.items():
        if occurrences[label] > 1:
            labels[version] = f"{label} · entry #{first_by_version[version][1]}"
    return actual, len(rows) > 5000, labels


def coverage(owner, account, actual):
    from hashlib import sha256

    from app.services.topbot_study import EXPERIMENT_ID
    from app.services.topbot_study_store import connection

    by_id = {row["id"]: row for row in actual}
    result = []
    with connection(owner) as db:
        if db is None:
            return result
        rows = db.execute(
            """SELECT e.entry,r.payload,r.hash FROM enrollments e LEFT JOIN results r ON
            r.seq=(SELECT max(seq) FROM results WHERE account=e.account AND entry=e.entry AND experiment=e.experiment)
            WHERE e.account=? AND e.experiment=? AND e.phase='exploratory' ORDER BY e.ordinal LIMIT 100""",
            (account, EXPERIMENT_ID),
        )
        for entry, payload, digest in rows:
            item = by_id.get(entry)
            if not item:
                continue
            if payload and sha256(payload.encode()).hexdigest() != digest:
                raise ValueError("Exit-study result integrity")
            row = json.loads(payload) if payload else {}
            reasons = sorted(
                {
                    v.get("reason") or v["status"]
                    for v in row.get("variants", [])
                    if v.get("status") != "resolved" and v.get("reason")
                }
            )
            result.append(
                {
                    "id": entry,
                    "mode": item["mode"],
                    "version": item["version"],
                    "date": item["entry"][:10],
                    "outcome": "unknown"
                    if item["net"] is None
                    else "win"
                    if item["net"] > 0
                    else "loss"
                    if item["net"] < 0
                    else "flat",
                    "status": "pending" if not payload else "usable" if row["paired_valid"] else "excluded",
                    "reasons": reasons or (["unspecified"] if row and not row["paired_valid"] else []),
                }
            )
    return result


def dispositions(pgdb, local, owner, cfg, start, actual):
    local.execute("""CREATE TABLE IF NOT EXISTS decisions(capture TEXT,episode TEXT,contract TEXT,side INTEGER,
        disposition TEXT,reason TEXT,entry_id INTEGER,PRIMARY KEY(capture,episode,contract,side))""")
    # Revisit the newest decisions to pick up late attempts. Confirmed fills below also reconcile older episodes.
    watermark = local.execute("SELECT value FROM meta WHERE key='decision_id'").fetchone()
    floor = max(0, int(watermark[0]) - 50) if watermark else 0
    rows = pgdb.execute(
        """SELECT d.id,d.contract_id,d.action,d.raw_payload->'depth_entry'->>'episode_at',
        d.raw_payload->'depth_entry'->'evidence'->>'capture_id',a.id,a.status,left(a.rejection_reason,160),
        d.reason FROM bot_decisions d LEFT JOIN bot_order_attempts a ON a.bot_decision_id=d.id
        AND a.user_id=d.user_id AND a.account_id=d.account_id WHERE d.user_id=%s AND d.account_id=%s
        AND d.bot_config_id=%s AND d.id>%s AND d.created_at >= %s AND d.action IN ('SELL','BUY')
        ORDER BY d.id,a.id LIMIT 2000""",
        (owner, cfg["account"], cfg["config"], floor, iso(start - 60)),
    ).fetchall()

    def save(capture, episode, contract, side, disposition, reason, entry):
        if capture and episode:
            local.execute(
                """INSERT INTO decisions VALUES(?,?,?,?,?,?,?) ON CONFLICT(capture,episode,contract,side)
                DO UPDATE SET disposition=excluded.disposition,reason=excluded.reason,entry_id=excluded.entry_id
                WHERE decisions.disposition!='Filled'""",
                (capture, episode, contract, 1 if side == "BUY" else -1, disposition, reason, entry),
            )

    for id_, contract, side, episode, capture, entry, status, reason, decision_reason in rows:
        skipped = status in {"blocked", "rejected", "cancelled", "error", "dry_run"}
        disposition = "Skipped" if skipped else "Order unresolved" if entry else "Decision only"
        save(
            capture,
            episode,
            contract,
            side,
            disposition,
            reason or status or "No linked order attempt",
            entry,
        )
    if rows:
        local.execute("INSERT OR REPLACE INTO meta VALUES('decision_id',?)", (str(rows[-1][0]),))
    for t in actual:
        save(t["capture"], t["episode"], t["contract"], t["side"], "Filled", None, t["id"])
    # Exact worker identity only. Independent market observations have no worker capture ID.
    local.execute("""UPDATE anchors AS a SET (disposition,reason,entry_id)=
        (SELECT disposition,reason,entry_id FROM decisions d WHERE d.capture=a.capture AND d.episode=a.episode
        AND d.contract=a.contract AND d.side=a.side) WHERE a.source='Worker' AND a.side!=0
        AND EXISTS(SELECT 1 FROM decisions d WHERE d.capture=a.capture AND d.episode=a.episode
        AND d.contract=a.contract AND d.side=a.side AND (a.disposition IS NOT d.disposition OR a.reason IS NOT d.reason
        OR a.entry_id IS NOT d.entry_id))""")
    # A verified position already open at qualification explains an otherwise unevaluated episode.
    for item in actual:
        if not item["exit"] or item["exit"] < iso(start):
            continue
        local.execute(
            """UPDATE anchors SET disposition='Skipped',reason='Bot position already open'
            WHERE source='Worker' AND contract=? AND side!=0 AND disposition IS NULL AND at>? AND at<?""",
            (
                item["contract"],
                datetime.fromisoformat(item["entry"]).timestamp(),
                datetime.fromisoformat(item["exit"]).timestamp(),
            ),
        )


def observed(db, source, contract, start, through):
    sets = []
    for h in HORIZONS:
        # Both paths use the same simulated marketable direction. Only complete pairs enter the difference.
        sample = db.execute(
            """WITH moves AS (SELECT a.control,
            CASE a.side WHEN 1 THEN s.bid-a.ask ELSE a.bid-s.ask END AS signal,
            CASE a.side WHEN 1 THEN m.bid-c.ask ELSE c.bid-m.ask END AS baseline
            FROM anchors a LEFT JOIN marks s ON s.anchor=a.id AND s.seconds=?
            LEFT JOIN anchors c ON c.id=a.control LEFT JOIN marks m ON m.anchor=c.id AND m.seconds=?
            WHERE a.source=? AND a.contract=? AND a.side!=0)
            SELECT count(signal),avg(signal),count(signal-baseline),avg(CASE WHEN baseline IS NOT NULL THEN signal END),
            avg(CASE WHEN signal IS NOT NULL THEN baseline END),avg(signal-baseline) FROM moves""",
            (h, h, source, contract),
        ).fetchone()
        sets.append(
            dict(zip(("valid", "all_mean", "pairs", "signal", "baseline", "difference"), sample), seconds=h)
        )
    counts = db.execute(
        """SELECT count(*),count(control),sum(disposition='Filled') FROM anchors
        WHERE source=? AND contract=? AND side!=0""",
        (source, contract),
    ).fetchone()
    rows = db.execute(
        """SELECT id,episode,at,side,bid,ask,control,disposition,reason,entry_id,rule,
        (SELECT at FROM episode_ends e WHERE e.anchor=anchors.id) AS ended FROM anchors
        WHERE source=? AND contract=? AND side!=0 ORDER BY at DESC LIMIT 40""",
        (source, contract),
    ).fetchall()
    recent = []
    for r in rows:
        row = dict(r)
        row["at"] = iso(row["at"])
        if not row["disposition"]:
            row["disposition"] = (
                "Independent observation"
                if source != "Worker"
                else "Awaiting reconciliation"
                if time.time() - r["at"] < 45
                else "Skipped / no linked decision"
                if r["ended"]
                else "No linked decision recorded"
            )
            row["reason"] = (
                "No verified reason recorded" if row["disposition"].startswith("Skipped") else None
            )
        marks = db.execute("SELECT seconds,bid,ask,reason FROM marks WHERE anchor=?", (r["id"],))
        row["moves"] = {
            str(h): {
                "points": None
                if b is None or r["ask"] is None
                else b - r["ask"]
                if r["side"] == 1
                else r["bid"] - a,
                "reason": reason,
            }
            for h, b, a, reason in marks
        }
        recent.append(row)
    return {
        "source": source,
        "total": counts[0],
        "matched": counts[1],
        "filled": counts[2] or 0,
        "horizons": sets,
        "recent": recent,
        "gaps": db.execute("SELECT count(*) FROM gaps WHERE source=?", (source,)).fetchone()[0],
        "through": iso(through) if through else None,
    }


def build_report(cfg, dsn, cache, owner, contract, db, start, through):
    with pg(dsn) as client:
        actual, truncated, labels = execution_rows(client, owner, cfg["account"], cfg["config"])
        dispositions(client, db, owner, cfg, start, actual)
    for t in actual:
        q = db.execute(
            """SELECT bid,ask FROM anchors WHERE source='Worker' AND capture=? AND episode=? AND contract=?
            AND side=? LIMIT 1""",
            (t.pop("capture"), t.pop("episode"), t["contract"], 1 if t["side"] == "BUY" else -1),
        ).fetchone()
        t["qualify"] = (q["ask"] if t["side"] == "BUY" else q["bid"]) if q else None
    independent = {}
    try:
        independent = json.loads(
            (directory(cache, owner, cfg["account"]) / "independent.json").read_bytes()
        )
    except FileNotFoundError:
        pass
    return {
        "account_id": cfg["account"],
        "config_id": cfg["config"],
        "contract": contract,
        "started": iso(start),
        "as_of": iso(time.time()),
        "actual": actual,
        "version_labels": labels,
        "truncated": truncated,
        "coverage": coverage(owner, cfg["account"], actual),
        "studies": [
            observed(db, "Worker", contract, start, through),
            observed(db, "Independent", contract, start, independent.get("through")),
        ],
    }
