from __future__ import annotations

import math
from dataclasses import dataclass


MAX_SUPPORTED_ORDER_SIZE = 10_000


@dataclass(frozen=True)
class RiskBlock:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class RiskEvaluationContext:
    bot_enabled: bool
    account_state: str
    account_can_trade: bool | None
    live_funded_account: bool
    configured_execution_mode: str
    dry_run: bool
    confirm_live_order_routing: bool
    running_under_tests: bool
    live_environment_enabled: bool
    contract_allowed: bool
    action: str
    order_size: float
    resulting_position_qty: float
    max_contracts: float
    max_open_position: float
    trades_today: int
    max_trades_per_day: int
    daily_pnl: float
    max_daily_loss: float
    latest_candle_age_seconds: float | None
    max_data_staleness_seconds: int
    inside_trading_session: bool
    delayed_session_block: RiskBlock | None = None
    cooldown_block: RiskBlock | None = None
    position_reducing: bool = False
    defer_entry_risk_until_authoritative: bool = False
    account_automation_eligible: bool | None = None
    require_account_classification: bool = False
    account_gross_position_qty: float = 0.0
    max_account_gross_position: float | None = None
    account_unrealized_pnl: float = 0.0
    unrealized_pnl_complete: bool = True
    proposed_stop_risk: float | None = None
    require_proposed_stop_risk: bool = False
    exchange_session_open: bool = True


