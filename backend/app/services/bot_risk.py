from __future__ import annotations

from dataclasses import dataclass


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
        if context.account_can_trade is False:
            blocks.append(
                RiskBlock(
                    code="account_cannot_trade",
                    message="Provider marks this account as not tradable.",
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
    if not context.contract_allowed:
        blocks.append(RiskBlock(code="contract_not_allowed", message="Contract is outside this bot's allowed contract list."))
    if context.order_size <= 0:
        blocks.append(RiskBlock(code="invalid_order_size", message="Computed order size must be positive."))
    if abs(context.order_size - round(context.order_size)) > 1e-9:
        blocks.append(RiskBlock(code="fractional_contract_size", message="ProjectX futures order size must be a whole number."))
    if abs(context.resulting_position_qty) > context.max_contracts:
        blocks.append(RiskBlock(code="max_contracts", message="Resulting position exceeds max contracts."))
    if abs(context.resulting_position_qty) > context.max_open_position:
        blocks.append(RiskBlock(code="max_open_position", message="Resulting position exceeds max open position setting."))
    if context.trades_today >= context.max_trades_per_day:
        blocks.append(RiskBlock(code="max_trades_per_day", message="Daily bot trade limit has been reached."))
    if context.daily_pnl <= -context.max_daily_loss:
        blocks.append(
            RiskBlock(
                code="max_daily_loss",
                message="Account has reached the configured daily loss limit.",
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
    elif context.latest_candle_age_seconds > context.max_data_staleness_seconds:
        blocks.append(RiskBlock(code="stale_market_data", message="Latest candle is stale.", severity="critical"))
    if not context.inside_trading_session:
        blocks.append(RiskBlock(code="outside_session", message="Current time is outside the bot trading session."))
    if context.cooldown_block is not None:
        blocks.append(context.cooldown_block)
    if context.action not in {"BUY", "SELL"}:
        blocks.append(RiskBlock(code="unsupported_action", message="Only BUY and SELL actions can create order attempts."))
    return blocks


__all__ = ["RiskBlock", "RiskEvaluationContext", "evaluate_risk"]
