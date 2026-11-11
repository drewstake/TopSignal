"""Bounded display evidence from the existing risk pass; never evaluates or routes."""


def snapshot(blocks, *, checked_at, episode_at, authoritative):
    groups = (
        ("account", "Practice account eligible", ("account_not_active", "account_cannot_trade", "account_automation_", "account_type_", "live_funded_", "shared_broker_account_")),
        ("position", "Position and working orders", ("dom_entry_requires_flat", "mathematical_exit_pending", "working_order", "max_contracts", "max_open_position", "max_account_gross_position", "invalid_account_exposure", "invalid_position_limit", "unresolved_order_submission", "order_reconciliation_", "recent_live_submission_", "account_position_", "account_working_order_")),
        ("loss", "Daily loss budget and stop risk", ("max_daily_loss", "daily_loss_limit_reached", "invalid_daily_pnl", "proposed_stop_risk", "account_unrealized_pnl")),
        ("profit", "Daily profit protection", ("daily_profit_",)),
        ("count", "Daily entry limit", ("max_trades_per_day", "invalid_daily_trade_count")),
        ("cooldown", "Entry cooldown", ("cooldown_",)),
        ("session", "Trading session and cutoff", ("outside_session", "exchange_session_closed", "entry_too_close_to_session_close")),
        ("signal", "Final depth recheck", ("dom_signal_expired", "missing_market_data", "stale_market_data", "invalid_market_data_age")),
        ("order", "Order size and protective stop", ("dom_size_invalid", "dom_instrument_invalid", "dom_signal_invalid", "contract_not_allowed", "invalid_order_size", "order_size_", "fractional_contract_size", "invalid_resulting_position", "protective_stop_", "strategy_stop_", "instrument_tick_", "authoritative_target_")),
    )
    checks, used = [], set()
    for key, label, prefixes in groups:
        matches = [b for b in blocks if b.code.startswith(prefixes)]
        used.update(b.code for b in matches)
        # A preliminary/local pass cannot certify broker entry readiness.
        checks.append({"id": key, "label": label,
                       "status": "blocked" if matches else "passed" if authoritative else "unknown",
                       "detail": "; ".join(b.message for b in matches) if matches else
                                 "Passed at the recorded broker check." if authoritative else
                                 "Awaiting the final broker check."})
    for block in blocks:
        if block.code not in used:
            checks.append({"id": block.code, "label": "Additional entry check",
                           "status": "blocked", "detail": block.message})
    return {"checked_at": checked_at.isoformat(), "episode_at": episode_at,
            "phase": "broker" if authoritative else "preliminary", "checks": checks}