def evaluate_risk(context: RiskEvaluationContext) -> list[RiskBlock]:
    """Pure, ordered execution-risk policy; data access stays in the service adapter."""

    blocks: list[RiskBlock] = []
    if not context.bot_enabled:
        blocks.append(RiskBlock(code="bot_disabled", message="Bot is disabled.", severity="critical"))
    if context.account_state != "ACTIVE":
        blocks.append(
            RiskBlock(
                code="account_not_active",
                message=f"Account state is {context.account_state}; only ACTIVE accounts can execute bot orders.",
                severity="critical",
            )
        )
    if not context.dry_run:
        if context.configured_execution_mode != "live":
            blocks.append(
                RiskBlock(
                    code="live_execution_not_configured",
                    message="This bot is configured for dry-run execution and cannot be overridden per request.",
                    severity="critical",
                )
            )
        elif not context.confirm_live_order_routing:
            blocks.append(
                RiskBlock(
                    code="live_order_confirmation_missing",
                    message="Live order routing requires explicit confirmation for this request.",
                    severity="critical",
                )
            )
        elif context.running_under_tests:
            blocks.append(
                RiskBlock(
                    code="live_execution_disabled_in_tests",
                    message="Live order routing is disabled in test environments.",
                    severity="critical",
                )
            )
        elif not context.live_environment_enabled:
            blocks.append(
                RiskBlock(
                    code="live_execution_environment_disabled",
                    message="Live order routing is disabled by the server environment.",
                    severity="critical",
                )
            )
        if context.account_can_trade is not True:
            blocks.append(
                RiskBlock(
                    code="account_cannot_trade",
                    message="Provider has not explicitly confirmed this account is tradable.",
                    severity="critical",
                )
            )
        if context.live_funded_account:
            blocks.append(
                RiskBlock(
                    code="live_funded_api_blocked",
                    message="Live Funded Account naming detected; ProjectX API automation is blocked for this account type.",
                    severity="critical",
                )
            )
        if context.require_account_classification:
            if context.account_automation_eligible is None:
                blocks.append(
                    RiskBlock(
                        code="account_automation_classification_unknown",
                        message=(
                            "ProjectX did not explicitly classify this account as simulated. "
                            "Connect the ProjectX account stream and wait for a fresh "
                            "GatewayUserAccount classification before live automation can route."
                        ),
                        severity="critical",
                    )
                )
            elif not context.account_automation_eligible:
                blocks.append(
                    RiskBlock(
                        code="account_type_not_eligible_for_automation",
                        message="ProjectX classifies this account as non-simulated; live automation is blocked.",
                        severity="critical",
                    )
                )

    # Checks below this point govern creation or growth of exposure.  A verified
    # reduction must remain available when a daily/session/data entry gate has
    # fired.  The first live pass deliberately defers these checks until the
    # account-locked provider preflight can classify the action authoritatively.
    entry_risk_applies = not (
        context.position_reducing or context.defer_entry_risk_until_authoritative
    )

    if entry_risk_applies and not context.contract_allowed:
        blocks.append(RiskBlock(code="contract_not_allowed", message="Contract is outside this bot's allowed contract list."))
    order_size_is_finite = math.isfinite(context.order_size)
    resulting_position_is_finite = math.isfinite(context.resulting_position_qty)
    max_contracts_is_valid = (
        math.isfinite(context.max_contracts)
        and 0 < context.max_contracts <= MAX_SUPPORTED_ORDER_SIZE
    )
    max_open_position_is_valid = (
        math.isfinite(context.max_open_position)
        and 0 < context.max_open_position <= MAX_SUPPORTED_ORDER_SIZE
    )
    if not order_size_is_finite or context.order_size <= 0:
        blocks.append(RiskBlock(code="invalid_order_size", message="Computed order size must be positive."))
    elif context.order_size > MAX_SUPPORTED_ORDER_SIZE:
        blocks.append(
            RiskBlock(
                code="order_size_too_large",
                message=f"Computed order size exceeds the supported limit of {MAX_SUPPORTED_ORDER_SIZE} contracts.",
                severity="critical",
            )
        )
    elif abs(context.order_size - round(context.order_size)) > 1e-9:
        blocks.append(RiskBlock(code="fractional_contract_size", message="ProjectX futures order size must be a whole number."))
    if not resulting_position_is_finite:
        blocks.append(
            RiskBlock(
                code="invalid_resulting_position",
                message="Resulting position could not be represented safely.",
                severity="critical",
            )
        )
    if entry_risk_applies and (
        not max_contracts_is_valid or not max_open_position_is_valid
    ):
        blocks.append(
            RiskBlock(
                code="invalid_position_limit",
                message=(
                    "Configured position limits must be finite positive values no greater than "
                    f"{MAX_SUPPORTED_ORDER_SIZE} contracts."
                ),
                severity="critical",
            )
        )
    elif resulting_position_is_finite and entry_risk_applies:
        if abs(context.resulting_position_qty) > context.max_contracts:
            blocks.append(RiskBlock(code="max_contracts", message="Resulting position exceeds max contracts."))
        if abs(context.resulting_position_qty) > context.max_open_position:
            blocks.append(RiskBlock(code="max_open_position", message="Resulting position exceeds max open position setting."))
    if entry_risk_applies:
        account_gross_is_valid = math.isfinite(context.account_gross_position_qty)
        max_account_gross_is_valid = (
            context.max_account_gross_position is not None
            and math.isfinite(context.max_account_gross_position)
            and context.max_account_gross_position > 0
        )
        if context.max_account_gross_position is not None and (
            not account_gross_is_valid or not max_account_gross_is_valid
        ):
            blocks.append(
                RiskBlock(
                    code="invalid_account_exposure_risk_data",
                    message="Authoritative account-wide exposure data is invalid.",
                    severity="critical",
                )
            )
        elif (
            context.max_account_gross_position is not None
            and context.account_gross_position_qty > context.max_account_gross_position
        ):
            blocks.append(
                RiskBlock(
                    code="max_account_gross_position",
                    message="Resulting account-wide gross position exceeds the configured position limit.",
                    severity="critical",
                )
            )

        if (
            not isinstance(context.trades_today, int)
            or not isinstance(context.max_trades_per_day, int)
            or context.trades_today < 0
            or context.max_trades_per_day < 0
        ):
            blocks.append(
                RiskBlock(
                    code="invalid_daily_trade_count",
                    message="Daily trade count and configured limit must be non-negative integers.",
                    severity="critical",
                )
            )
        elif context.trades_today >= context.max_trades_per_day:
            blocks.append(RiskBlock(code="max_trades_per_day", message="Daily bot trade limit has been reached."))

        daily_values_are_finite = (
            math.isfinite(context.daily_pnl)
            and math.isfinite(context.max_daily_loss)
            and math.isfinite(context.account_unrealized_pnl)
        )
        if not context.unrealized_pnl_complete:
            blocks.append(
                RiskBlock(
                    code="account_unrealized_pnl_unavailable",
                    message="Authoritative unrealized P&L is unavailable for one or more open account positions.",
                    severity="critical",
                )
            )
        elif not daily_values_are_finite or context.max_daily_loss < 0:
            blocks.append(
                RiskBlock(
                    code="invalid_daily_pnl_risk_data",
                    message="Daily P&L must be finite and the daily loss limit non-negative.",
                    severity="critical",
                )
            )
        else:
            account_day_pnl = context.daily_pnl + context.account_unrealized_pnl
            if account_day_pnl <= -context.max_daily_loss:
                blocks.append(
                    RiskBlock(
                        code="max_daily_loss",
                        message="Account has reached the configured daily loss limit including unrealized P&L.",
                        severity="critical",
                    )
                )
            if context.require_proposed_stop_risk:
                stop_risk = context.proposed_stop_risk
                if stop_risk is None or not math.isfinite(stop_risk) or stop_risk <= 0:
                    blocks.append(
                        RiskBlock(
                            code="proposed_stop_risk_unavailable",
                            message="The proposed entry's provider-held stop risk could not be verified.",
                            severity="critical",
                        )
                    )
                elif account_day_pnl - stop_risk <= -context.max_daily_loss:
                    blocks.append(
                        RiskBlock(
                            code="proposed_stop_risk_exceeds_daily_loss_budget",
                            message=(
                                "The proposed entry's stop risk exceeds the account's remaining daily loss budget."
                            ),
                            severity="critical",
                        )
                    )

        if context.delayed_session_block is not None:
            blocks.append(context.delayed_session_block)
        if context.latest_candle_age_seconds is None:
            blocks.append(
                RiskBlock(
                    code="missing_market_data",
                    message="No closed candle data is available.",
                    severity="critical",
                )
            )
        elif (
            not math.isfinite(context.latest_candle_age_seconds)
            or context.latest_candle_age_seconds < 0
            or not math.isfinite(context.max_data_staleness_seconds)
            or context.max_data_staleness_seconds <= 0
        ):
            blocks.append(
                RiskBlock(
                    code="invalid_market_data_age",
                    message="Closed-candle age must be finite and non-negative with a positive staleness limit.",
                    severity="critical",
                )
            )
        elif context.latest_candle_age_seconds > context.max_data_staleness_seconds:
            blocks.append(RiskBlock(code="stale_market_data", message="Latest candle is stale.", severity="critical"))
        if not context.inside_trading_session:
            blocks.append(RiskBlock(code="outside_session", message="Current time is outside the bot trading session."))
        if not context.exchange_session_open:
            blocks.append(
                RiskBlock(
                    code="exchange_session_closed",
                    message="The exchange session is closed for this contract.",
                    severity="critical",
                )
            )
        if context.cooldown_block is not None:
            blocks.append(context.cooldown_block)
    if context.action not in {"BUY", "SELL"}:
        blocks.append(RiskBlock(code="unsupported_action", message="Only BUY and SELL actions can create order attempts."))
    return blocks


__all__ = ["MAX_SUPPORTED_ORDER_SIZE", "RiskBlock", "RiskEvaluationContext", "evaluate_risk"]
