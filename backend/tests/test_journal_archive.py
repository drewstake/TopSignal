import copy
import json

from cryptography.fernet import Fernet, InvalidToken
import pytest

from tools.archive_journal import archive, verify


def records():
    return dict(journal_entries=[dict(id=1,user_id='owner',account_id=10,entry_date='2026-01-01',body='private notes')],
        journal_entry_images=[dict(id=2,user_id='owner',account_id=10,journal_entry_id=1,entry_date='2026-01-01',filename='private/key.png',byte_size=3,mime_type='image/png')])


def test_encrypted_journal_roundtrip_with_owner_object_and_restore_verification(tmp_path):
    path=tmp_path/'journal.fernet';key=Fernet.generate_key()
    receipt=archive(records(),lambda name: b'abc',path,key,source='fixture')
    assert receipt['verified'] and receipt['entries']==1 and receipt['image_bytes']==3
    assert b'private notes' not in path.read_bytes()
    restored=verify(path,key,restore_to=tmp_path/'isolated')
    assert restored['isolated_restore'] and restored['archive_sha256']==receipt['archive_sha256']
    assert json.loads((tmp_path/'isolated/records.json').read_text())==records()
    assert next((tmp_path/'isolated/objects').iterdir()).read_bytes()==b'abc'
    with pytest.raises(FileExistsError):archive(records(),lambda _:b'abc',path,key,source='fixture')
    with pytest.raises(InvalidToken):verify(path,Fernet.generate_key())


@pytest.mark.parametrize('field,value',[('user_id','other'),('account_id',11),('journal_entry_id',3),('entry_date','2026-01-02')])
def test_journal_archive_rejects_cross_scope_or_orphan_images(tmp_path,field,value):
    data=copy.deepcopy(records());data['journal_entry_images'][0][field]=value
    with pytest.raises(ValueError,match='ownership'):
        archive(data,lambda _:pytest.fail('Must validate ownership before object I/O'),tmp_path/'x',Fernet.generate_key(),source='fixture')


def test_missing_or_wrong_size_objects_never_publish_verified_archive(tmp_path):
    with pytest.raises(ValueError,match='size'):
        archive(records(),lambda _:b'ab',tmp_path/'x',Fernet.generate_key(),source='fixture')
    assert not (tmp_path/'x').exists()
