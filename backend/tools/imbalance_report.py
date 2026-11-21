"""Account-scoped report built from existing fills, exploratory enrollments and prospective anchors."""

import json
import math
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from imbalance_study import EpisodeMatches, HORIZONS, broker_ids, directory, saved_direction
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
       raw_request->'strategySnapshot'->'quantity' AS planned_quantity, provider_order_id, created_at, left(rejection_reason,160)
       FROM bot_order_attempts WHERE user_id=%s AND account_id=%s AND bot_config_id=%s
       AND (raw_request->'strategySnapshot' IS NOT NULL OR raw_request->'timeExit'->'runner'->>'version'='1')
       ORDER BY id DESC LIMIT 5001"""
    rows = db.execute(sql, (owner, account, config)).fetchall()
    actual, attempts = [], []
    first_by_version = {}
    for id_, side, status, contract, size, runner, version, impl, episode, capture, rules, limits, quantity, order, created, reason in rows[:5000]:
        attempts.append(dict(id=id_, user_id=owner, account_id=account, bot_config_id=config, side=side,
                             status=status, contract_id=contract, runner=runner or {}, capture=capture,
                             episode=episode, version=version, direction=saved_direction((rules or {}).get("entry") or {}),
                             provider_order_id=order, at=created.timestamp(), reason=reason))
        if not runner or str(runner.get("version")) != "1":
            continue
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
    ids = sorted({i for a in attempts for i in broker_ids(a["runner"], "entry_fill_id")})
    legs = db.execute("""SELECT user_id,account_id,contract_id,order_id,source_trade_id,side,status,
                         raw_payload->>'voided' FROM projectx_trade_events
                         WHERE user_id=%s AND account_id=%s AND source_trade_id=ANY(%s)""",
                      (owner, account, ids)).fetchall() if ids else []
    fills = [dict(user_id=u, account_id=a, contract_id=c, order_id=o, source_trade_id=i,
                  side=s, status=st, voided=v == "true") for u, a, c, o, i, s, st, v in legs]
    return actual, len(rows) > 5000, labels, EpisodeMatches(attempts, fills, owner, account, config)


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


def reconcile(local, matches, actual):
    # Derived only in this connection. Original directions, dispositions and IDs remain auditable.
    local.execute("""CREATE TEMP TABLE IF NOT EXISTS attribution(anchor INTEGER PRIMARY KEY,
                  order_side INTEGER, version TEXT, disposition TEXT, reason TEXT, entry_id INTEGER,
                  entry_order_id TEXT, entry_fill_ids TEXT)""")
    local.execute("DELETE FROM attribution")
    rows = local.execute("""SELECT id,capture,episode,contract,at,bid,ask,-side AS heavy FROM anchors
                         WHERE source='Worker' AND side!=0""").fetchall()
    links = {}
    verified = {a["id"] for attempts in matches.episodes.values() for a in attempts if matches.verified(a)}
    intervals = defaultdict(list)
    for a in actual:
        if a["exit"] and a["id"] in verified:
            intervals[a["contract"]].append((datetime.fromisoformat(a["entry"]).timestamp(),
                                             datetime.fromisoformat(a["exit"]).timestamp()))
    for periods in intervals.values():
        periods.sort()
        for i in range(1, len(periods)):
            start, end = periods[i]
            periods[i] = start, max(end, periods[i-1][1])
    keys = Counter((r["contract"], r["capture"], r["episode"]) for r in rows)
    for r in rows:
        key = r["contract"], r["capture"], r["episode"]
        m = matches.lookup(*key, r["at"], r["heavy"])
        if keys[key] > 1:
            continue
        periods = intervals[r["contract"]]
        previous = bisect_left(periods, r["at"], key=lambda p: p[0]) - 1
        if (m["disposition"] == "Unknown" and key not in matches.episodes and previous >= 0
                and r["at"] < periods[previous][1]):
            m.update(disposition="Skipped", reason="Bot position already open")
        local.execute("INSERT INTO attribution VALUES(?,?,?,?,?,?,?,?)", (
            r["id"], m["order_side"], m["version"], m["disposition"], m["reason"], m["entry_id"],
            m["entry_order_id"], json.dumps(m["entry_fill_ids"])))
        if m["entry_id"]:
            links[m["entry_id"]] = r["ask"] if m["order_side"] == 1 else r["bid"]
    local.execute("""CREATE TEMP VIEW IF NOT EXISTS study_signals AS
        SELECT a.id,a.source,a.capture,a.episode,a.contract,a.at,a.bid,a.ask,a.control,a.rule,
        -a.side AS heavy, CASE WHEN a.source='Independent' THEN a.side ELSE t.order_side END AS side,
        t.order_side,t.version,t.disposition,t.reason,t.entry_id,t.entry_order_id,t.entry_fill_ids
        FROM anchors a LEFT JOIN attribution t ON a.id=t.anchor WHERE a.side!=0""")
    return links


def observed(db, source, contract, start, through):
    sets = []
    for h in HORIZONS:
        # Both paths use the same simulated marketable direction. Only complete pairs enter the difference.
        sample = db.execute(
            """WITH moves AS (SELECT a.control,
            CASE a.side WHEN 1 THEN s.bid-a.ask WHEN -1 THEN a.bid-s.ask END AS signal,
            CASE a.side WHEN 1 THEN m.bid-c.ask WHEN -1 THEN c.bid-m.ask END AS baseline
            FROM study_signals a LEFT JOIN marks s ON s.anchor=a.id AND s.seconds=?
            LEFT JOIN anchors c ON c.id=a.control LEFT JOIN marks m ON m.anchor=c.id AND m.seconds=?
            WHERE a.source=? AND a.contract=?)
            SELECT count(signal),avg(signal),count(signal-baseline),avg(CASE WHEN baseline IS NOT NULL THEN signal END),
            avg(CASE WHEN signal IS NOT NULL THEN baseline END),avg(signal-baseline) FROM moves""",
            (h, h, source, contract),
        ).fetchone()
        sets.append(
            dict(zip(("valid", "all_mean", "pairs", "signal", "baseline", "difference"), sample), seconds=h)
        )
    counts = db.execute(
        """SELECT count(*),count(control),sum(disposition='Filled') FROM study_signals
        WHERE source=? AND contract=?""",
        (source, contract),
    ).fetchone()
    rows = db.execute(
        """SELECT id,episode,at,side,bid,ask,control,disposition,reason,entry_id,rule,heavy,order_side,version,entry_order_id,entry_fill_ids,
        (SELECT at FROM episode_ends e WHERE e.anchor=study_signals.id) AS ended FROM study_signals
        WHERE source=? AND contract=? ORDER BY at DESC LIMIT 40""",
        (source, contract),
    ).fetchall()
    recent = []
    for r in rows:
        row = dict(r)
        row["at"] = iso(row["at"])
        row["disposition"] = row["disposition"] or ("Independent observation" if source == "Independent" else "Unknown")
        row["entry_fill_ids"] = json.loads(row["entry_fill_ids"] or "[]")
        marks = db.execute("SELECT seconds,bid,ask,reason FROM marks WHERE anchor=?", (r["id"],))
        row["moves"] = {
            str(h): {
                "points": None
                if b is None or r["ask"] is None or r["side"] is None
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
        actual, truncated, labels, matches = execution_rows(client, owner, cfg["account"], cfg["config"])
    links = reconcile(db, matches, actual)
    for t in actual:
        t["qualify"] = links.get(t["id"])
        t.pop("capture")
        t.pop("episode")
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
            observed(db, "Independent", contract, start, independent.get("through") if independent.get("contract") == contract else None),
        ],
    }
