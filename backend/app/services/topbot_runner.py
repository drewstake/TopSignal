"""Durable practice-position runner and bounded fill-derived trade statistics.

State lives in the existing entry audit. No tick arrays or history imports.
Excursions are observed BBO excursions and explicitly marked incomplete.
"""
from datetime import datetime, timedelta, timezone
from statistics import mean, median
import math
from time import perf_counter

from .topbot_depth import feed, utc
from .topbot_audit import record, scope, client_for


def runner_plan(now=None, *, exit_mode="trailing"):
    from .topbot_session import flat_deadline, CLOSE_BUFFER
    now = now or datetime.now(timezone.utc)
    if exit_mode not in {"trailing", "fixed"}:
        raise ValueError("Unknown position exit mode")
    return {"status": "pending", "deadline": (flat_deadline(now)-CLOSE_BUFFER).isoformat(),
            "prepared_at": now.isoformat(), "attempts": 0, "reconciliation_cycles": 0,
            "runner": {"version": 1, "exit_mode": exit_mode, "take_profit_points": 25 if exit_mode == "fixed" else None,
                       "initial_stop_points": 25, "activation_points": 25,
                       "trail_points": 25, "stats_status": "awaiting_fill"}}


def desired_stop(side, entry_price, mfe_points, current_stop):
    """Quarter-point prices; never widen, even after restart or a manual tighten."""
    direction = 1 if side == "BUY" else -1
    offset = mfe_points-25 if mfe_points >= 25 else -25
    wanted = entry_price + direction*offset
    wanted = (math.floor(wanted*4) if direction == 1 else math.ceil(wanted*4))/4
    return max(current_stop, wanted) if direction == 1 else min(current_stop, wanted)


def _trades(client, entry, now):
    from .bot_service import _as_utc
    rows = client.fetch_trade_history(account_id=entry.account_id,
                                     start=_as_utc(entry.created_at)-timedelta(minutes=1), end=now,
                                     require_valid_collection=True)
    scoped = [r for r in rows if r["account_id"] == entry.account_id and r["contract_id"] == entry.contract_id and not r["voided"]]
    unique, seen = [], {}
    for row in scoped:
        identity = row.get("source_trade_id")
        if identity is not None:
            key = str(identity)
            if key in seen:
                # Identical retries are one execution. Conflicting versions are
                # retained for explicit reconciliation, never silently replaced.
                fields = ("order_id", "side", "size", "price", "timestamp", "fees", "commissions")
                if all(row.get(k) == seen[key].get(k) for k in fields): continue
            seen[key] = row
        unique.append(row)
    return unique


def initialize(entry, plan, client, now):
    runner = plan["runner"]
    if runner.get("entry_at"): return True
    fills = [r for r in _trades(client, entry, now) if str(r["order_id"]) == str(entry.provider_order_id)]
    if not fills: return False
    if len(fills) != 1 or fills[0]["side"] != entry.side or fills[0]["size"] != 1 or float(entry.size) != 1:
        runner["stats_status"] = "ambiguous_entry_fills"
        runner["reconciliation"] = {"entry_fill_count": len(fills), "reason": "partial_or_mismatched_entry_fills"}
        raise ValueError("Runner requires an exact single-contract entry fill; reconcile mismatched exposure.")
    fill = fills[0]
    if not math.isfinite(fill["price"]) or fill["price"] <= 0: raise ValueError("Invalid entry fill price")
    runner.update(entry_at=fill["timestamp"].isoformat(), entry_price=fill["price"], entry_fees=fill["fees"],
                  side=entry.side, mfe_points=0., mae_points=0., stats_status="open", coverage_complete=False)
    runner.update(entry_order_id=str(entry.provider_order_id), entry_fill_id=fill.get("source_trade_id", fill.get("trade_id", fill.get("id"))),
                  quantity=float(fill["size"]), contract_id=entry.contract_id, account_id=entry.account_id,
                  initial_risk_usd=float(runner.get("initial_stop_points", 25))*2*float(fill["size"]))
    from .topbot_execution_trace import entry_metrics
    observed_at = datetime.now(timezone.utc).isoformat()
    runner["execution"] = entry_metrics(getattr(entry, "raw_request", None) or {}, entry.side, fill["price"], runner["entry_at"], observed_at)
    plan["position_observed_at"] = now.isoformat()
    record(**scope(entry), kind="entry_fill_verified", operation=f"entry-fill:{entry.id}:{runner['entry_fill_id']}",
           fill_id=runner["entry_fill_id"], quantity=fill["size"], price=fill["price"], fees=fill["fees"],
           fill_at=runner["entry_at"], observed_at=observed_at, order_id=entry.provider_order_id,
           side=entry.side, execution=runner["execution"])
    return True


