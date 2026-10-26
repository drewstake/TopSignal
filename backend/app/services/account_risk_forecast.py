"""Read-only, session-block bootstrap of recorded net trade outcomes.

No broker calls, SQL writes, new candle caches, or live risk-policy changes.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import numpy as np
from sqlalchemy.orm import Session

from ..models import BotBacktest, ProjectXTradeEvent
from ..risk_forecast_schemas import RiskForecastRequest
from .projectx_accounts import get_projectx_account_row
from .projectx_metrics import _effective_fee
from .projectx_trades import _non_voided_trade_event_expr, _to_metric_sample
from .topbot_backtest_jobs import jobs
from .trading_day import as_utc, trading_day_date, trading_day_key

VERSION = "account-risk-session-blocks-v1"
PATHS = 2000
BLOCK_DAYS = 3
MIN_DAYS = 20
MIN_TRADES = 30
MAX_TRADES = 50000
MAX_TRADES_PER_DAY = 2000
MULTIPLIERS = (.5, 1., 1.5)


@dataclass(frozen=True)
class Outcome:
    timestamp: datetime
    day: str
    net: float


def owned_account(db: Session, owner: str, account_id: int):
    account = get_projectx_account_row(db, account_id, user_id=owner)
    if account is None:
        raise LookupError("Account not found.")
    return account


def sources(db: Session, owner: str, account_id: int) -> dict:
    account = owned_account(db, owner, account_id)
    saved = (db.query(BotBacktest.id, BotBacktest.strategy_type, BotBacktest.actual_start,
                      BotBacktest.actual_end, BotBacktest.created_at)
             .filter(BotBacktest.user_id == owner, BotBacktest.account_id == account_id)
             .order_by(BotBacktest.created_at.desc(), BotBacktest.id.desc()).limit(20).all())
    choices = [{"id": "trades", "label": "Recorded account trades", "kind": "trades"}]
    choices += [{"id": f"saved:{r.id}", "kind": "saved",
                 "label": f"Saved #{r.id} · {r.strategy_type} · {r.actual_start:%Y-%m-%d} – {r.actual_end:%Y-%m-%d}"}
                for r in saved]
    # Local historical runs belong to a user, not an account. Explicitly label
    # them as scenarios applied to this account's independently entered limits.
    choices += [{"id": f"job:{r['id']}", "kind": "job",
                 "label": f"Temporary TopBot scenario · {r['start'][:10]} – {r['end'][:10]} · {r['id'][:8]}"}
                for r in jobs.completed(owner)]
    balance = float(account.balance) if account.balance is not None else None
    return {"account_id": account_id, "balance": balance if balance is not None and math.isfinite(balance) else None,
            "sources": choices}


def _number(value) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("Source has missing or invalid trade outcomes. Repair the source before forecasting.")
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1e9:
        raise ValueError("Source has non-finite or out-of-range trade outcomes.")
    return number


def load_outcomes(db: Session, owner: str, account_id: int, request: RiskForecastRequest,
                  now: datetime) -> tuple[list[Outcome], str, list[str]]:
    owned_account(db, owner, account_id)
    warnings = []
    if request.source_id == "trades":
        cutoff = trading_day_date(now) - timedelta(days=request.lookback_days)
        # Read only existing events. Provider sync is deliberately not invoked.
        rows = (db.query(ProjectXTradeEvent.trade_timestamp, ProjectXTradeEvent.pnl, ProjectXTradeEvent.fees,
                         ProjectXTradeEvent.commissions, ProjectXTradeEvent.fee_scope, ProjectXTradeEvent.trade_date,
                         ProjectXTradeEvent.entry_timestamp, ProjectXTradeEvent.order_id, ProjectXTradeEvent.symbol,
                         ProjectXTradeEvent.contract_id, ProjectXTradeEvent.side, ProjectXTradeEvent.size,
                         ProjectXTradeEvent.price)
                .filter(ProjectXTradeEvent.user_id == owner, ProjectXTradeEvent.account_id == account_id,
                        ProjectXTradeEvent.pnl.isnot(None), _non_voided_trade_event_expr(),
                        ProjectXTradeEvent.trade_timestamp >= now - timedelta(days=request.lookback_days + 2))
                .order_by(ProjectXTradeEvent.trade_timestamp, ProjectXTradeEvent.id)
                .limit(MAX_TRADES + 1).all())
        if len(rows) > MAX_TRADES:
            raise ValueError("Too many recorded trades. Select a shorter lookback (maximum 50,000 trades).")
        outcomes = []
        for row in rows:
            day = (row.trade_date or trading_day_date(row.trade_timestamp)).isoformat()
            if day < cutoff.isoformat():
                continue
            for value in (row.pnl, row.fees, row.size):
                _number(value)
            if row.commissions is not None:
                _number(row.commissions)
            sample = _to_metric_sample(row)
            outcomes.append(Outcome(as_utc(row.trade_timestamp), day, _number(sample.pnl - _effective_fee(sample))))
        label = "Recorded account trades"
        warnings.append("Uses locally stored closed trades with the dashboard's fee and commission treatment. Missing imports or unsynced trades are not reconstructed.")
    else:
        if request.source_id.startswith("saved:"):
            row = (db.query(BotBacktest).filter(BotBacktest.id == int(request.source_id.split(":")[1]),
                                             BotBacktest.user_id == owner, BotBacktest.account_id == account_id).first())
            if row is None:
                raise LookupError("Saved backtest not found for this account.")
            result, label = row.result_snapshot, f"Saved backtest #{row.id} · {row.strategy_type}"
        else:
            job = jobs.get(owner, request.source_id.split(":")[1])
            if job["status"] != "complete" or not job["result"]:
                raise ValueError("Select a completed historical backtest.")
            result, label = job["result"], f"Temporary TopBot scenario · {job['id'][:8]}"
            warnings.append("This user-owned historical run is a strategy scenario, not this account's executed trade history. Temporary runs expire after one hour or a backend restart.")
        trades = result.get("trades") if isinstance(result, dict) else None
        if not isinstance(trades, list) or len(trades) > MAX_TRADES:
            raise ValueError("Backtest must contain a complete trade list of at most 50,000 trades.")
        metrics = result.get("metrics", {})
        if metrics.get("trade_count") != len(trades):
            raise ValueError("Backtest trade detail is incomplete; summary-only results cannot support a forecast.")
        outcomes = []
        for trade in trades:
            stamp = datetime.fromisoformat(trade["exit_timestamp"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Backtest trade timestamps must include a timezone.")
            outcomes.append(Outcome(as_utc(stamp), trading_day_key(stamp), _number(trade.get("net_pnl"))))
        if not math.isclose(math.fsum(t.net for t in outcomes), _number(metrics.get("net_pnl")), abs_tol=.05, rel_tol=1e-7):
            raise ValueError("Backtest trade outcomes do not reconcile with its saved net P&L.")
        warnings.append("Historical backtest outcomes retain their original execution costs and modelling assumptions; strategy selection and overfitting can make forecasts optimistic.")
    today = trading_day_key(now)
    complete = [t for t in outcomes if t.day < today and t.timestamp < now]
    if len(complete) != len(outcomes):
        warnings.append(f"Excluded {len(outcomes)-len(complete)} outcome(s) from the current or future trading day.")
    return sorted(complete, key=lambda t: (t.day, t.timestamp)), label, warnings


def _interval(hits: np.ndarray) -> dict:
    """Wilson interval measures simulation sampling error, not forecast validity."""
    n, p, z = len(hits), float(np.mean(hits)), 1.96
    center = (p + z*z/(2*n))/(1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return {"probability": round(p, 5), "simulation_interval": [max(0., center-half), min(1., center+half)]}


def simulate(days: list[list[float]], request: RiskForecastRequest, seed: int, *, paths: int = PATHS) -> list[dict]:
    """Same sampled day blocks in every size scenario; account breach is absorbing.

    Daily loss stops skip the remainder of that sampled day and reset on the next.
    We check realized P&L at each recorded close, never infer intratrade excursions.
    """
    rng = np.random.default_rng(seed)
    block = min(BLOCK_DAYS, len(days))
    starts = rng.integers(0, len(days)-block+1, size=(paths, math.ceil(request.horizon_days/block)))
    indices = (starts[:, :, None] + np.arange(block)).reshape(paths, -1)[:, :request.horizon_days]
    lengths = np.array([len(day) for day in days])
    prefixes = [np.cumsum(np.asarray(day) - request.extra_cost_per_trade) for day in days]
    results = []
    for multiplier in MULTIPLIERS:
        equity = np.full(paths, request.starting_balance, dtype=float)
        peak = equity.copy()
        floor = equity - request.loss_buffer
        breached, daily_hit = np.zeros(paths, dtype=bool), np.zeros(paths, dtype=bool)
        drawdown = np.zeros(paths)
        fan = [{"day": 0, "p10": request.starting_balance, "p50": request.starting_balance, "p90": request.starting_balance}]
        for day in range(request.horizon_days):
            active = np.flatnonzero(~breached)
            # At most 128 x 2,000 outcome cells per working matrix, regardless
            # of source history size. Never allocate history_days x max_trades.
            for offset in range(0, len(active), 128):
                ids = active[offset:offset+128]
                selected = indices[ids, day]
                count = lengths[selected]
                width = int(max(count))
                cumulative = np.zeros((len(ids), width))
                for row, sample in enumerate(selected):
                    cumulative[row, :count[row]] = prefixes[sample] * multiplier
                positions = np.arange(width)[None, :]
                values = equity[ids, None] + cumulative
                valid = positions < count[:, None]
                daily = valid & (cumulative <= -request.daily_loss_limit) if request.daily_loss_limit else np.zeros_like(valid)
                daily_first = np.where(daily.any(axis=1), daily.argmax(axis=1), count)
                if request.trailing_mode == "trade_close":
                    session_peak = np.maximum(equity[ids, None], np.maximum.accumulate(values, axis=1))
                    moving_floor = session_peak - request.trailing_distance
                    if request.floor_cap is not None:
                        moving_floor = np.minimum(moving_floor, request.floor_cap)
                    floors = np.maximum(floor[ids, None], moving_floor)
                else:
                    floors = floor[ids, None]
                failures = valid & (values <= floors)
                failure_first = np.where(failures.any(axis=1), failures.argmax(axis=1), count)
                breached[ids] = (failure_first < count) & (failure_first <= daily_first)
                daily_hit[ids] |= (daily_first < count) & (daily_first <= failure_first)
                final = np.minimum(np.minimum(failure_first, daily_first), count-1)
                observed = valid & (positions <= final[:, None])
                running_peak = np.maximum(peak[ids, None], np.maximum.accumulate(values, axis=1))
                drawdown[ids] = np.maximum(drawdown[ids], np.max(np.where(observed, running_peak-values, 0.), axis=1))
                session_high = np.max(np.where(observed, values, -np.inf), axis=1)
                peak[ids] = np.maximum(peak[ids], session_high)
                equity[ids] = values[np.arange(len(ids)), final]
                if request.trailing_mode != "fixed":
                    anchor = session_high if request.trailing_mode == "trade_close" else equity[ids]
                    updated = anchor - request.trailing_distance
                    if request.floor_cap is not None:
                        updated = np.minimum(updated, request.floor_cap)
                    floor[ids] = np.maximum(floor[ids], updated)
            p10, p50, p90 = np.quantile(equity, [.1, .5, .9])
            fan.append({"day": day+1, "p10": round(float(p10), 2), "p50": round(float(p50), 2), "p90": round(float(p90), 2)})
        results.append({"multiplier": multiplier, "account_breach": _interval(breached), "daily_limit_hit": _interval(daily_hit) if request.daily_loss_limit else None,
                        "ending_balance": {key: round(float(value), 2) for key, value in zip(("p10", "p50", "p90"), np.quantile(equity, [.1, .5, .9]))},
                        "max_drawdown": {key: round(float(value), 2) for key, value in zip(("p50", "p90", "p95"), np.quantile(drawdown, [.5, .9, .95]))},
                        "fan": fan})
    return results


def forecast(outcomes: list[Outcome], request: RiskForecastRequest, label: str, warnings: list[str], now: datetime) -> dict:
    grouped: dict[str, list[float]] = {}
    for trade in outcomes:
        grouped.setdefault(trade.day, []).append(_number(trade.net))
    if grouped and max(map(len, grouped.values())) > MAX_TRADES_PER_DAY:
        raise ValueError("A source day exceeds 2,000 closed trades; this forecast cannot process it without dropping outcomes.")
    fingerprint = hashlib.sha256(json.dumps([(t.timestamp.isoformat(), t.day, t.net) for t in outcomes], separators=(",", ":")).encode()).hexdigest()
    result = {"version": VERSION, "generated_at": now.isoformat(), "source_label": label, "source_id": request.source_id,
              "input_fingerprint": fingerprint, "parameters": request.model_dump(), "paths": PATHS, "block_days": BLOCK_DAYS,
              "sample": {"trades": len(outcomes), "active_days": len(grouped), "first_day": min(grouped) if grouped else None,
                         "last_day": max(grouped) if grouped else None, "net_pnl": round(math.fsum(t.net for t in outcomes), 2)},
              "status": "ready", "warnings": list(warnings), "scenarios": []}
    if len(grouped) < MIN_DAYS or len(outcomes) < MIN_TRADES:
        result["status"] = "insufficient_data"
        result["warnings"].append(f"Need at least {MIN_DAYS} completed active trading days and {MIN_TRADES} closed trades. No probability is estimated from this sample.")
        return result
    if len(grouped) < 60:
        result["warnings"].append("Limited history: fewer than 60 active days. Tail estimates are especially sensitive to the sampled period.")
    if not any(t.net < 0 for t in outcomes):
        result["warnings"].append("No historical losing trades: resampling cannot reveal losses absent from the source. A zero simulated breach rate is not evidence of safety.")
    if (trading_day_date(now) - datetime.fromisoformat(max(grouped)).date()).days > 30:
        result["warnings"].append("The latest source outcome is over 30 days old; current market conditions may differ.")
    result["warnings"] += [
        "Forecast starts at the next active trading day with daily P&L reset to zero. Three-day moving blocks preserve only short clusters of observed trading activity; inactive and missing days are not inferred.",
        "Loss limits are checked at recorded trade closes. Unrealized losses, intratrade peaks, gaps and broker liquidation may breach limits earlier; this is not a broker-rule certification.",
        "Exposure multipliers scale recorded net P&L, existing costs and added costs linearly. They are not exact whole-contract sizing or a new strategy replay; historical entries already reflect their original risk controls.",
        "Percentile bands describe these resampled paths. Simulation intervals measure Monte Carlo noise only, excluding data uncertainty, unseen losses and future regime changes.",
    ]
    seed = int(fingerprint[:8], 16)
    result["scenarios"] = simulate(list(grouped.values()), request, seed)
    return result
