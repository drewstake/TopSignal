"""Read the existing filesystem cache; export bounded 2024 replay input locally."""
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.trading_day import futures_session_is_open

cache = Path(os.environ.get('TOPSIGNAL_DATABENTO_CACHE_DIR', ROOT / 'backend/storage/databento'))
manifest = json.loads((cache / 'current.json').read_text())
series = cache / manifest['version_dir'] / manifest['series']['MNQ:5m']['path']
names = ['timestamp_ns', 'open_nano', 'high_nano', 'low_nano', 'close_nano', 'volume',
         'instrument_id', 'raw_symbol_code', 'observed_minute_count']
arrays = {n: np.load(series / (n + '.npy'), mmap_mode='r') for n in names}
tz = ZoneInfo('America/New_York')
start = datetime(2024, 1, 1, tzinfo=tz)
end = datetime(2025, 1, 1, tzinfo=tz)
lo, hi = np.searchsorted(arrays['timestamp_ns'], [int(start.timestamp()*1e9), int(end.timestamp()*1e9)])
bars = []
months = Counter()
partial = Counter()
rolls = []
holidays = {101,115,219,329,527,619,703,704,902,1128,1129,1224,1225}
def entry_slot(d):
    return d.weekday() < 5 and d.month*100+d.day not in holidays and 575 <= d.hour*60+d.minute <= 930

for i in range(int(lo), int(hi)):
    t = int(arrays['timestamp_ns'][i]) // 1_000_000_000
    d = datetime.fromtimestamp(t, tz)
    b = dict(t=t, o=float(arrays['open_nano'][i])/1e9, h=float(arrays['high_nano'][i])/1e9,
             l=float(arrays['low_nano'][i])/1e9, c=float(arrays['close_nano'][i])/1e9,
             v=int(arrays['volume'][i]), instrument=int(arrays['instrument_id'][i]),
             symbolCode=int(arrays['raw_symbol_code'][i]), observedMinutes=int(arrays['observed_minute_count'][i]))
    if bars and b['symbolCode'] != bars[-1]['symbolCode']:
        rolls.append(dict(time=d.isoformat(), previous=bars[-1]['symbolCode'], new=b['symbolCode']))
    bars.append(b)
    months[d.strftime('%Y-%m')] += 1
    if b['observedMinutes'] != 5:
        partial['all'] += 1
        if entry_slot(d): partial['entry_hours'] += 1

actual = {b['t'] for b in bars}
expected, missing, missing_entries = 0, [], []
d = start
while d < end:
    if futures_session_is_open(d, symbol='MNQ'):
        expected += 1
        if int(d.timestamp()) not in actual: missing.append(d.isoformat())
    if entry_slot(d) and int(d.timestamp()) not in actual: missing_entries.append(d.isoformat())
    d += timedelta(minutes=5)
assert len(actual) == len(bars)
assert all(bars[i]['t'] > bars[i-1]['t'] for i in range(1, len(bars)))
out = cache / 'exports/fxreplay/adaptive-2024-backtest'
out.mkdir(parents=True, exist_ok=True)
payload = json.dumps(bars, separators=(',', ':')).encode()
(out / 'bars.json').write_bytes(payload)
coverage = dict(period='2024-01-01 through 2024-12-31 America/New_York', bars=len(bars),
    first=datetime.fromtimestamp(bars[0]['t'], tz).isoformat(), last=datetime.fromtimestamp(bars[-1]['t'], tz).isoformat(),
    months=dict(sorted(months.items())), expected_scheduled_5m_buckets=expected,
    missing_scheduled_buckets=len(missing), missing_scheduled_times=missing,
    missing_entry_buckets=len(missing_entries), missing_entry_times=missing_entries,
    partial_observed_minutes=dict(partial), rolls=rolls,
    source_fingerprint=manifest['source_fingerprint'], series_fingerprint=manifest['series']['MNQ:5m']['series_fingerprint'],
    roll_policy=manifest['roll_policy_version'], data_sha256=hashlib.sha256(payload).hexdigest(),
    input_policy='Use existing observed-minute 5m aggregates, as supplied to an OHLCV indicator; no filling or future-trained model. Start with empty state on first available 2024 bar.')
(out / 'coverage.json').write_text(json.dumps(coverage, indent=2))
print(json.dumps({k:v for k,v in coverage.items() if not k.endswith('_times')}, indent=2))
print('OUTPUT', out)