def manage(db, *, entry, plan, client, stops, worker_lease_token, now, orders=None):
    """Return True when a retraced price requires the existing verified flatten path."""
    from .bot_service import _ProviderMutationFence
    runner = plan["runner"]
    was_trailing = runner.get("trailing_active", False)
    runner.pop("decision_reason", None)
    feed.ensure(entry.user_id, entry.contract_id, client)
    if not initialize(entry, plan, client, now):
        raise ValueError("Waiting for the broker entry fill before managing the runner")
    if len(stops) != 1:
        raise ValueError("Runner requires exactly one matching protective child stop")
    stop = stops[0]
    runner["stop_order_id"] = str(stop["order_id"])
    runner.update(feed.track(entry.user_id, entry.contract_id, entry.id, runner))
    # Preserve the latest broker stop ID after merging streaming excursion state.
    runner["stop_order_id"] = str(stop["order_id"])
    current = float(stop["raw_payload"]["stopPrice"])
    if runner.get("confirmed_stop") != current:
        record(**scope(entry), kind="stop_observed", old_stop=runner.get("confirmed_stop"), new_stop=current,
               order_id=stop["order_id"], reason="Broker protective stop readback")
    # Broker/database reads can outlive the cycle timestamp. Read the clock
    # with the locked book so a newly arrived tick is not rejected as future.
    quote = feed.snapshot(entry.user_id, entry.contract_id)
    checked_at = utc(quote["checked_at"]) if quote.get("checked_at") else datetime.now(timezone.utc)
    # Protection verification is independent of receiving a new quote.
    initial = runner["entry_price"] + (-25 if entry.side == "BUY" else 25)
    if (entry.side == "BUY" and current < initial-.001) or (entry.side == "SELL" and current > initial+.001):
        plan["runner_exit_reason"] = "protective_stop_too_wide"
        return True
    verified_at = datetime.now(timezone.utc).isoformat()
    execution = runner.setdefault("execution", {})
    if "initial_protection_observed_at" not in execution:
        from .topbot_execution_trace import delta_ms
        execution["initial_protection_observed_at"] = verified_at
        execution["fill_to_protection_observation_ms"] = delta_ms(verified_at, runner["entry_at"])
        record(**scope(entry), kind="initial_protection_verified", order_id=stop["order_id"],
               stop_price=current, observed_at=verified_at,
               fill_to_observation_ms=execution["fill_to_protection_observation_ms"])
    runner["confirmed_stop"] = current
    fixed = runner.get("exit_mode") == "fixed"
    if fixed:
        target_price = runner["entry_price"] + (25 if entry.side == "BUY" else -25)
        targets = [order for order in (orders or [])
                   if order.get("account_id") == entry.account_id and order.get("contract_id") == entry.contract_id
                   and str(order.get("parent_order_id")) == str(entry.provider_order_id)
                   and order.get("order_type") == 1 and order.get("status") == 1
                   and order.get("side") == (1 if entry.side == "BUY" else 0)
                   and order.get("size") == float(entry.size)
                   and (order.get("raw_payload") or {}).get("limitPrice") == target_price]
        if len(targets) != 1:
            plan["runner_exit_reason"] = "fixed_take_profit_not_verified"
            return True
        runner.update(confirmed_target=target_price, target_order_id=str(targets[0]["order_id"]),
                      trailing_active=False, desired_stop=current, management_status="Fixed stop and target",
                      decision_reason="Keep the broker's fixed 25-point stop and 25-point target")
    if not quote.get("ready") or not quote.get("observed_at") or not 0 <= (checked_at-utc(quote["observed_at"])).total_seconds() <= 2:
        runner["management_status"] = "Waiting for fresh prices; broker stop remains active"
        return False
    mark = quote["bid"] if entry.side == "BUY" else quote["ask"]
    runner["last_market_price"] = mark
    runner["last_price_observed_at"] = quote["observed_at"]
    points = (mark-runner["entry_price"])*(1 if entry.side == "BUY" else -1)
    runner["mfe_points"] = max(runner["mfe_points"], points)
    runner["mae_points"] = min(runner["mae_points"], points)
    for label, crossed in (("plus_10", points >= 10), ("plus_25", points >= 25), ("minus_10", points <= -10)):
        if crossed and label not in runner:
            runner[label] = max(0, (utc(quote["observed_at"])-utc(runner["entry_at"])).total_seconds())
    if fixed:
        return False
    wanted = desired_stop(entry.side, runner["entry_price"], runner["mfe_points"], current)
    runner["trailing_active"] = runner["mfe_points"] >= 25
    if runner["trailing_active"] and not was_trailing:
        record(**scope(entry), kind="trailing_activated", market_price=mark, mfe_points=runner["mfe_points"],
               old_stop=current, new_stop=wanted, reason="Observed favorable excursion reached +25 points")
    runner["management_status"] = "Trailing" if runner["trailing_active"] else "Initial stop"
    runner["desired_stop"] = wanted
    if wanted == current:
        runner["decision_reason"] = "Keep existing stop; calculated stop would not tighten protection"
        return False
    runner["decision_reason"] = "Tighten stop using 25-point trail"
    if (entry.side == "BUY" and mark <= wanted) or (entry.side == "SELL" and mark >= wanted):
        plan["runner_exit_reason"] = "trailing_level_crossed"
        return True
    fence = _ProviderMutationFence(db=db, token=worker_lease_token, user_id=entry.user_id, account_id=entry.account_id)
    fence.require()
    client = client_for(client, entry, old_stop=current, new_stop=wanted, market_price=mark,
                        depth_evidence=quote.get("evidence"),
                        reason="25-point trail from best observed executable price")
    client.modify_stop_order(account_id=entry.account_id, order_id=stop["order_id"], stop_price=wanted)
    # Never cancel/recreate a protective stop. An ambiguous modification is
    # reconciled from current broker state on the next cycle, not blindly retried.
    verified = [r for r in client.search_open_orders(account_id=entry.account_id)
                if str(r["order_id"]) == str(stop["order_id"]) and r["account_id"] == entry.account_id
                and r["contract_id"] == entry.contract_id]
    if len(verified) != 1 or verified[0]["raw_payload"].get("stopPrice") != wanted:
        raise ValueError("Stop modification not yet verified at the broker")
    runner["confirmed_stop"] = wanted
    runner["stop_verified_at"] = datetime.now(timezone.utc).isoformat()
    runner["last_verified_trail_stop"] = wanted
    runner["execution"]["last_stop_request_to_verified_ms"] = round((perf_counter()-client.request_started)*1000, 3)
    record(**scope(entry), kind="stop_change_verified", operation=client.last_operation,
           old_stop=current, new_stop=wanted, market_price=mark, order_id=stop["order_id"],
           request_to_verified_ms=runner["execution"]["last_stop_request_to_verified_ms"],
           verified_at=runner["stop_verified_at"],
           reason="Broker readback matches requested trailing stop")
    return False


