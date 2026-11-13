"""Optional pacing and recorder-health admission for bulk filesystem backups."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time


class BackupIO:
    def __init__(self, mib_per_second=0, status_path=None, *, clock=time.monotonic, sleep=time.sleep):
        if not 0 <= mib_per_second <= 64:
            raise ValueError('Backup rate must be between 0 and 64 MiB/s')
        self.rate = mib_per_second * 1024**2
        self.status_path = Path(status_path) if status_path else None
        self.clock, self.sleep = clock, sleep
        self.next_io = clock()
        self.next_check = 0

    def check(self, size=0):
        now = self.clock()
        if self.status_path and now >= self.next_check:
            try:
                health = json.loads(self.status_path.read_text(encoding='utf-8-sig'))
                age = (datetime.now(timezone.utc)-datetime.fromisoformat(health['at'])).total_seconds()
                good = health['state'] == 'recording' and 0 <= age <= 30
                good = good and not health.get('storage',{}).get('scan_stalled',False)
            except (OSError, ValueError, KeyError, TypeError):
                good = False
            if not good:
                raise RuntimeError('Backup deferred: recorder health is unavailable, stale or under pressure')
            self.next_check = now+2
        if self.rate and size:
            # No accumulated credits: quiet time must not permit a later I/O burst.
            self.next_io = max(now,self.next_io)+size/self.rate
            while self.clock() < self.next_io:
                self.sleep(min(1,self.next_io-self.clock()))


class PacedReader:
    def __init__(self, stream, checkpoint):
        self.stream, self.checkpoint = stream, checkpoint

    def read(self, size=-1):
        result = self.stream.read(size)
        self.checkpoint(len(result))
        return result
