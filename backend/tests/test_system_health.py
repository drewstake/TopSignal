from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError

from app.services import system_health as health


NOW = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)


def recording(at):
    return dict(at=at.isoformat(), state='recording', storage={}, last_frame_at=at.isoformat(),
                recording=dict(received_events=10, durable_event_index=10, last_depth_at=at.isoformat()))


@pytest.mark.parametrize('value', [None, 'invalid', NOW.replace(tzinfo=None).isoformat(),
                                    (NOW + timedelta(seconds=1)).isoformat()])
def test_missing_invalid_and_future_checks_are_unknown(value):
    result = health.discord(dict(state='watching', at=value), NOW)
    assert result.status == 'Unknown' and result.checked_at is None


def test_archive_and_execution_are_independent_and_failure_ages_out():
    old = NOW - timedelta(days=4)
    failure = dict(status='failed', last_verified_at=old.isoformat(), last_attempt_at=NOW.isoformat())
    assert health.backup(failure, NOW, archive=True).status == 'Unhealthy'
    stale = health.backup(failure, NOW + timedelta(hours=37), archive=True)
    assert stale.status == 'Unknown' and stale.checked_at == NOW
    assert health.backup(dict(status='verified', last_verified_at=old.isoformat(),
                              last_attempt_at=NOW.isoformat()), NOW, archive=True).status == 'Unknown'
    assert health.backup(dict(state='verified_and_published', observed_at=NOW.isoformat()), NOW, archive=False).status == 'Healthy'


def test_recorder_and_market_use_distinct_evidence_even_when_bot_is_idle():
    depth = recording(NOW)
    check = health.evidence(depth['at'], NOW, 60, 'the recorder')
    assert health.recorder(depth, check, NOW).status == health.market_data(depth, check, NOW).status == 'Healthy'
    old = NOW - timedelta(minutes=10)
    depth['recording']['last_depth_at'] = old.isoformat()
    market = health.market_data(depth, check, NOW)
    assert market.status == 'Unknown' and market.checked_at == old
    assert health.recorder(depth, check, NOW).status == 'Healthy'
    depth['recording'].update(received_events=13, last_durable_at=old.isoformat())
    assert health.recorder(depth, check, NOW).status == 'Unhealthy'
    depth.update(state='reconnecting')
    assert health.market_data(depth, check, NOW).status == 'Unhealthy'
    stale_check = health.evidence(depth['at'], NOW + timedelta(minutes=5), 60, 'the recorder')
    assert health.recorder(depth, stale_check, NOW).status == 'Unknown'
    assert health.market_data(depth, stale_check, NOW).status == 'Unknown'


def test_closed_session_uses_transport_heartbeat():
    saturday = NOW + timedelta(days=1)
    depth = recording(saturday)
    depth['recording']['last_depth_at'] = NOW.isoformat()
    check = health.evidence(depth['at'], saturday, 60, 'the recorder')
    market = health.market_data(depth, check, saturday)
    assert market.status == 'Healthy' and 'closed' in market.reason
    depth.pop('last_frame_at')
    assert health.market_data(depth, check, saturday).status == 'Unknown'


def test_partial_host_failure_and_intentional_pause_leave_other_checks_available(tmp_path, monkeypatch):
    depth = tmp_path / 'depth-recorder/status.json'
    depth.parent.mkdir()
    depth.write_text('{')
    reader = tmp_path / 'discord-topbot/status.json'
    reader.parent.mkdir()
    reader.write_text(json.dumps(dict(state='attention', at=datetime.now(timezone.utc).isoformat(),
                                      errors=['private detail'])))
    monkeypatch.setenv('TOPSIGNAL_DEPTH_RECORDER_STATUS', str(depth))
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    db = Mock()
    db.get_bind.return_value.dialect.name = 'postgresql'
    db.execute.side_effect = OperationalError('private query', {}, Exception('private detail'))
    worker = dict(enabled=False, healthy=True)
    rows = health.report(db, worker)['components']
    assert {k: v.status for k, v in rows.items()} == dict(
        postgres='Unhealthy', recorder='Unknown', market='Unknown', worker='Unknown',
        discord='Unhealthy', execution_backup='Unknown', archive_backup='Unknown')
    assert 'disabled' in rows['worker'].reason and 'private' not in str(rows)
    assert str(db.execute.call_args.args[0]).lower() == 'select 1'
    reader.with_name('STOP').touch()
    assert 'pause' in health.report(db, worker)['components']['discord'].reason
    assert sorted(p.name for p in tmp_path.iterdir()) == ['depth-recorder', 'discord-topbot']


def test_sqlite_does_not_certify_postgres():
    db = Mock()
    db.get_bind.return_value.dialect.name = 'sqlite'
    assert health.database(db, NOW).status == 'Unknown'
    db.execute.assert_not_called()
