import importlib.util
import json
from pathlib import Path
import sqlite3
import io
import tarfile

import pytest


def test_optional_quote_spool_is_copied_before_archive_handoff(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('cache_backup_quote_tool', Path(__file__).parents[1]/'tools/backup_market_cache.py')
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    from app.services.market_archive_quotes import QuoteSpool
    from app.services.market_archive import Archive
    from test_optional_quotes import quote
    source, output = tmp_path/'source', tmp_path/'backup'
    spool = QuoteSpool('fixture',source); spool.enqueue(quote(),now=0)
    with Archive('fixture',source).transaction(): pass
    original=tool.checksum
    def handoff(path):
        if Path(path).name == 'optional-quotes.sqlite3':
            spool.flush(now=0)
        return original(path)
    monkeypatch.setattr(tool,'checksum',handoff)
    tool.backup(source,output)
    restored=QuoteSpool('fixture',output)
    assert restored.status()['backlog'] == 1
    restored.flush(now=1)
    with Archive('fixture',output).reader() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1


def test_restored_cache_can_be_backed_up_again_without_losing_original_manifest(tmp_path):
    spec = importlib.util.spec_from_file_location('cache_backup_tool', Path(__file__).parents[1]/'tools/backup_market_cache.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source = tmp_path/'original'
    source.mkdir()
    (source/'model.json').write_text('{"frozen":"original"}')
    with sqlite3.connect(source/'ledger.sqlite3') as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO decisions VALUES (123)')
        db.commit()
        first = tmp_path/'first-backup'
        tool.backup(source, first)
    second = tmp_path/'second-backup'
    tool.backup(first, second)
    manifest = tool.verify(second)
    preserved = second/'source-manifests'/(tool.checksum(first/'backup-manifest.json')+'.json')
    assert preserved.read_bytes() == (first/'backup-manifest.json').read_bytes()
    assert (source/'model.json').read_bytes() == (second/'model.json').read_bytes()
    assert len(manifest['files']) == 3
    with sqlite3.connect(second/'ledger.sqlite3') as db:
        assert db.execute('SELECT id FROM decisions').fetchall() == [(123,)]


def test_transport_round_trip_preserves_long_windows_paths(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'tools'))
    from backup_market_cache import backup,pack,checksum,filesystem_path
    from run_market_archive_backup import unpack_backup
    source=filesystem_path(tmp_path/'source')
    relative=Path('audits')/('a'*64)/('b'*64)/('c'*64)/('d'*64+'.json')
    original=source/relative;original.parent.mkdir(parents=True)
    original.write_bytes(b'original evidence')
    snapshot=tmp_path/'snapshot';backup(source,snapshot)
    transport=tmp_path/'transport.tar';receipt=pack(snapshot,transport)
    assert receipt['sha256']==checksum(transport)
    restored=tmp_path/'restored';unpack_backup(transport,restored,max_bytes=100000)
    assert (filesystem_path(restored)/relative).read_bytes()==original.read_bytes()


@pytest.mark.parametrize('name,link,limit',[
    ('../escape',False,100),('C:/escape',False,100),('directory\\escape',False,100),
    ('link',True,100),('large',False,0),
])
def test_transport_rejects_unsafe_members_before_writing(tmp_path,monkeypatch,name,link,limit):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'tools'))
    from run_market_archive_backup import unpack_backup
    archive=tmp_path/'bad.tar'
    with tarfile.open(archive,'w') as f:
        item=tarfile.TarInfo(name)
        if link:item.type=tarfile.SYMTYPE;item.linkname='outside'
        else:item.size=1
        f.addfile(item,None if link else io.BytesIO(b'x'))
    destination=tmp_path/'destination'
    with pytest.raises(ValueError):unpack_backup(archive,destination,max_bytes=limit)
    assert not destination.exists()
