from threading import BoundedSemaphore
from time import monotonic
from types import SimpleNamespace
import sqlite3

from fastapi import HTTPException
import pytest

from app.services import liquidity_heatmap_history as service
from tools.depth_heatmap import read_heatmap
from test_depth_heatmap import CONTRACT


def test_saturated_heatmap_reader_rejects_immediately_without_disk_io(monkeypatch, tmp_path):
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(service, '_slots', slots)
    monkeypatch.setattr(service, 'history_source_path', lambda: tmp_path/'absent')
    monkeypatch.setattr(service, 'read_heatmap', lambda *a, **kw: pytest.fail('No spare disk slot'))
    with pytest.raises(RuntimeError, match='busy'):
        service.account_heatmap(user_id='saturated', contract_id=CONTRACT, data_live=False,
                                start_ms=0, end_ms=1000, tier=0)


def test_sqlite_failure_returns_recoverable_status_and_releases_slot(monkeypatch, tmp_path):
    from app import main
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(service, '_slots', slots)
    monkeypatch.setattr(service, 'history_source_path', lambda: tmp_path/'absent')
    def locked(*a, **kw):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(service, 'read_heatmap', locked)
    monkeypatch.setattr(main, 'get_authenticated_user_id', lambda: 'locked')
    monkeypatch.setattr(main, '_require_owned_projectx_account', lambda *a, **kw: None)
    with pytest.raises(HTTPException) as error:
        main.get_liquidity_heatmap_history(1, SimpleNamespace(headers={}), CONTRACT, 0, 1000,
                                           0, False, False, db=SimpleNamespace(rollback=lambda: None))
    assert error.value.status_code == 503
    assert error.value.headers == {'Cache-Control': 'no-store', 'Retry-After': '5'}
    assert error.value.detail['server_time_ms'] > 0
    assert slots.acquire(blocking=False)
    slots.release()


def test_expired_heatmap_deadline_reads_nothing(tmp_path):
    with pytest.raises(RuntimeError, match='Retry'):
        read_heatmap(tmp_path, {}, deadline=monotonic()-1)
