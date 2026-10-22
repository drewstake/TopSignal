"""Read-only TopBot collection and order-safety check. Never routes orders.

Run explicitly against the hosting task's current release. A fresh risk HOLD is
collection evidence, not a forecast, trade, calibration sample or validation.
"""
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import argparse
import json
import math
import os
import sys
import time
import sqlite3
from urllib.request import urlopen

EASTERN = ZoneInfo('America/New_York')


def stamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Missing timestamp timezone')
    return result.astimezone(timezone.utc)


def summarize_observations(records, now, target_date, contract):
    unique = {}
    duplicates = 0
    invalid = 0
    conflicts = 0
    for row in records:
        try:
            if stamp(row['decision_close']).astimezone(EASTERN).date().isoformat() != target_date:
                continue
            if row['contract_id'] != contract:
                continue
            key = (row['event'], row['observation_id'])
            if key in unique:
                duplicates += 1
                # Retry timestamps can differ; forecast/outcome data must agree.
                old = {k: v for k, v in unique[key].items() if k != 'recorded_at'}
                new = {k: v for k, v in row.items() if k != 'recorded_at'}
                conflicts += old != new
            unique[key] = row
        except (KeyError, ValueError, TypeError):
            invalid += 1
    forecasts = {k[1]: v for k, v in unique.items() if k[0] == 'observation_forecast'}
    outcomes = {k[1]: v for k, v in unique.items() if k[0] == 'observation_outcome'}
    observed = {k: v for k, v in outcomes.items() if v.get('outcome_status') == 'observed' and k in forecasts}
    unknown = [k for k, v in outcomes.items() if v.get('outcome_status') != 'observed']
    missing = [k for k, v in forecasts.items() if stamp(v['decision_close']) + timedelta(minutes=17) <= now and k not in outcomes]
    bad = []
    for key, row in forecasts.items():
        try:
            if row['validation_status'] != 'unvalidated' or row['execution_mode'] != 'dry_run' or row['broker_orders'] is not False:
                raise ValueError('Unexpected mode')
            if row.get('training_stale') is not True or not row.get('artifact_sha256'):
                raise ValueError('Missing model disclosure')
            if stamp(row['decision_close']) > now or stamp(row['recorded_at']) > now:
                raise ValueError('Future record')
            for side in ('BUY', 'SELL'):
                d = row['forecasts'][side]
                probabilities = [d[k] for k in ('probability_net_positive', 'probability_net_nonpositive', 'probability_stop', 'probability_time_exit', 'probability_target')]
                if not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in probabilities):
                    raise ValueError('Invalid probability')
                if abs(sum(probabilities[:2]) - 1) > 1e-6 or abs(sum(probabilities[2:]) - 1) > 1e-6:
                    raise ValueError('Invalid distribution')
                if not math.isfinite(d['expected_net_usd']):
                    raise ValueError('Invalid expectation')
        except (KeyError, ValueError, TypeError):
            bad.append(key)
    bad_outcomes = []
    for key, row in observed.items():
        try:
            if stamp(row['recorded_at']) < stamp(row['decision_close']) + timedelta(minutes=15):
                raise ValueError('Premature outcome')
            if row.get('execution_mode') != 'dry_run' or row.get('broker_orders') is not False or row.get('validation_status') != 'unvalidated':
                raise ValueError('Unexpected outcome mode')
            for side in ('BUY', 'SELL'):
                if not math.isfinite(row['outcomes'][side]['hypothetical_net_usd']):
                    raise ValueError('Invalid outcome')
        except (KeyError, ValueError, TypeError):
            bad_outcomes.append(key)
    latest = max((stamp(v['decision_close']) for v in forecasts.values()), default=None)
    return {'forecasts': len(forecasts), 'matched_observed_outcomes': len(observed),
            'unknown_outcomes': len(unknown), 'overdue_missing_outcomes': missing,
            'pending_not_overdue': sum(k not in outcomes and k not in missing for k in forecasts),
            'orphan_outcomes': len(set(outcomes) - set(forecasts)), 'duplicates': duplicates,
            'conflicting_duplicates': conflicts, 'invalid_records': invalid,
            'invalid_forecasts': bad, 'invalid_outcomes': bad_outcomes,
            'latest_forecast_close': latest, 'latest_forecast_age_seconds': (now - latest).total_seconds() if latest else None,
            'recorded_forecast_closes': sorted({stamp(v['decision_close']) for v in forecasts.values()}),
            'note': 'Hypothetical 15-minute outcomes, never broker fills or portfolio P&L.'}