def finalize(entry, plan, client, now):
    """Broker-flat is verified by the caller. Missing/ambiguous fills stay unknown."""
    runner = plan["runner"]
    if runner.get("stats_status") == "complete": return True
    if not initialize(entry, plan, client, now):
        if entry.status == "cancelled":
            runner["stats_status"] = "not_filled"
            return True
        runner["stats_status"] = "awaiting_fill"
        return False
    rows = [r for r in _trades(client, entry, now) if r["timestamp"] >= utc(runner["entry_at"])]
    exits = [r for r in rows if str(r["order_id"]) != str(entry.provider_order_id)]
    if not exits:
        runner["stats_status"] = "awaiting_exit_fill"
        return False
    if len(exits) != 1 or exits[0]["side"] == entry.side or exits[0]["size"] != 1:
        runner["stats_status"] = "ambiguous_fills"
        runner["reconciliation"] = {"exit_fill_count": len(exits), "reason": "partial_or_ambiguous_exit_fills"}
        return False
    exit_fill = exits[0]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in
           (exit_fill["price"], exit_fill["fees"], runner["entry_fees"])):
        runner["stats_status"] = "invalid_fill_values"
        return False
    runner.update(feed.track(entry.user_id, entry.contract_id, entry.id, runner, end_at=exit_fill["timestamp"]))
    direction = 1 if entry.side == "BUY" else -1
    points = (exit_fill["price"]-runner["entry_price"])*direction
    costs = cost_summary(runner["entry_fees"]+exit_fill["fees"], points,
                         exit_fill["timestamp"], entry.contract_id, exit_fill.get("commissions"))
    fees = costs["fees_usd"]
    from .topbot_study import exit_classification
    # A fast native bracket may fill before management sees its working child.
    # Match broker order identity; price resemblance is never proof of an exit.
    if str(exit_fill["order_id"]) not in {runner.get("stop_order_id"), runner.get("target_order_id")} and hasattr(client, "search_orders"):
        try:
            history = client.search_orders(account_id=entry.account_id, start=utc(runner["entry_at"])-timedelta(minutes=1), end=now)
            matching = [o for o in history if o.get("account_id") == entry.account_id and o.get("contract_id") == entry.contract_id
                and str(o.get("order_id")) == str(exit_fill["order_id"])
                and str(o.get("parent_order_id")) == str(entry.provider_order_id)
                and o.get("status") == 2 and o.get("size") == float(entry.size)
                and o.get("side") == (1 if entry.side == "BUY" else 0)]
            if len(matching) == 1:
                key = {1: "target_order_id", 4: "stop_order_id"}.get(matching[0].get("order_type"))
                if key:
                    runner[key] = str(exit_fill["order_id"])
                    price_key = "limitPrice" if key == "target_order_id" else "stopPrice"
                    order_price = (matching[0].get("raw_payload") or {}).get(price_key)
                    if isinstance(order_price, (float, int)) and math.isfinite(order_price):
                        runner["confirmed_target" if key == "target_order_id" else "confirmed_stop"] = order_price
        except Exception:
            runner["exit_order_history_status"] = "unavailable"
    reason, reason_basis = exit_classification(runner, plan, exit_fill["order_id"])
    runner.setdefault("initial_risk_usd", float(runner.get("initial_stop_points", 25))*2*float(entry.size))
    runner.update(stats_status="complete", exit_at=exit_fill["timestamp"].isoformat(), exit_price=exit_fill["price"],
                  hold_seconds=max(0,(exit_fill["timestamp"]-utc(runner["entry_at"])).total_seconds()),
                  gross_points=points, fees_usd=fees, net_pnl_usd=points*2-fees,
                  exit_reason=reason, exit_reason_basis=reason_basis,
                  exit_order_id=str(exit_fill["order_id"]), exit_fill_id=exit_fill.get("source_trade_id", exit_fill.get("trade_id", exit_fill.get("id"))),
                  gross_pnl_usd=points*2, realized_r=(points*2-fees)/runner["initial_risk_usd"])
    runner.update(costs)
    runner["mfe_points"] = max(runner["mfe_points"], points)
    runner["mae_points"] = min(runner["mae_points"], points)
    runner["giveback_points"] = runner["mfe_points"]-points
    execution = runner.setdefault("execution", {})
    from .topbot_execution_trace import delta_ms
    execution["exit_fill_to_observation_ms"] = delta_ms(datetime.now(timezone.utc).isoformat(), runner["exit_at"])
    if str(exit_fill["order_id"]) == runner.get("stop_order_id") and runner.get("confirmed_stop") is not None:
        execution["exit_slippage_from_last_verified_stop_ticks"] = round((runner["confirmed_stop"]-exit_fill["price"])*direction*4, 6)
    record(**scope(entry), kind="exit_fill_verified", operation=f"exit-fill:{entry.id}:{runner['exit_fill_id']}",
           fill_id=runner["exit_fill_id"], quantity=exit_fill["size"], order_id=exit_fill["order_id"],
           broker_exit_fees=exit_fill["fees"], broker_fees_usd=costs["broker_fees_usd"],
           commissions_usd=costs["commissions_usd"], commission_basis=costs["commission_basis"],
           gross_pnl_usd=runner["gross_pnl_usd"], initial_risk_usd=runner["initial_risk_usd"], realized_r=runner["realized_r"],
           exit_price=exit_fill["price"], exit_at=runner["exit_at"], reason=runner["exit_reason"],
           net_pnl_usd=runner["net_pnl_usd"], fees_usd=fees, hold_seconds=runner["hold_seconds"],
           execution=execution, coverage=runner.get("coverage"))
    feed.untrack(entry.user_id, entry.contract_id, entry.id)
    return True


