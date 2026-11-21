from datetime import UTC, datetime, timedelta
import gzip
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from app.services.imbalance_study import EpisodeMatches, Observations, open_store
from app.services.recording_health import StudyWatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from track_imbalances import independent_boundary
from weekly_rebound import measure, closed_outcome

CONTRACT = 'CON.F.US.MNQ.Z26'
WEEK = Path(__file__).resolve().parents[1]/'storage/databento/trade-review-v1/week-20261120'


def sample(watch, now, raw, processed, contract=CONTRACT):
    raw = dict(state='recording', contract_id=contract, at=now.isoformat(),
               recording={'last_depth_at':raw.isoformat()})
    return watch.check(('owner',29593203), CONTRACT, raw, dict(contract=CONTRACT, through=processed.timestamp()), now)


def test_quiet_trading_switch_stall_and_recovery():
    watch = StudyWatch()
    now = datetime(2026,11,20,0,6,24,tzinfo=UTC)
    for seconds in range(0, 361, 15):
        at = now + timedelta(seconds=seconds)
        assert sample(watch, at, at, at)['state'] == 'processing'
    old = at
    for seconds in (20,40):
        at = old + timedelta(seconds=seconds)
        assert sample(watch, at, at, old)['state'] == 'processing'
    for seconds in range(55, 300, 15):
        at = old + timedelta(seconds=seconds)
        assert sample(watch, at, at, old)['state'] != 'study_stalled'
    at = old + timedelta(minutes=5)
    assert sample(watch, at, at, old)['state'] == 'study_stalled'
    assert sample(watch, at, at, at)['state'] == 'processing'
    assert sample(watch, at, old, old)['state'] == 'raw_feed_stale'
    assert sample(watch, at, at, old, 'CON.F.US.MNQ.H27')['state'] == 'raw_feed_stale'
    closed = datetime(2026,11,27,19,tzinfo=UTC)
    assert sample(watch, closed, old, old)['state'] == 'scheduled_closure'


def test_cleanup_retries_after_two_writers_contend(tmp_path):
    db = open_store(tmp_path/'study')
    other = open_store(tmp_path/'study')
    db.execute('PRAGMA busy_timeout=10')
    obs = Observations(db, 'Independent','old',CONTRACT,0)
    obs.observe(100, {'ready':True, 'bid':100,'ask':101,'imbalance':.2},
                [{'kind':'episode_qualified','episode_at':'old','direction':1}],.1,30)
    db.commit()
    other.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.OperationalError):
        independent_boundary(db, obs)
    other.rollback()
    independent_boundary(db, obs)
    assert db.execute('SELECT COUNT(*) FROM marks WHERE bid IS NULL').fetchone()[0] == 3
    other.close()
    db.close()


def test_scoring_as_of_qualification_and_missing_data():
    quotes = {t:(100,101) for t in range(100,131)}
    trades = [(120,'trade',1,3),(131,'trade',0,9)]
    result = measure(130,quotes,[],trades)
    assert result['condition'] == 'flagged' and result['midpoint_change_60s'] is None
    assert measure(130,quotes,[],trades+[(125,'trade',0,3)])['condition'] == 'unflagged'
    for bad in ([(126,'gap',None,None)], [(125,'trade',3,1)]):
        assert measure(130,quotes,[],trades+bad)['condition'] == 'unknown'
    assert measure(130,quotes,[(125,126)],trades)['condition'] == 'unknown'
    assert measure(130,quotes,[],trades,False)['condition'] == 'unknown'
    assert measure(130,{t:q for t,q in quotes.items() if not 119<t<125},[],trades)['condition']=='unknown'


def test_conflicting_matches_and_profitable_nontarget():
    a = dict(user_id='o',account_id=1,bot_config_id=40,id=10,contract_id=CONTRACT,
             provider_order_id='o1',status='submitted',capture='c',episode='e',at=101,
             version='v',direction='following',side='BUY',runner={'entry_order_id':'o1','entry_fill_id':'f1'})
    fill = dict(user_id='o',account_id=1,contract_id=CONTRACT,order_id='o1',source_trade_id='f1',side='BUY')
    def match(attempts, fills, heavy=1):
        return EpisodeMatches(attempts, fills,'o',1,40).lookup(CONTRACT,'c','e',100,heavy)
    assert match([a],[fill])['entry_id']==10
    for attempts, fills in (([a],[]),([a],[dict(fill,user_id='x')]),([a],[dict(fill,account_id=2)]),
                            ([a],[dict(fill,contract_id='other')]),([a],[dict(fill,order_id='other')]),
                            ([a,dict(a,id=11)], [fill]),([a],[fill,fill])):
        assert match(attempts,fills)['disposition']=='Unknown'
    assert match([a],[fill],-1)['disposition']=='Unknown'
    result = closed_outcome(match([a],[fill]), dict(fill_ids=['f1','f2'], net=10),
                            dict(exit_order_id='other',exit_fill_id='f2',target_order_id='target'), {'f2':{'order_id':'other'}})
    assert result['broker_outcome']=='win' and result['exit_kind']=='other_profitable_exit'


