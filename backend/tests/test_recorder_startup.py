from cryptography.fernet import Fernet
import pytest
from tools.recorder_startup import configuration


def fixture_config(tmp_path):
    env = tmp_path / 'recorder.env'
    env.write_text('DATABASE_URL=postgresql+psycopg://depth_reader:fixture@127.0.0.1:5433/topsignal\n'
                   'CREDENTIALS_ENCRYPTION_KEY=' + Fernet.generate_key().decode() + '\n'
                   'TOPSIGNAL_LIVE_EXECUTION_ENABLED=false\nTOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false\n'
                   'TOPSIGNAL_DATABENTO_CACHE_DIR=' + tmp_path.as_posix() + '\n')
    return dict(backend_root=str(tmp_path), credential_env_file=str(env), database_role='depth_reader',
                owner_id='11111111-1111-1111-1111-111111111111', recording_root=str(tmp_path/'depth-recordings-v1'))


def test_pinned_cloud_environment_and_inherited_credentials_are_not_loaded(tmp_path, monkeypatch):
    config = fixture_config(tmp_path)
    (tmp_path/'.env').write_text('DATABASE_URL=postgresql://cloud.invalid/test\n')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://cloud.invalid/test')
    values, url, owner, *_ = configuration(config)
    assert url.host == '127.0.0.1' and owner == config['owner_id']
    assert not any(k.startswith('SUPABASE') for k in values)


@pytest.mark.parametrize('old,new', [
    ('127.0.0.1', 'cloud.invalid'), ('depth_reader:', 'application_writer:'),
    ('TOPSIGNAL_LIVE_EXECUTION_ENABLED=false', 'TOPSIGNAL_LIVE_EXECUTION_ENABLED=true'),
    ('TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false', 'SUPABASE_URL=https://obsolete.invalid'),
])
def test_recorder_configuration_fails_before_connect(tmp_path, old, new):
    from pathlib import Path
    config = fixture_config(tmp_path); path = Path(config['credential_env_file'])
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(RuntimeError): configuration(config)


def test_no_implicit_release_environment_fallback(tmp_path):
    with pytest.raises(RuntimeError, match='Explicit protected'):
        configuration(dict(backend_root=str(tmp_path)))


def test_independent_recording_loop_does_not_import_application_database(tmp_path):
    """A clean scheduled process has no ambient application DATABASE_URL."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    backend = Path(__file__).resolve().parents[1]
    script = r'''
import asyncio,json,pathlib,sys
sys.path.insert(0,sys.argv[1])
from tools.record_market_depth import record
root=pathlib.Path(sys.argv[2])
runtime=root/'runtime';runtime.mkdir()
(runtime/'STOP').write_text('startup acceptance')
config=dict(runtime_root=str(runtime),max_bytes=64*1024**2,reserve_bytes=0,
            review_after_days=30,segment_seconds=60,record_contract_totals=True)
class NoBrokerCalls:
    def __getattr__(self,name):
        raise AssertionError('Stopped startup must not call the broker')
assert asyncio.run(record(config,NoBrokerCalls(),'a'*64,root/'raw'))=='stopped'
assert 'app.db' not in sys.modules
assert 'app.models' not in sys.modules
assert json.loads((runtime/'status.json').read_text())['state']=='stopped'
'''
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'PYTHONPATH')
                   and not key.startswith('SUPABASE_')}
    environment['PYTHON_DOTENV_DISABLED'] = '1'
    result = subprocess.run([sys.executable, '-B', '-c', script, str(backend), str(tmp_path)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
