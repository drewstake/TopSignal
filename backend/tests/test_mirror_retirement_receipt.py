from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest
from tools.retire_local_mirror import checked_backup


def test_retirement_requires_fresh_fenced_cloud_independent_matching_recovery(tmp_path):
    now = datetime.now(timezone.utc)
    receipt = tmp_path/'off-device-verification.json'
    files = {}
    for name in ('execution.dump','manifest','credential-key','local-auth.sqlite3','runtime-env'):
        path = tmp_path/(name+'.fernet')
        path.write_bytes(name.encode())
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    good = dict(verified=True, authority_absent=True, auth_issuance=True, auth_restart=True,
                auth_revocation=True, auth_network_calls=0, database='topsignal_audit_restore',
                verified_at=now.isoformat(), backup_created_at=now.isoformat(), encrypted_files=files, dump_sha256='a'*64)
    receipt.write_text(json.dumps(good))
    assert checked_backup(receipt,now=now) == hashlib.sha256(receipt.read_bytes()).hexdigest()
    for changes in ({'authority_absent':False}, {'auth_network_calls':1}, {'database':'topsignal'},
                    {'verified_at':(now-timedelta(hours=3)).isoformat()},
                    {'backup_created_at':(now-timedelta(hours=3)).isoformat()}, {'encrypted_files':{}}):
        receipt.write_text(json.dumps(good|changes))
        with pytest.raises(ValueError):
            checked_backup(receipt,now=now)
    receipt.write_text(json.dumps(good))
    (tmp_path/'execution.dump.fernet').write_bytes(b'changed')
    with pytest.raises(ValueError,match='differs'):
        checked_backup(receipt,now=now)
