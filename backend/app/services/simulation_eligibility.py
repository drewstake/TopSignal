"""Read-only observation eligibility, independent of broker tradability and signal side.

This is not broker authorization. Portfolio gates run separately on each local
simulated account; no broker positions, fills, or P&L are used as paper state.
"""
import math

from .probabilistic_observation import enabled, stop_policy
from .probabilistic_payoff import PAPER_PAYOFF, timing_matches
from .probabilistic_strategy import BAR, Candle, Costs, contiguous, utc, valid_stop
from .topbot_session import entry_boundary_reason

POLICY = 'simulation-eligibility-v2'


def assessment(codes=(), *, unknown=False, **evidence):
    return dict(policy=POLICY, status='unknown' if unknown else 'blocked' if codes else 'eligible',
                codes=list(dict.fromkeys(codes)), **evidence)


def assess(*, owner, config, account, run, dry_run, candles, forecast, now):
    codes = []
    evidence = dict(checked_at=utc(now).isoformat(), owner=str(owner), account=config.account_id,
                    config=config.id, contract=config.contract_id, mode='simulation_only',
                    account_rules='Independent position, cooldown, daily entries and dollar loss budget per simulated account')
    try:
        if (str(config.user_id) != str(owner) or str(account.user_id) != str(owner)
                or account.provider != 'projectx' or str(account.external_id) != str(config.account_id)
                or account.trade_data_source != 'projectx'):
            codes.append('simulation_ownership_mismatch')
        if not enabled() or dry_run is not True or config.execution_mode != 'dry_run':
            codes.append('simulation_mode_not_authorized')
        if (run is None or run.dry_run is not True or run.status != 'running'
                or str(run.user_id) != str(owner) or run.account_id != config.account_id
                or run.bot_config_id != config.id or not isinstance(run.raw_state, dict)
                or run.raw_state.get('continuous') is not True
                or run.raw_state.get('live_routing_confirmed') is not False):
            codes.append('simulation_run_not_authorized')
        if config.enabled is not True:
            codes.append('bot_disabled')
        from .topbot_mathematical import selected
        if (config.strategy_type != 'topbot_adaptive' or not selected(config.strategy_params)
                or config.timeframe_unit != 'minute' or config.timeframe_unit_number != 5
                or config.symbol != 'MNQ' or not str(config.contract_id).startswith('CON.F.US.MNQ.')
                or config.contract_id not in (config.allowed_contracts or []) and 'MNQ' not in (config.allowed_contracts or [])):
            codes.append('simulation_market_scope_invalid')
        rows = [Candle.from_row(c) for c in candles]
        if rows and rows[-1].partial:
            rows = rows[:-1]
        if (not rows or any(c.owner != str(owner) or c.contract_id != config.contract_id for c in rows)
                or len(rows) < 21 or not contiguous(rows[-21:])):
            return assessment([*codes, 'simulation_market_data_invalid'], **evidence)
        close = rows[-1].timestamp + BAR
        evidence.update(close=close.isoformat(), data_live=rows[-1].live)
        raw_limits = [float(getattr(config, k)) for k in ('max_contracts', 'max_open_position', 'max_daily_loss',
                      'max_trades_per_day', 'cooldown_seconds', 'max_data_staleness_seconds')]
        if any(not math.isfinite(v) or v < 0 for v in raw_limits):
            codes.append('simulation_limits_invalid')
        limits = dict(max_contracts=min(1., float(config.max_contracts), float(config.max_open_position)),
                      max_daily_loss=min(250., float(config.max_daily_loss)),
                      max_trades_per_day=min(30, int(config.max_trades_per_day)),
                      cooldown_seconds=max(300, int(config.cooldown_seconds)),
                      max_age_seconds=min(120, int(config.max_data_staleness_seconds)))
        if (float(config.order_size) != 1 or any(not math.isfinite(v) or v <= 0 for v in limits.values())
                or limits['max_contracts'] < 1):
            codes.append('simulation_limits_invalid')
        evidence['limits'] = limits
        if not 0 <= (utc(now)-close).total_seconds() <= limits['max_age_seconds']:
            codes.append('stale_market_data')
        boundary = entry_boundary_reason(now, decision_at=close)
        if boundary:
            codes.append(boundary)
        if (forecast.get('observation_only') is not True or forecast.get('routing_allowed') is not False
                or forecast.get('integrity_verified') is not True or not forecast.get('artifact_sha256')
                or forecast.get('validation_status') != 'unvalidated' or not forecast.get('forecasts')
                or forecast.get('data_status') != 'fresh' or forecast.get('data_live') is not rows[-1].live
                or forecast.get('model_version') != 'bayesian_cells_v1'
                or forecast.get('probability_basis') != 'uncalibrated_model_estimate'):
            return assessment([*codes, 'simulation_model_unavailable'], unknown=not codes, **evidence)
        if forecast.get('payoff_semantics') != PAPER_PAYOFF:
            codes.append('simulation_artifact_incompatible')
        if forecast.get('candle_close_timestamp') != close.isoformat() or not timing_matches(forecast, close, utc(now)):
            codes.append('simulation_timing_mismatch')
        policy = stop_policy()
        stop = forecast.get('stop_points')
        if (forecast.get('stop_policy', 'registered_25_points') != policy or not valid_stop(stop, policy)
                or forecast.get('target_points') != math.ceil(stop*1.5/.25)*.25
                or forecast.get('quantity') != 1 or forecast.get('costs') != Costs().describe()):
            codes.append('simulation_payoff_invalid')
        evidence['artifact'] = forecast.get('artifact_sha256')
        return assessment(codes, **evidence)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        return assessment([*codes, 'simulation_evidence_invalid'], unknown=True, **evidence)


