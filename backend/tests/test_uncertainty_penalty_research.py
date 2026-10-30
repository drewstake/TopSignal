from copy import deepcopy
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from tools.research_uncertainty_penalty import (
    bootstrap_penalties, block_counts, calibrated_bounds, decision, fit_affine, prior_calibration,
)


def raw(mean=5., se=2., days=50.):
    return {side:dict(expected_net_usd=mean if side=='BUY' else -mean,
                     standard_error_usd=se,effective_days=days,uncertainty_penalty_usd=3*se+1)
            for side in ('BUY','SELL')}


def test_ablation_changes_deduction_only_and_never_routes():
    source=raw();before=deepcopy(source)
    control=decision(source,'registered',paths=400)
    reduced=decision(source,'one_se_diagnostic',paths=400)
    cost_only=decision(source,'cost_buffer_only_diagnostic',paths=400)
    zero=decision(source,'zero_penalty_diagnostic',paths=400)
    assert control['research_action']=='NO_TRADE'
    assert reduced['research_action']=='BUY'
    assert cost_only['entry_scores']['BUY']==4
    assert zero['entry_scores']['BUY']==5
    for result in (control,reduced,cost_only,zero):
        assert result['action']=='NO_TRADE' and result['routing_allowed'] is False
        assert result['adjusted_means']['BUY']==5
    assert source==before


@pytest.mark.parametrize('paths,days',[(299,50),(400,19.99)])
def test_reduced_penalties_preserve_minimum_evidence_gates(paths,days):
    assert decision(raw(days=days),'zero_penalty_diagnostic',paths=paths)['research_action']=='NO_TRADE'


def test_entry_threshold_is_strict():
    assert decision(raw(mean=1),'zero_penalty_diagnostic',paths=400)['research_action']=='NO_TRADE'


def test_joint_t95_deduction_between_one_se_and_registered():
    source=raw()
    score=decision(source,'two_action_t95',paths=400)['entry_scores']['BUY']
    assert source['BUY']['expected_net_usd']-source['BUY']['uncertainty_penalty_usd']<score<2


def test_day_blocks_preserve_observation_count_and_are_deterministic():
    counts=block_counts(21,5,repeats=30)
    assert counts.shape==(30,21)
    np.testing.assert_array_equal(counts.sum(axis=1),21)
    np.testing.assert_array_equal(counts,block_counts(21,5,repeats=30))


def test_bootstrap_preserves_weighted_mean_and_two_action_dependence():
    rng=np.random.default_rng(13)
    weights=rng.uniform(.01,1,(30,4));weights/=weights.sum(axis=0)
    means=np.array([2.,-2.,7.,-7.])
    # Exact constant day payoffs have zero estimated mean error, despite weights.
    joint,daily,_=bootstrap_penalties(weights*means,weights,means,np.ones(4),repeats=100)
    np.testing.assert_allclose(joint,np.ones(4),atol=1e-12)
    np.testing.assert_allclose(daily,np.ones(4),atol=1e-12)
    returns=rng.normal(size=(30,2))
    returns=np.column_stack([returns[:,0],-returns[:,0],returns[:,1],-returns[:,1]])
    weights=np.full((30,4),1/30)
    joint,daily,_=bootstrap_penalties(weights*returns,weights,returns.mean(axis=0),np.full(4,.2),repeats=300)
    assert np.all(daily>=joint)
    np.testing.assert_array_equal(joint[::2],joint[1::2])


def test_bootstrap_rejects_zero_se_instead_of_silently_lowering_penalty():
    with pytest.raises(ValueError):
        bootstrap_penalties(np.ones((20,2)),np.ones((20,2)),np.ones(2),np.zeros(2))


def test_future_query_features_cannot_change_an_earlier_penalty():
    rng=np.random.default_rng(17)
    values=rng.normal(size=(30,6))
    weights=np.full((30,6),1/30)
    means=values.mean(axis=0)
    early=bootstrap_penalties(weights[:,:2]*values[:,:2],weights[:,:2],means[:2],np.ones(2),repeats=200)
    full=bootstrap_penalties(weights*values,weights,means,np.ones(6),repeats=200)
    np.testing.assert_allclose(early[0],full[0][:2],rtol=0,atol=1e-12)
    np.testing.assert_allclose(early[1],full[1][:2],rtol=0,atol=1e-12)


def calibration_rows():
    rows=[]
    for i in range(24):
        day=(date(2026,1,1)+timedelta(days=i)).isoformat()
        for side in ('BUY','SELL'):
            for x in (-.5,0.,.5):
                rows.append(dict(day=day,side=side,lattice=True,risk=100.,mean=x*100,actual=(.1+.5*x)*100))
    return rows


def test_calibration_never_uses_same_day_future_or_off_lattice_outcomes():
    rows=calibration_rows()
    fit=prior_calibration(rows,'2026-01-25',repeats=50)
    polluted=rows+[dict(rows[0],day='2026-01-25',actual=1e10),dict(rows[0],day='2026-01-26',actual=-1e10),dict(rows[0],lattice=False,actual=1e10)]
    other=prior_calibration(polluted,'2026-01-25',repeats=50)
    assert fit['last_day']=='2026-01-24' and fit['days']==24
    for side in ('BUY','SELL'):
        assert fit['sides'][side]['slope']==pytest.approx(.5)
        assert fit['sides'][side]['intercept']==pytest.approx(.1)
        for length in ('5','10'):
            np.testing.assert_array_equal(fit['sides'][side]['bootstrap'][length],other['sides'][side]['bootstrap'][length])
    means,bounds=calibrated_bounds(fit,raw(mean=5,se=2),100)
    assert means['BUY']==pytest.approx(12.5)
    assert bounds['BUY']<means['BUY']-1


def test_calibration_requires_twenty_completed_days():
    with pytest.raises(ValueError):prior_calibration(calibration_rows(),'2026-01-20',repeats=5)


def test_calibration_slope_is_constrained():
    _,positive=fit_affine([0,0,1,2])
    _,negative=fit_affine([0,0,1,-2])
    assert positive==1 and negative==0


def test_research_offline_guard_blocks_network_and_live_environment():
    backend=Path(__file__).resolve().parents[1]
    script="""
import json,os,socket
from tools.research_probabilistic_topbot import offline_guard
offline_guard()
blocked=False
try: socket.create_connection(('203.0.113.1',443),timeout=.1)
except RuntimeError: blocked=True
print(json.dumps(dict(blocked=blocked,live=os.getenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED'),worker_live=os.getenv('TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION'),database=os.getenv('DATABASE_URL'),dotenv=os.getenv('PYTHON_DOTENV_DISABLED'))))
"""
    env=dict(os.environ,TOPSIGNAL_LIVE_EXECUTION_ENABLED='true',TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION='true',DATABASE_URL='postgresql://should-never-connect')
    out=subprocess.run([sys.executable,'-B','-c',script],cwd=backend,env=env,capture_output=True,text=True,check=True,timeout=15)
    assert json.loads(out.stdout)==dict(blocked=True,live='false',worker_live='false',database='sqlite+pysqlite:///:memory:',dotenv='1')
