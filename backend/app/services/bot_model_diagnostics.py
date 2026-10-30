"""Bounded, read-only dashboard diagnostics. Never fits, installs or routes a model."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

from .probabilistic_protocol import protocol
from .probabilistic_shadow import cache_root, scope_hash
from .probabilistic_strategy import ET, utc
from .topbot_session import entry_boundary_reason


def _stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Expected an explicit timezone')
    return utc(parsed)


def refresh_status(trained_through, now):
    trained = _stamp(trained_through)
    age = (now - trained).total_seconds() / 86400
    if age < 0:
        raise ValueError('Future training cutoff')
    limit = protocol()['refit']['max_age_days']
    due = trained + timedelta(days=limit)
    return dict(status='stale' if now > due else 'due_soon' if now >= due-timedelta(days=1) else 'fresh',
                trained_through=trained.isoformat(), due_at=due.isoformat(), age_days=round(age, 3),
                max_age_days=limit, seconds_until_due=(due-now).total_seconds(),
                automatic_refit=False, automatic_install=False,
                stale_behavior='Observation continues with a stale-training warning; this is not validated trading permission.')


def _session(now):
    close = datetime.fromtimestamp(int(now.timestamp() // 300)*300, timezone.utc)
    reason = entry_boundary_reason(now, decision_at=close)
    next_close = close if reason is None else None
    if next_close is None:
        candidate = close
        for _ in range(10*24*12):
            candidate += timedelta(minutes=5)
            if entry_boundary_reason(candidate, decision_at=candidate) is None:
                next_close = candidate
                break
    return dict(status='open' if reason is None else 'outside_entry_window', reason=reason,
                window='09:35–15:30 America/New_York, subject to exchange closures',
                next_eligible_close=next_close.isoformat() if next_close else None)


def _installed(owner, contract, records, root, now):
    from .probabilistic_observation import enabled, observation_path, load_model
    if not enabled():
        return dict(status='unavailable', reason='paper_observation_not_enabled')
    stream = next((value for row in records for value in (
        (row.get('forecast') or {}).get('data_live'), (row.get('simulation_eligibility') or {}).get('data_live'))
        if type(value) is bool), None)
    choices = [stream] if stream is not None else [False, True]
    paths = [(live, observation_path(owner, contract, live, root=root)) for live in choices]
    existing = [(live, path) for live, path in paths if path.is_file()]
    if len(existing) != 1:
        return dict(status='unavailable', reason='model_stream_ambiguous' if existing else 'model_missing')
    live, path = existing[0]
    with path.open('rb') as source:
        raw = source.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('Oversized model')
    artifact = json.loads(raw)
    if (artifact.get('owner_hash') != scope_hash(owner) or artifact.get('root_symbol') != 'MNQ'
            or artifact.get('roll_policy') != protocol()['roll_policy'] or artifact.get('data_live') is not live):
        raise ValueError('Artifact scope mismatch')
    model, metadata = load_model(artifact, as_of=now)
    return dict(status='available', artifact=metadata['artifact_sha256'], version=model.version,
                payoff_semantics=model.payoff_semantics, data_live=live, integrity_verified=True,
                created_at=artifact.get('created_at'), training_paths=len(model.samples),
                refresh=refresh_status(model.trained_through.isoformat(), now))


def _training_source(root, installed):
    """Metadata only; a later timestamp is not certification of complete training sessions."""
    from .databento_cache import DatabentoReplayStore
    try:
        bounds = DatabentoReplayStore(root, build_missing_timeframes=False).history_bounds('MNQ')
        if not bounds:
            return dict(status='unavailable', source='Databento filesystem cache')
        end = utc(bounds[1])
        cutoff = _stamp(installed['refresh']['trained_through'])
        return dict(status='newer_dates_available' if end.astimezone(ET).date() > cutoff.astimezone(ET).date() else 'no_newer_dates',
                    source='Databento filesystem cache', latest_bar_end=end.isoformat(),
                    complete_sessions_verified=False,
                    note='Cache coverage only. A refresh still needs complete-session validation; newer provider observations are not automatically training data.')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return dict(status='unavailable', source='Databento filesystem cache')


def summarize(records, installed, now):
    identified = [r for r in records if (r.get('forecast') or {}).get('artifact_sha256')]
    matching = [r for r in identified if installed.get('status') == 'available'
                and r['forecast'].get('artifact_sha256') == installed['artifact']
                and r['forecast'].get('payoff_semantics') == installed['payoff_semantics']
                and r['forecast'].get('data_live') is installed['data_live']]
    forecasts = [r for r in matching if r['forecast'].get('forecasts')]
    eligible = []
    invalid = 0
    for row in records:
        try:
            if entry_boundary_reason(_stamp(row['at']), decision_at=_stamp(row['close'])) is None:
                eligible.append(row)
        except (ValueError, TypeError, KeyError):
            invalid += 1
    eligible_ids = {id(r) for r in eligible}
    current = dict(bound_evaluations=len(matching), forecasts=len(forecasts),
                   session_eligible_evaluations=sum(id(r) in eligible_ids for r in matching),
                   qualified_signals=sum(r.get('stage') == 'qualified_signal' for r in matching),
                   first_forecast_at=min((r['at'] for r in forecasts), default=None),
                   latest_forecast_at=max((r['at'] for r in forecasts), default=None),
                   rejection_counts=dict(Counter(r.get('stage', 'unknown') for r in matching)))
    latest = records[0] if records else None
    explanation = None
    if latest:
        outside = latest.get('stage') == 'outside_entry_window'
        explanation = dict(at=latest.get('at'), close=latest.get('close'), stage=latest.get('stage'),
                           reason='Outside the permitted entry window; no numerical forecast is expected.' if outside else latest.get('reason'),
                           forecast_expected=not outside,
                           simulation_status=(latest.get('simulation_eligibility') or {}).get('status', 'unknown'),
                           simulation_codes=(latest.get('simulation_eligibility') or {}).get('codes', []),
                           broker_status=(latest.get('broker_readiness') or {}).get('status', 'unknown'),
                           broker_codes=(latest.get('broker_readiness') or {}).get('codes', []))
    edge = None
    if forecasts:
        row = forecasts[0]
        f = row['forecast']
        sides = {}
        for side in ('BUY', 'SELL'):
            s = f['forecasts'].get(side, {})
            keys = ('expected_net_usd', 'uncertainty_penalty_usd', 'lower_utility_usd', 'effective_days')
            if all(isinstance(s.get(k), (int, float)) and not isinstance(s[k], bool) and math.isfinite(s[k]) for k in keys):
                sides[side] = {k: s[k] for k in keys}
        if len(sides) == 2:
            edge = dict(at=row['at'], close=row['close'], sides=sides, minimum_net_edge_usd=protocol()['minimum_net_edge_usd'],
                        costs=f.get('costs'), basis='Expected net already includes trading costs. Entry score is expected net minus uncertainty; it must strictly exceed the threshold.')
    return dict(current_model=current, latest_evaluation=explanation, latest_current_forecast=edge,
                window_totals=dict(evaluations=len(records), session_eligible_evaluations=len(eligible), invalid_timestamps=invalid,
                                   other_model_evaluations=len(identified)-len(matching) if installed.get('status') == 'available' else None,
                                   unbound_evaluations=len(records)-len(identified)),
                attribution='Current-model counts require an exact artifact, payoff and data-stream match. Records without a model identity, including outside-hours HOLDs, remain unattributed. Creation time is not installation time.')


def report(owner, config, *, days=7, now=None, root=None):
    now = utc(now or datetime.now(timezone.utc))
    if (str(config.user_id) != str(owner) or config.execution_mode != 'dry_run'
            or config.strategy_type != 'topbot_adaptive'
            or (config.strategy_params or {}).get('revision') != 'mnq_bayesian_payoff_v1'):
        return dict(status='not_applicable')
    root = Path(root) if root is not None else cache_root()
    from .forward_results import location
    path = location(str(owner), config.account_id, config.id, root=root)
    records = []
    limited = False
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            rows = db.execute('SELECT payload FROM evaluations WHERE at>=? AND at<=? ORDER BY at DESC LIMIT 9001',
                              ((now-timedelta(days=min(30, max(1, days)))).isoformat(), now.isoformat())).fetchall()
            limited = len(rows) > 9000
            records = [json.loads(row[0]) for row in rows[:9000]]
    # Current-contract diagnostics are separate from the existing all-history account ledger.
    records = [r for r in records if r.get('contract') == config.contract_id]
    try:
        installed = _installed(str(owner), config.contract_id, records, root, now)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        installed = dict(status='unavailable', reason='model_integrity_or_scope_failed')
    if installed['status'] == 'available':
        installed['training_source'] = _training_source(root, installed)
    return dict(status='available', generated_at=now.isoformat(), scan_limited=limited, contract=config.contract_id,
                session=_session(now), installed_model=installed, **summarize(records, installed, now))