def collection_health(records, observations, *, now, contract, run_id, boundary_reason):
    """Require two recent completed evaluations; only explicit expected gates excuse no forecast.

    Allow the existing 120-second candle/evaluation delivery budget. A HOLD
    label, what-if outcome, old record or healthy process alone is insufficient.
    This function does not infer model integrity: the checker tests it separately.
    """
    now = stamp(now)
    due = datetime.fromtimestamp(int((now.timestamp() - 120) // 300) * 300, timezone.utc)
    expected = {due - timedelta(minutes=5), due}
    by_close, issues = {}, []
    logged = {stamp(t) for t in observations['recorded_forecast_closes']}
    for row in records:
        try:
            if row['contract'] != contract or row['run'] != run_id:
                continue
            close, at = stamp(row['close']), stamp(row['at'])
            if close < min(expected):
                continue
            if (close > now or at > now or not 0 <= (at-close).total_seconds() <= 120
                    or close.timestamp() % 300 != 0):
                raise ValueError('Invalid evaluation timestamps')
            if close in by_close:
                raise ValueError('Duplicate evaluation')
            f = row['forecast']
            if not isinstance(f, dict):
                raise ValueError('Invalid forecast metadata')
            state = 'unexplained_no_forecast'
            if f.get('forecasts'):
                if f.get('data_status') != 'fresh' or stamp(f['candle_close_timestamp']) != close or close not in logged:
                    raise ValueError('Forecast absent from observation log or not fresh/aligned')
                state = 'collecting_forecasts'
            elif (row['action'] == 'HOLD' and row['status'] == 'held'
                    and row.get('outcome_status') == 'no_forecast'):
                if (row['stage'] == 'volatility_risk_cap'
                        and f.get('reason_code') == 'volatility_above_risk_cap'
                        and f.get('data_status') == 'fresh'
                        and stamp(f['candle_close_timestamp']) == close):
                    state = 'expected_risk_hold'
                elif (row['stage'] == 'outside_entry_window' and boundary_reason(at)
                      and boundary_reason(at) in row.get('reason', '')):
                    state = 'expected_session_hold'
            by_close[close] = {'close': close, 'at': at, 'state': state, 'stage': row['stage']}
            if state == 'unexplained_no_forecast':
                issues.append('Recent evaluation has unexplained missing forecasts: ' + close.isoformat())
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            issues.append('Recent collection record is invalid, duplicated, late, or missing its forecast log')
    missing = sorted(expected - by_close.keys())
    if missing:
        issues.append('Recent completed evaluations are missing or stale')
    latest = by_close[max(by_close)] if by_close else None
    state = 'attention' if issues else latest['state']
    messages = {
        'expected_risk_hold': 'Fresh evaluations continue; the unchanged volatility cap blocks numerical forecasts before model inference.',
        'expected_session_hold': 'Fresh evaluations continue outside the permitted entry window; no numerical forecast is expected.',
        'collecting_forecasts': 'Fresh numerical forecasts are present in the evaluation ledger and observation log.',
        'attention': 'Collection evidence is stale, missing, invalid, or does not explain the absence of forecasts.',
    }
    return {'status': state, 'healthy': not issues, 'message': messages[state],
            'latest_evaluation': latest, 'checked_evaluations': sorted(by_close.values(), key=lambda x: x['close']),
            'missing_expected_closes': missing, 'issues': issues,
            'latest_forecast_close': observations['latest_forecast_close'],
            'latest_forecast_age_seconds': observations['latest_forecast_age_seconds'],
            'forecasts': observations['forecasts'], 'matched_observed_outcomes': observations['matched_observed_outcomes'],
            'note': 'Risk HOLDs and what-if paths do not increment forecast, calibration, trade or validation counts.'}


def broker_is_readonly(method, path):
    return method.upper() == 'POST' and path in {
        '/api/Auth/loginKey', '/api/Order/search', '/api/Order/searchOpen', '/api/Trade/search'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-snapshot', required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--deployed-at', type=stamp, required=True)
    parser.add_argument('--config-id', type=int, required=True)
    parser.add_argument('--phase', choices=('preflight', 'market-check'), default='market-check')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    RELEASE, REPORTS, DEPLOYED = args.release, args.runtime_root / 'checks', args.deployed_at
    phase = args.phase
    report = {'started_at': now, 'release': str(RELEASE), 'phase': phase, 'host': os.environ.get('COMPUTERNAME'),
              'checks': {}, 'issues': [], 'limitations': ['Broker history is limited to the configured TopBot account and provider responses.',
              'No real-time alert channel is configured; report is saved on peteamdpc for later SSH retrieval.']}
    def check(name, fn):
        try:
            report['checks'][name] = fn()
        except Exception as exc:
            # Do not serialize exception messages: upstream errors can contain secrets.
            report['checks'][name] = {'error_type': type(exc).__name__}
            report['issues'].append(name + ' could not be verified (' + type(exc).__name__ + ')')
    def issue(condition, message):
        if not condition:
            report['issues'].append(message)
    REPORTS.mkdir(parents=True, exist_ok=True)
    from dotenv import load_dotenv
    load_dotenv(RELEASE / 'backend/.env', override=True)
    sys.path.insert(0, str(RELEASE / 'backend'))
    from app.db import SessionLocal
    from sqlalchemy import text
    def rows(query, params=None):
        with SessionLocal() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            db.execute(text("SET LOCAL statement_timeout='20000'"))
            return [dict(row) for row in db.execute(text(query), params or {}).mappings()]
    def configuration():
        from app.services.probabilistic_observation import enabled
        from app.services.bot_execution_safety import live_execution_environment_enabled
        from app.services.topbot_mathematical import require_live_worker
        flags = {k: os.getenv(k) for k in ('TOPSIGNAL_UNVALIDATED_DRY_RUN', 'TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION')}
        safe = enabled() and not live_execution_environment_enabled()
        try:
            require_live_worker()
            rejected = False
        except ValueError:
            rejected = True
        issue(safe and rejected, 'Live-order guard verification failed')
        return {'flags': flags, 'central_live_gate_disabled': safe, 'live_entry_rejected': rejected,
                'scope': 'Deployment dotenv and code guards in checker; running-process evidence is evaluated separately.'}
    check('configuration', configuration)
    def runtime():
        snapshot = json.loads(Path(args.runtime_snapshot).read_text(encoding='utf-8-sig'))
        issue(snapshot.get('state') == 'Running', 'Hosting task is not running')
        issue(snapshot.get('release_matches') is True, 'Hosting task release differs from expected release')
        process = snapshot.get('backend_process') or {}
        issue(bool(process) and process.get('expected_command') is True, 'Backend process could not be verified against current release')
        if process:
            issue(stamp(snapshot['env_modified_at']) <= stamp(process['started_at']), 'Environment file changed since backend startup; effective runtime settings need review')
        endpoints = {}
        for path in ('/health', '/health/worker?require_enabled=true', '/ready'):
            with urlopen('http://127.0.0.1:8000' + path, timeout=15) as response:
                endpoints[path] = json.load(response)
        issue(endpoints['/health/worker?require_enabled=true'].get('healthy') is True, 'Worker endpoint is not healthy')
        return {'task': snapshot, 'endpoints': endpoints}
    check('runtime', runtime)
    configs = rows('select id,user_id,account_id,name,enabled,execution_mode,strategy_type,strategy_params,contract_id from bot_configs where id=:id', {'id': args.config_id})
    cfg = configs[0]
    issue(cfg['enabled'] and cfg['execution_mode'] == 'dry_run' and cfg['strategy_type'] == 'topbot_adaptive' and cfg['strategy_params'].get('revision') == 'mnq_bayesian_payoff_v1', 'TopBot configuration is not the expected mathematical dry-run')
    active_configs = rows('select id from bot_configs where user_id=:owner and enabled=true', {'owner': cfg['user_id']})
    issue([r['id'] for r in active_configs] == [cfg['id']], 'Expected exactly one enabled TopBot configuration')
    def model():
        from app.services.probabilistic_observation import observation_path, load_model
        artifact = json.loads(observation_path(str(cfg['user_id']), cfg['contract_id'], False).read_text())
        fitted, metadata = load_model(artifact, as_of=now)
        issue(artifact['routing_allowed'] is False and artifact['validation_status'] == 'unvalidated', 'Artifact routing/validation status changed')
        if metadata.get('training_stale'):
            report['limitations'].append('The fitted model training data is old; it remains unvalidated and dry-run only. Expected HOLDs do not cure training staleness.')
        return {'samples': len(fitted.samples), 'validation_status': artifact['validation_status'], 'routing_allowed': artifact['routing_allowed'], **metadata}
    check('model', model)
    def worker():
        query = "select id,bot_config_id,status,dry_run,last_heartbeat_at,last_evaluated_at,last_error,raw_state from bot_runs where status='running' and user_id=:owner"
        first = rows(query, {'owner': cfg['user_id']})
        time.sleep(12)
        second = rows(query, {'owner': cfg['user_id']})
        good = len(second) == 1 and second[0]['bot_config_id'] == cfg['id'] and second[0]['dry_run'] is True
        issue(good, 'Expected exactly one active mathematical dry-run for this owner')
        if good:
            r = second[0]
            state = r['raw_state'] or {}
            issue(state.get('continuous') is True and state.get('execution_mode') == 'dry_run' and state.get('live_routing_confirmed') is False, 'Active run has unexpected execution settings')
            previous = next((x for x in first if x['id'] == r['id']), {})
            advanced = bool(r['last_heartbeat_at'] and previous.get('last_heartbeat_at') and r['last_heartbeat_at'] > previous['last_heartbeat_at'])
            issue(advanced, 'Run heartbeat did not advance during the sample')
            if phase == 'market-check':
                candle = state.get('last_closed_candle_at')
                issue(candle and -60 <= (datetime.now(timezone.utc) - stamp(candle)).total_seconds() <= 720, 'Worker candle is absent or stale')
        leases = rows("select * from bot_runtime_leases where lease_name='recurring-bot-evaluator-v1'")
        issue(len(leases) == 1 and leases[0]['expires_at'] > datetime.now(timezone.utc), 'Worker lease is missing or expired')
        if leases:
            issue(leases[0]['details'].get('provider_status') == 'ok', 'Worker provider is not healthy')
            backend = report['checks'].get('runtime', {}).get('task', {}).get('backend_process', {})
            issue(str(leases[0]['owner_id']).split(':')[1:2] == [str(backend.get('pid'))], 'Lease owner does not match the local backend PID')
        return {'runs': second, 'leases': leases}
    check('worker', worker)
    evaluation_records = []
    def observations():
        # Forecast logging precedes the committed ledger write. Snapshot the
        # ledger first so an evaluation completing during this check cannot
        # falsely appear to be missing from an earlier observation-log snapshot.
        from app.services.forward_results import location
        path = location(str(cfg['user_id']), cfg['account_id'], cfg['id'])
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            evaluation_records.extend(json.loads(row[0]) for row in db.execute('SELECT payload FROM evaluations ORDER BY at DESC LIMIT 12'))
        records = []
        parse_errors = 0
        paths = list((args.runtime_root / 'logs').glob('research-observations.jsonl*'))
        for path in paths:
            with path.open(encoding='utf-8') as source:
                for line in source:
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        parse_errors += 1
        summary = summarize_observations(records, datetime.now(timezone.utc), now.astimezone(EASTERN).date().isoformat(), cfg['contract_id'])
        summary.update(log_files=len(paths), json_parse_errors=parse_errors)
        if phase == 'market-check':
            issue(not any(summary[k] for k in ('unknown_outcomes', 'overdue_missing_outcomes', 'invalid_forecasts', 'invalid_outcomes', 'conflicting_duplicates', 'invalid_records', 'json_parse_errors', 'orphan_outcomes')), 'Observation records contain missing, unknown, inconsistent or invalid data')
        return summary
    check('observations', observations)
    def collection():
        from app.services.topbot_session import entry_boundary_reason
        active = report['checks']['worker']['runs']
        if len(active) != 1:
            raise ValueError('Active run not verified')
        result = collection_health(evaluation_records, report['checks']['observations'], now=datetime.now(timezone.utc),
                                   contract=cfg['contract_id'], run_id=active[0]['id'], boundary_reason=entry_boundary_reason)
        if phase == 'market-check':
            for message in result['issues']:
                issue(False, message)
        return result
    check('collection', collection)
    def attempts():
        result = rows("select execution_mode,status,count(*) count, count(provider_order_id) provider_ids from bot_order_attempts where user_id=:owner and created_at>=:start group by execution_mode,status", {'owner': cfg['user_id'], 'start': DEPLOYED})
        issue(not any(r['execution_mode'] != 'dry_run' or r['status'] in ('submitted','submission_unknown') or r['provider_ids'] for r in result), 'Live, submitted, unknown or broker-linked order attempts found since deployment')
        return {'since': DEPLOYED, 'groups': result}
    check('application_orders', attempts)
    def broker():
        from app.services.projectx_credentials import get_projectx_credentials
        from app.services.projectx_client import ProjectXClient
        class ReadOnlyClient(ProjectXClient):
            def _request_once(self, method, path, **kwargs):
                if not broker_is_readonly(method, path):
                    raise RuntimeError('Checker forbids broker mutation endpoints')
                return super()._request_once(method, path, **kwargs)
        with SessionLocal() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            creds = get_projectx_credentials(db, user_id=str(cfg['user_id']))
        if creds is None:
            raise RuntimeError('Credentials unavailable')
        base = next((os.getenv(k) for k in ('PROJECTX_API_BASE_URL','PROJECTX_BASE_URL','PROJECTX_GATEWAY_URL','TOPSTEP_API_BASE_URL','TOPSTEPX_API_BASE_URL') if os.getenv(k)), '')
        client = ReadOnlyClient(base_url=base, username=creds.username, api_key=creds.api_key, timeout_seconds=15)
        end = datetime.now(timezone.utc)
        orders = client.search_orders(account_id=cfg['account_id'], start=DEPLOYED, end=end)
        opened = client.search_open_orders(account_id=cfg['account_id'])
        trades = client.fetch_trade_history(cfg['account_id'], DEPLOYED, end, require_valid_collection=True)
        tagged = [r for r in orders if (r.get('custom_tag') or '').lower().startswith('topsignal-')]
        tagged_open = [r for r in opened if (r.get('custom_tag') or '').lower().startswith('topsignal-')]
        ids = {r['order_id'] for r in tagged + tagged_open}
        matched = [t for t in trades if t['order_id'] in ids]
        issue(not tagged and not tagged_open and not matched, 'TopSignal-tagged orders/fills found in broker history or working orders')
        if orders or trades or opened:
            report['limitations'].append('Other broker activity exists; untagged activity cannot be conclusively attributed or excluded as TopBot activity.')
            issue(not any(not r.get('custom_tag') for r in orders + opened) and not any(t['order_id'] not in ids for t in trades), 'Broker activity includes untagged/unattributed orders or fills requiring review')
        return {'account_id': cfg['account_id'], 'since': DEPLOYED, 'until': end,
                'orders_returned': len(orders), 'open_orders_returned': len(opened), 'fills_returned': len(trades),
                'topsignal_tagged_order_ids': sorted(ids), 'matched_fill_count': len(matched),
                'conclusion': 'No TopSignal-tagged broker activity returned' if not ids and not matched else 'Review required',
                'api_policy': 'Auth/loginKey and order/trade search only; all other endpoints rejected.'}
    check('broker_history', broker)
    report['completed_at'] = datetime.now(timezone.utc)
    report['status'] = 'ATTENTION' if report['issues'] else ('PREFLIGHT_OK' if phase == 'preflight' else 'PASS')
    name = now.astimezone(EASTERN).date().isoformat() + '-' + phase
    destination = REPORTS / (name + '.json')
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    temporary.replace(destination)
    obs = report['checks'].get('observations', {})
    summary = '\n'.join([f"TopBot {phase}: {report['status']}", f"Completed: {report['completed_at'].astimezone(EASTERN).isoformat()}",
                         f"Forecasts: {obs.get('forecasts', 'unknown')}; completed hypothetical outcomes: {obs.get('matched_observed_outcomes', 'unknown')}",
                         'Collection: ' + report['checks'].get('collection', {}).get('message', 'Could not verify'),
                         f"Latest numerical forecast: {obs.get('latest_forecast_close')}; age seconds: {obs.get('latest_forecast_age_seconds')}",
                         *['ISSUE: ' + i for i in report['issues']], *['LIMIT: ' + i for i in report['limitations']],
                         f"Details: {destination}", ''])
    destination.with_suffix('.txt').write_text(summary, encoding='utf-8')
    print(summary)
    return 2 if report['issues'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