def test_weekly_command_reads_copied_captures_and_skipped_signal(tmp_path, monkeypatch):
    from app.services.topbot_evidence import Capture
    from depth_recording import SegmentRecorder
    from depth_heatmap import SESSION_SCHEMA, encode_tier0
    from weekly_rebound import main
    from hashlib import sha256

    at = datetime(2026,11,20,15,tzinfo=UTC)
    owner = 'practice-owner'
    worker = Capture(owner, CONTRACT, root=tmp_path/'topbot-worker-evidence-v1', free_floor=0)
    try:
        worker.append({'event':'snapshot','data':{}}, at,
                      [dict(kind='episode_qualified', direction=1, episode_at=at.isoformat())])
    finally:
        worker.close()
    scope = sha256(owner.encode()).hexdigest()
    raw = SegmentRecorder(tmp_path/'depth-recordings-v1',scope)
    for offset in (-32,-20,1):
        raw.append(CONTRACT,'trade',{'type':1,'volume':2},received_at=(at+timedelta(seconds=offset)).isoformat())
    raw.close()
    sessions = tmp_path/'depth-heatmap-v1'/scope/'sessions'
    sessions.mkdir(parents=True)
    db = sqlite3.connect(sessions/'2026-11-19.sqlite3')
    ts = int(at.timestamp()*1000)
    db.executescript(SESSION_SCHEMA)
    db.execute("INSERT INTO chunks VALUES (1,'seg',?,0,?,1,?,?,'[]','[]')",(CONTRACT,raw.run_id,ts-32000,ts+60000))
    frames = [(ts+t*1000,[(400+int(t>0),3)],[(402+int(t>0),3)]) for t in range(-32,61)]
    db.execute("INSERT INTO tier_data VALUES (1,'depth0',?)",(encode_tier0(frames),))
    db.commit()
    db.close()
    bundle = tmp_path/'trade-review-v1/week-20261120/evidence'
    bundle.mkdir(parents=True)
    request = dict(strategySnapshot=dict(strategy_version_id='v',rules=dict(entry=dict(
        revision='dom_contrarian_v1',imbalance_direction='following'),exit=dict(exit_mode='fixed',
        initial_stop_points=25,take_profit_points=40))),
        entryEvidence=dict(signal=dict(episode_at=at.isoformat(), evidence=dict(capture_id=worker.capture_id))))
    attempt = dict(user_id=owner,bot_config_id=40,account_id=1,id=99,status='blocked',side='BUY',
                   raw_request=request,contract_id=CONTRACT,created_at=at.isoformat(),provider_order_id=None)
    data = dict(_owner=owner,_snapshot_at='2026-11-20T22:00:00Z',bot_order_attempts=[attempt],
                bot_configs=[dict(id=40,user_id=owner,account_id=1,contract_id=CONTRACT)],projectx_trade_events=[],
                accounts=[dict(user_id=owner,external_id='1',provider_simulated=True)])
    blob = gzip.compress(json.dumps(data).encode())
    (bundle/'execution.json.gz').write_bytes(blob)
    (bundle/'manifest.json').write_text(json.dumps(dict(owner=owner,files={'execution.json.gz':{'sha256':sha256(blob).hexdigest()}})))
    monkeypatch.setattr(sys,'argv',['recap','--cache',str(tmp_path),'--account','1','--week-ending','2026-11-20'])
    main()
    report = json.loads((bundle.parent/'rebound-recap.json').read_bytes())
    row, = report['qualifications']
    assert (row['condition'],row['disposition'],row['midpoint_change_60s']) == ('flagged','Skipped',.25)


@pytest.mark.skipif(not WEEK.exists(),reason='Private November 20 review bundle')
def test_recorded_outage_and_exact_historical_ids():
    from verify_rebound_review import replay
    report = replay(WEEK)
    assert len(report['qualifications']) == 401
    records = json.loads(gzip.decompress((WEEK/'records.json.gz').read_bytes()))
    import base64, struct, zlib
    bbo = json.loads(gzip.decompress((WEEK/'bbo.json.gz').read_bytes()))
    failed = datetime.fromisoformat(records['independent']['at'])
    assert records['independent']['error'] == 'OperationalError'
    # The legacy error receipt lost the watermark. Replay with no claimed later processing.
    watch = StudyWatch()
    stamps = sorted(t/1000 for t, *_ in struct.iter_unpack('<qiiHH', zlib.decompress(base64.b64decode(bbo['2026-11-19']))))
    from bisect import bisect_right
    for seconds in range(0,451,10):
        at = failed+timedelta(seconds=seconds)
        raw = datetime.fromtimestamp(stamps[bisect_right(stamps,at.timestamp())-1], UTC)
        status = watch.check(('owner',29593203),CONTRACT,dict(state='recording',contract_id=CONTRACT,
                             at=at.isoformat(),recording={'last_depth_at':raw.isoformat()}),{},at)
    assert status['state']=='study_stalled'