def cost_summary(broker_fees, gross_points, exit_at, contract_id, commissions=None):
    """Use the same round-turn commission basis as the account trade journal."""
    from .topstep_fees import effective_topstep_trade_fee
    if any(not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (broker_fees, gross_points)):
        raise ValueError("Invalid runner cost inputs")
    fees = effective_topstep_trade_fee(trade_timestamp=exit_at, pnl=gross_points*2,
        fees=broker_fees, symbol="MNQ", contract_id=contract_id, size=1,
        raw_fee_is_per_side=False, commissions=commissions)
    if not math.isfinite(fees): raise ValueError("Invalid runner commission")
    return {"fee_basis": "broker_fees_plus_commission_v1", "broker_fees_usd": broker_fees,
            "commission_basis": "broker_explicit" if commissions is not None else "configured_round_turn_schedule",
            "commissions_usd": fees-broker_fees, "fees_usd": fees, "net_pnl_usd": gross_points*2-fees}


def statistics_item(row):
    item = {"entry_id": row.id, "side": row.side, "attempt_status": getattr(row, "status", None),
            **row.raw_request["timeExit"]["runner"]}
    from .topbot_study import classify
    item.update(classify(row.raw_request))
    from .trading_day import trading_day_key
    if item.get("entry_at"): item["session"] = trading_day_key(utc(item["entry_at"]))
    if (item.get("stats_status") == "complete" and not item.get("fee_basis")
            and all(item.get(k) is not None for k in ("fees_usd", "gross_points", "exit_at"))):
        # Derive a corrected display without rewriting historical execution/audit records.
        item.update(cost_summary(item["fees_usd"], item["gross_points"], utc(item["exit_at"]), row.contract_id))
        item["costs_derived_from_legacy_record"] = True
    if item.get("initial_stop_points") and item.get("net_pnl_usd") is not None:
        quantity = item.get("quantity", float(getattr(row, "size", 1)))
        risk = item["initial_stop_points"]*2*quantity
        item.update(initial_risk_usd=risk, realized_r=item["net_pnl_usd"]/risk)
    return item