def at_recording(snapshot, forecast, close, now):
    """Recheck clocks after the application commit; never reinterpret a stale forecast."""
    if not isinstance(snapshot, dict) or snapshot.get('policy') != POLICY:
        return assessment(['simulation_eligibility_not_recorded'], unknown=True)
    result = dict(snapshot, codes=list(snapshot.get('codes', [])), recorded_at=utc(now).isoformat())
    if result.get('status') != 'eligible':
        return result
    if (snapshot.get('close') != close.isoformat()
            or not 0 <= (utc(now)-close).total_seconds() <= snapshot.get('limits', {}).get('max_age_seconds', 0)):
        result['codes'].append('stale_market_data')
    boundary = entry_boundary_reason(now, decision_at=close)
    if boundary:
        result['codes'].append(boundary)
    if not timing_matches(forecast, close, utc(now)):
        result['codes'].append('simulation_timing_mismatch')
    if result['codes']:
        result['status'] = 'blocked'
    return result


def broker_readiness(*, account, config, run, forecast, confirm_live):
    """Known static blockers only. Eligibility requires a fresh authoritative live preflight."""
    from .bot_execution_safety import live_execution_environment_enabled
    from .probabilistic_observation import requested
    codes = []
    if account.account_state != 'ACTIVE': codes.append('account_not_active')
    if account.can_trade is not True: codes.append('account_cannot_trade')
    if config.execution_mode != 'live': codes.append('live_execution_not_configured')
    if not live_execution_environment_enabled(): codes.append('live_execution_environment_disabled')
    if requested(): codes.append('observation_cannot_route')
    state = run.raw_state if run is not None and isinstance(run.raw_state, dict) else {}
    if not confirm_live or state.get('live_routing_confirmed') is not True:
        codes.append('live_order_confirmation_missing')
    if forecast.get('routing_allowed') is not True or forecast.get('payoff_semantics') == PAPER_PAYOFF:
        codes.append('artifact_not_authorized_for_routing')
    return dict(status='blocked' if codes else 'unknown', codes=codes,
                basis='Static readiness only; fresh provider preflight and all broker routing gates still required')
