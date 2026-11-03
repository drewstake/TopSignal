from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Account, ProjectXMarketCandle
from app.market_observation_models import MarketObservation
from app.research_storage import StorageSession, initialize_store, open_store
from tools.migrate_research_storage import canonical, checksum, models, restore, verify_export


@pytest.fixture
def routed(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    path = tmp_path / 'research-store-v1/research.sqlite3'
    local = initialize_store(path)
    cloud = create_engine('sqlite:///' + str(tmp_path / 'cloud.sqlite3'))
    Account.__table__.create(cloud)
    monkeypatch.setenv('TOPSIGNAL_RESEARCH_STORAGE', 'local')
    factory = sessionmaker(bind=cloud, class_=StorageSession)
    yield factory, local, cloud
    open_store.cache_clear()
    local.dispose()
    cloud.dispose()


def observation(owner, fingerprint):
    return dict(user_id=owner, contract_id='MNQ', source='test', event_type='quote',
                received_at=datetime.now(timezone.utc), fingerprint=fingerprint, details={})


def test_routing_concurrency_restart_and_tenant_filter(routed):
    factory, local, cloud = routed
    owners = ['00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002']

    def write(index):
        with factory() as db:
            db.add(MarketObservation(**observation(owners[index % 2], str(index))))
            db.commit()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(40)))
    open_store.cache_clear()
    with factory() as db:
        assert db.query(MarketObservation).filter_by(user_id=owners[0]).count() == 20
        assert db.get_bind(mapper=Account) is cloud
        assert db.get_bind(mapper=ProjectXMarketCandle).dialect.name == 'sqlite'
        with pytest.raises(RuntimeError, match='Cross-store'):
            db.execute(select(Account, MarketObservation))
        with pytest.raises(RuntimeError, match='mapped tables'):
            db.execute(text('select * from market_observations'))


def test_cloud_outage_does_not_affect_research_and_local_failure_never_falls_back(routed, tmp_path, monkeypatch):
    factory, local, cloud = routed
    from sqlalchemy import event
    @event.listens_for(cloud, 'connect')
    def unavailable(*_):
        raise RuntimeError('cloud unavailable')
    cloud.dispose()
    with factory() as db:
        db.add(MarketObservation(**observation('00000000-0000-0000-0000-000000000001', '1')))
        db.commit()
        assert db.query(MarketObservation).count() == 1
        with pytest.raises(RuntimeError, match='cloud unavailable'):
            db.query(Account).first()
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path / 'missing'))
    with factory() as db, pytest.raises(RuntimeError, match='store missing'):
        db.query(MarketObservation).count()


def test_archive_restores_all_rows_and_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_RESEARCH_STORAGE', 'application')
    source = initialize_store(tmp_path / 'source.sqlite3')
    with source.begin() as conn:
        conn.execute(MarketObservation.__table__.insert(), observation('00000000-0000-0000-0000-000000000001', 'abc'))
    archive = tmp_path / 'archive'
    archive.mkdir()
    manifest = {'schema': 1, 'tables': {}}
    with source.connect() as conn:
        for name, model in models().items():
            content = b''.join((canonical(dict(row)) + '\n').encode() for row in conn.execute(select(model.__table__)).mappings())
            path = archive / (name + '.jsonl.gz')
            with gzip.open(path, 'wb') as stream:
                stream.write(content)
            manifest['tables'][name] = dict(file=path.name, rows=content.count(b'\n'), bytes=path.stat().st_size,
                sha256=checksum(path), content_sha256=hashlib.sha256(content).hexdigest())
    (archive / 'manifest.json').write_text(json.dumps(manifest))
    result = restore(archive, tmp_path / 'restored.sqlite3')
    assert result['verified']
    assert result['tables']['market_observations']['rows'] == 1
    with pytest.raises(FileExistsError):
        restore(archive, tmp_path / 'restored.sqlite3')
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        verify_export(archive)
    source.dispose()
