from datetime import datetime, timedelta, timezone
import json
import sqlite3

from app.services.forward_evaluation import collection_coverage, expected_closes
from app.services import forward_results


def record(close, **extra):
    return dict(id='contract:'+close, close=close,
                at=(datetime.fromisoformat(close)+timedelta(seconds=20)).isoformat(),
                stage='outside_entry_window', forecast={}, **extra)


def coverage(records, *, now='2026-10-23T12:25:00+00:00', start='2026-10-01T00:00:00+00:00', limited=False):
    return collection_coverage(records, first_record=records[0] if records else None,
                               now=datetime.fromisoformat(now), window_start=datetime.fromisoformat(start),
                               scan_limited=limited)


def test_full_hold_session_is_coverage_not_forecasts_or_baselines():
    rows = [record('2026-10-22T13:20:00+00:00')]+[record(t) for t in expected_closes('2026-10-22')]
    c = coverage(rows)
    assert c['started_after_requested_window']
    assert c['collection_started_at'] == rows[0]['at']
    assert c['complete_evaluation_sessions'] == 1
    assert c['sessions'][0]['recorded'] == c['sessions'][0]['due'] == 72
    assert c['missing_due_closes'] == c['comparison_evaluations'] == 0


def test_missing_quality_and_truncated_evidence_cannot_claim_complete_session():
    rows = [record(t) for t in expected_closes('2026-10-22')]
    rows.pop(4)
    rows[5]['stage'] = 'stale_candles'
    c = coverage(rows)
    assert c['missing_due_closes'] == c['sessions'][0]['quality_failures'] == 1
    assert c['complete_evaluation_sessions'] == 0
    full = [record(t) for t in expected_closes('2026-10-22')]
    assert coverage(full, limited=True)['complete_evaluation_sessions'] == 0


def test_partial_window_grace_weekend_and_duplicate_records():
    rows = [record(t) for t in expected_closes('2026-10-22')[6:]]
    rows.append(rows[-1])
    c = coverage(rows)
    assert c['sessions'][0]['partial_window'] and not c['missing_due_closes']
    assert c['duplicate_closes'] == 1 and not c['complete_evaluation_sessions']
    recent = [record('2026-10-23T13:35:00+00:00')]
    c = coverage(recent, now='2026-10-23T13:40:30+00:00')
    assert c['sessions'][0]['due'] == 1 and c['sessions'][0]['unfinished']
    assert c['missing_due_closes'] == 0
    weekend = coverage([], now='2026-10-25T16:00:00+00:00')
    assert weekend['sessions'] == [] and weekend['first_recorded_at'] is None
    assert expected_closes('2026-10-24') == ()


def test_feature_rollout_gaps_are_not_backfilled():
    rows = [record(t) for t in expected_closes('2026-10-22')[:4]]
    rows[0]['forecast'] = {'forecasts': {'BUY': {}}}
    rows[1].update(stage='volatility_risk_cap')
    rows[2].update(stage='volatility_risk_cap', volatility_counterfactual={'status': 'observed'})
    rows[3].update(forecast={'forecasts': {'BUY': {}}}, evaluation_version=2, comparison_cohort='new')
    c = coverage(rows)
    assert c['legacy_forecasts_without_delayed_entry'] == c['volatility_without_scenario'] == 1
    assert c['first_scenario_at'] == rows[2]['at'] and c['comparison_evaluations'] == 1


def test_report_sections_share_snapshot_during_concurrent_worker_commit(tmp_path, monkeypatch):
    from app.services import forward_evaluation
    path = forward_results.location('a', 1, 32, tmp_path)
    row = record('2026-10-22T13:35:00+00:00', action='HOLD', status='held', reason='session',
                 risk_codes=[], paper_disposition='outside_entry_window', outcome_status='no_forecast')
    with forward_results.connect(path) as writer:
        writer.execute('INSERT INTO evaluations VALUES (?,?,?)', (row['id'], row['at'], json.dumps(row)))
        writer.execute('INSERT INTO account VALUES (1,?)', (json.dumps(forward_results.initial()),))
    def concurrent_commit(db, records, now, limited, start):
        with sqlite3.connect(path) as writer:
            writer.execute('INSERT INTO comparison_cohorts VALUES (?,?)', ('new', '{}'))
            writer.execute('UPDATE account SET payload=?', (json.dumps(dict(forward_results.initial(), net_usd=50)),))
        # A later section must still see the pre-commit snapshot.
        assert db.execute('SELECT count(*) FROM comparison_cohorts').fetchone()[0] == 0
        return {'cohorts': []}
    monkeypatch.setattr(forward_evaluation, 'comparison_report', concurrent_commit)
    result = forward_results.report('a',1,32,root=tmp_path, now=datetime(2026,10,22,14,tzinfo=timezone.utc))
    assert result['account']['net_usd'] == 0 and result['evaluations'] == 1
    assert result['coverage']['collection_started_at'] == row['at']