def statistics(db, *, user_id, account_id):
    from ..models import BotOrderAttempt, BotConfig
    config = db.query(BotConfig).filter_by(user_id=user_id, account_id=account_id,
               strategy_type="topbot_adaptive").order_by(BotConfig.id.desc()).first()
    depth = feed.snapshot(user_id, config.contract_id) if config else None
    rows = db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == user_id,
            BotOrderAttempt.account_id == account_id,
            BotOrderAttempt.raw_request["timeExit"]["runner"]["version"].as_integer() == 1
        ).order_by(BotOrderAttempt.id.desc()).limit(500).all()
    items = [statistics_item(row) for row in rows]
    closed = [r for r in items if r.get("stats_status") == "complete"]
    wins = [r["hold_seconds"] for r in closed if r["net_pnl_usd"] > 0]
    losses = [r["hold_seconds"] for r in closed if r["net_pnl_usd"] < 0]
    return {"scope": "Most recent 500 strategy entries on this account", "items": items[:50], "depth": depth,
            "closed_trades": len(closed), "wins": len(wins), "losses": len(losses),
            "net_pnl_usd": sum(r["net_pnl_usd"] for r in closed),
            "winner_hold_mean_seconds": mean(wins) if wins else None,
            "winner_hold_median_seconds": median(wins) if wins else None,
            "loser_hold_mean_seconds": mean(losses) if losses else None,
            "loser_hold_median_seconds": median(losses) if losses else None}


def study_statistics(db, *, user_id, account_id, **filters):
    from ..models import BotOrderAttempt
    from .topbot_study_store import comparison
    rows = db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == user_id,
        BotOrderAttempt.account_id == account_id,
        BotOrderAttempt.raw_request["timeExit"]["runner"]["version"].as_integer() == 1
    ).order_by(BotOrderAttempt.id.desc()).limit(5001).all()
    truncated = len(rows) > 5000
    items = [statistics_item(row) for row in rows[:5000]]
    db.rollback()
    result = comparison(user_id, account_id, items, **filters)
    result["actual_scope"] = "Most recent 5,000 runner entries; dates use UTC entry dates"
    result["actual_truncated"] = truncated
    from .topbot_study_worker import HEALTH
    result["research_health"] = dict(HEALTH)
    return result
