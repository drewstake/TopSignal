import json
from types import SimpleNamespace
import pytest
from tools.runtime_configuration import runtime_environment_file


def test_backup_follows_task_release_and_rejects_stale_ambiguous_selector(tmp_path, monkeypatch):
    releases = tmp_path/'releases'; releases.mkdir()
    def release(name):
        path = releases/name/'backend'; path.mkdir(parents=True); (path/'.env').write_text('fixture')
        return path/'.env'
    first, second = release('first'), release('second')
    current = [first]
    def task(*args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(dict(Execute='C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
            Arguments=f'-File "C:/tools/run-production.ps1" -RepoRoot "{current[0].parent.parent}"')))
    monkeypatch.setattr('tools.runtime_configuration.subprocess.run', task)
    config = dict(runtime_task_name='TopSignal Personal Device', release_root=str(releases))
    assert runtime_environment_file(config) == first
    current[0] = second
    assert runtime_environment_file(config) == second
    with pytest.raises(ValueError, match='unambiguous'):
        runtime_environment_file(dict(config, runtime_env_file=str(first)))
    current[0] = tmp_path/'untrusted'/'backend'/'.env'
    current[0].parent.mkdir(parents=True); current[0].write_text('fixture')
    with pytest.raises(ValueError, match='immutable'):
        runtime_environment_file(config)
