"""Rejected what-if paths are evidence only, never trading or model-validation inputs."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services import forward_results as ledger
from app.services import topbot_mathematical as strategy
from app.services.probabilistic_shadow import explain_shadow
from app.services.probabilistic_strategy import BAR, Costs
from test_forward_results import NOW, CONTRACT, candle, ingest, dry


def volatile_rows():
    close = NOW.replace(second=0)
    return [SimpleNamespace(unit='minute', unit_number=5, candle_timestamp=close-(21-i)*BAR,
                            open_price=1000.+20*i, high_price=1001.+20*i, low_price=999.+20*i,
                            close_price=1000.+20*i, volume=100., user_id='a', contract_id=CONTRACT,
                            live=False, is_partial=False) for i in range(21)]


def blocked(root):
    rows = volatile_rows()
    forecast = explain_shadow(candles=rows, owner='a', contract_id=CONTRACT, as_of=NOW, root=root)
    assert forecast['reason_code'] == 'volatility_above_risk_cap'
    assert forecast['forecasts'] is None and forecast['routing_allowed'] is False
    return dict(candles=rows, forecast=forecast, action='HOLD', status='held', reason=forecast['reasons'][0])


def test_volatility_hold_collects_future_only_paths_without_account_or_calibration(tmp_path, monkeypatch):
    args = blocked(tmp_path)
    monkeypatch.setattr('app.services.topbot_session.entry_boundary_reason', lambda *a, **kw: None)
    decision = strategy.evaluate(args['candles'], as_of=NOW, root=tmp_path)
    assert decision.action == 'HOLD' and decision.raw_payload['live_routing_allowed'] is False
    with pytest.raises(ValueError): strategy.require_live_worker()
    for _ in range(2): ingest(tmp_path, **args)
    result = ledger.report('a', 1, 32, root=tmp_path, now=NOW)
    section = result['volatility_counterfactuals']
    assert section['recorded'] == section['pending'] == 1
    scenario = section['recent'][0]
    assert scenario['stop_points'] == 34.75 and scenario['risk_cap_points'] == 25
    assert scenario['entry_at'].endswith('14:05:00+00:00')
    assert scenario['deadline'].endswith('14:15:00+00:00')
    assert scenario['outcomes'] is None
    assert result['account']['position'] is None and result['comparison']['cohorts'] == []
    # There is deliberately no candle-open 14:00 entry. Both brackets touch in the
    # first usable bar; the conservative stop-first result must win for both sides.
    future = [candle(5, low=1, high=200), candle(10)]
    ingest(tmp_path, now=NOW+timedelta(minutes=10), candles=future, forecast={}, action='HOLD')
    assert ledger.report('a',1,32,root=tmp_path,now=NOW)['volatility_counterfactuals']['pending'] == 1
    ingest(tmp_path, now=NOW+timedelta(minutes=15), candles=future, forecast={}, action='HOLD')
    result = ledger.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=15))
    section = result['volatility_counterfactuals']
    assert section['completed'] == 1 and section['pending'] == 0
    expected = -(2*34.75+2*Costs().adverse_points_per_side+2*Costs().commission_per_side)
    for side in ('BUY','SELL'):
        outcome = section['recent'][0]['outcomes'][side]
        assert outcome['net_usd'] == pytest.approx(expected)
        assert outcome['exit_kind'] == 'stop'
    assert result['forecasts'] == result['completed'] == result['calibration']['BUY']['count'] == 0
    assert result['account']['net_usd'] == result['account']['trades'] == 0
    assert result['comparison']['cohorts'] == []
    # Restarts/retries/revised later candles cannot overwrite a settled outcome.
    ingest(tmp_path, now=NOW+timedelta(minutes=20), candles=[candle(5),candle(10)], forecast={}, action='HOLD')
    again = ledger.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=20))
    assert again['volatility_counterfactuals']['recent'] == section['recent']
    assert ledger.report('other',1,32,root=tmp_path,now=NOW)['volatility_counterfactuals'] is None


@pytest.mark.parametrize('fault', ['missing', 'subscription', 'roll'])
def test_missing_paths_remain_unknown_not_zero(tmp_path, fault):
    ingest(tmp_path, **blocked(tmp_path))
    future = [candle(5), candle(10)]
    values = {}
    if fault == 'missing': future = [candle(10)]
    elif fault == 'subscription':
        for row in future: row.live = True
    else:
        values['contract'] = 'CON.F.US.MNQ.H27'
        for row in future: row.contract_id = values['contract']
    ingest(tmp_path, now=NOW+timedelta(hours=3), candles=future, forecast={}, action='HOLD', **values)
    result = ledger.report('a',1,32,root=tmp_path,now=NOW+timedelta(hours=3))
    section = result['volatility_counterfactuals']
    assert section['unknown'] == 1 and section['completed'] == 0
    assert section['recent'][0]['outcomes'] is None
    assert result['account']['trades'] == 0


@pytest.mark.parametrize('fault', ['stale', 'not_held', 'not_hold', 'invalid_input', 'not_volatile'])
def test_only_fresh_valid_new_volatility_holds_start_scenarios(tmp_path, fault):
    args = blocked(tmp_path)
    now = NOW
    if fault == 'stale': now += timedelta(minutes=3)
    elif fault == 'not_held': args['status'] = 'duplicate_skipped'
    elif fault == 'not_hold': args['action'] = 'BUY'
    elif fault == 'invalid_input': args['candles'].pop(8)
    else:
        for i,row in enumerate(args['candles']):
            row.open_price-=i*20; row.high_price-=i*20; row.low_price-=i*20; row.close_price-=i*20
    ingest(tmp_path, now=now, **args)
    result = ledger.report('a',1,32,root=tmp_path,now=now)
    assert result['volatility_counterfactuals']['recorded'] == 0
    assert result['account']['position'] is None


def test_no_retrospective_counterfactuals_for_old_rejections(tmp_path):
    args = blocked(tmp_path)
    ingest(tmp_path, **args)
    import json
    with ledger.connect(ledger.location('a',1,32,tmp_path)) as db, db:
        ident, raw = db.execute('SELECT id,payload FROM evaluations').fetchone()
        record = json.loads(raw); record.pop('volatility_counterfactual')
        db.execute('UPDATE evaluations SET payload=? WHERE id=?',(json.dumps(record),ident))
    ingest(tmp_path, **args)
    assert ledger.report('a',1,32,root=tmp_path,now=NOW)['volatility_counterfactuals']['recorded'] == 0
