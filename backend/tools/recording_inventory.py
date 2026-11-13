"""Fresh directory metadata without reopening every Windows directory ancestor."""
import os
from pathlib import Path
import re
import stat


def directories(parent, pattern, *, reverse=False):
    result = []
    with os.scandir(parent) as entries:
        for entry in entries:
            if not re.fullmatch(pattern, entry.name):
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Recording reparse points are unsupported.')
            if stat.S_ISDIR(info.st_mode):
                result.append((Path(entry.path), [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]))
    return sorted(result, key=lambda item: str(item[0]), reverse=reverse)
