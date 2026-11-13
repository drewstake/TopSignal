"""Resolve a deployed immutable release from the Windows task, without secrets."""
import json
from pathlib import Path
import re
import stat
import subprocess


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def runtime_environment_file(config):
    task = config.get('runtime_task_name')
    if not task:
        # Explicit paths remain supported for isolated backup/restore fixtures.
        path = Path(config['runtime_env_file'])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Existing absolute runtime environment required')
        return path
    if 'runtime_env_file' in config or not re.fullmatch(r'[A-Za-z0-9 _-]{1,80}', task):
        raise ValueError('One unambiguous runtime configuration selector required')
    releases = Path(config['release_root']).resolve(strict=True)
    script = "$ErrorActionPreference='Stop'; @(Get-ScheduledTask -TaskName '" + task + "').Actions | Select-Object Execute,Arguments | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8-sig', timeout=15, check=True)
    action = json.loads(result.stdout)
    if not isinstance(action, dict) or not str(action.get('Execute', '')).lower().endswith('powershell.exe'):
        raise ValueError('One immutable runtime task action required')
    arguments = action.get('Arguments', '')
    roots = re.findall(r'(?i)(?:^|\s)-RepoRoot\s+"([^"\r\n]+)"', arguments)
    if len(roots) != 1 or 'run-production.ps1' not in arguments.lower():
        raise ValueError('Runtime task release root is ambiguous')
    selected = Path(roots[0])
    if linked(selected):
        raise ValueError('Runtime task must select an immutable release directory')
    release = selected.resolve(strict=True)
    if release.parent != releases:
        raise ValueError('Runtime task must select an immutable release directory')
    path = release / 'backend' / '.env'
    if (not path.is_file() or not path.resolve().is_relative_to(release)
            or any(linked(p) for p in (path, path.parent))):
        raise ValueError('Runtime environment absent from selected release')
    return path
