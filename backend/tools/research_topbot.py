"""Report and audit helpers for previously saved MNQ research ledgers."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

REUSED_HISTORY = (
    "All available 2019-July 2026 history was previously examined. Development "
    "and later diagnostic periods are retrospective; untouched final evaluation "
    "is unavailable. Bootstrap intervals do not correct for repeated strategy selection."
)


def write_new_json(path: Path, value: object) -> None:
    """Never silently replace a previous hypothesis, failed run or ledger."""
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def create_run_directory(parent: Path, label: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", label):
        raise ValueError("label must be 1-80 ASCII letters, digits, dots, underscores or hyphens")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = parent / f"{stamp}-{label}-{uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def offline_environment() -> None:
    # This changes only this research process, not operator files or settings.
    for name in tuple(os.environ):
        if name.startswith(("PROJECTX_", "TOPSTEP_", "TOPSTEPX_", "DATABENTO_", "SUPABASE_")):
            os.environ.pop(name, None)
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite+pysqlite:///:memory:",
        TOPSIGNAL_DB_SCHEMA_INIT="skip", TOPSIGNAL_LIVE_EXECUTION_ENABLED="false",
        TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false", TOPSIGNAL_BOT_WORKER_ENABLED="false",
    )

    def block_network(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Offline research forbids network access")

    sys.addaudithook(block_network)


def concentration(trades, sessions):
    descending = sorted((float(row["net_pnl"]) for row in trades), reverse=True)
    positive = [value for value in descending if value > 0]
    total = sum(descending)
    positive_sum = sum(positive)
    top_count = max(1, math.ceil(len(descending) * .01)) if descending else 0
    session_net = sorted((float(row["net_pnl"]) for row in sessions), reverse=True)
    year_net = defaultdict(float)
    for row in sessions:
        year_net[row["session"][:4]] += float(row["net_pnl"])
    return {
        "best_trade": descending[0] if descending else None,
        "worst_trade": descending[-1] if descending else None,
        "top_1_percent_trade_count": top_count,
        "top_1_percent_positive_profit_share": sum(positive[:top_count]) / positive_sum if positive_sum else None,
        "net_excluding_best_trade": total - sum(descending[:1]),
        "net_excluding_best_5_trades": total - sum(descending[:5]),
        "net_excluding_best_1_percent_trades": total - sum(descending[:top_count]),
        "net_excluding_best_5_sessions": sum(session_net) - sum(session_net[:5]),
        "net_excluding_best_calendar_year": sum(session_net) - max(year_net.values(), default=0),
        "best_calendar_year_net_pnl": max(year_net.values(), default=0),
    }


def historical_screen(summaries):
    """Apply the registered numerical screen; never imply final confirmation.

    Keys are (period, slippage_ticks). Missing experiments remain unevaluated,
    including failed runs, never silently passing. Parameter-neighbor and unseen
    data requirements remain pending until their separate evidence is supplied.
    """
    gates = []

    def gate(name, observed, threshold, passed):
        gates.append({"gate": name, "observed": observed, "required": threshold,
                      "status": "not_evaluated" if observed is None else "pass" if passed else "fail"})

    full = summaries.get(("full", 1.0))
    total = full["metrics"]["trade_count"] if full else None
    gate("full_history_base_cost_trade_count", total, ">= 500", total is not None and total >= 500)
    for period in ("development", "diagnostic"):
        for slip in (1.0, 2.0):
            result = summaries.get((period, slip))
            net = result["metrics"]["net_pnl"] if result else None
            gate(f"{period}_net_at_{slip:g}_ticks", net, "> 0", net is not None and net > 0)
    profitable_years = (
        sum(full["years"].get(str(year), {}).get("mark_to_market_net_pnl", 0) > 0 for year in range(2020, 2026))
        if full else None
    )
    gate("profitable_2020_through_2025_session_marked_years", profitable_years, ">= 5", profitable_years is not None and profitable_years >= 5)
    diagnostic = summaries.get(("diagnostic", 1.0))
    pf = diagnostic["metrics"]["profit_factor"] if diagnostic else None
    gate("diagnostic_base_cost_profit_factor", pf, ">= 1.10", pf is not None and pf >= 1.10)
    estimates = {row["block_sessions"]: row for row in full["uncertainty"]["estimates"]} if full else {}
    for block in (5, 20):
        interval = estimates.get(block, {}).get("mean_session_pnl_95_percent_interval")
        lower = interval[0] if interval else None
        gate(f"full_base_cost_{block}_session_bootstrap_lower_95_percent", lower, "> 0", lower is not None and lower > 0)
    for concentration_key in ("net_excluding_best_5_trades", "net_excluding_best_calendar_year"):
        net = full["concentration"][concentration_key] if full else None
        gate(concentration_key, net, "> 0", net is not None and net > 0)
    status = "fails_registered_screen" if any(row["status"] == "fail" for row in gates) else (
        "incomplete" if any(row["status"] == "not_evaluated" for row in gates) else "passes_measured_gates_only"
    )
    return {
        "status": status, "gates": gates,
        "pending_requirements": ["predeclared nearby parameter variants", "frozen candidate on genuinely untouched evaluation data"],
        "confirmed_profitability": False,
        "scope": "Fixed September 4, 2026 protocol; bootstrap/concentration/year gates use full-history one-tick cost run; diagnostic PF uses one-tick run.",
        "limitations": REUSED_HISTORY,
    }
