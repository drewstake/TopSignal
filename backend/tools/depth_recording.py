"""Compressed, bounded raw-depth segments. No broker, database or model writes."""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4
import zlib

SCHEMA = 'mnq-depth-segment-v1'


def utc():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+uuid4().hex+'.tmp')
    with temporary.open('xb') as f:
        f.write(canonical(value)); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)


def checksum(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


class StorageLimit(RuntimeError):
    pass


def inventory(root, *, max_bytes, reserve_bytes, max_age_days=30, now=None):
    """Count every retained file, including incomplete evidence. Never delete."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    used = incomplete = sealed = 0
    oldest = now
    for p in root.rglob('*'):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise StorageLimit('recording_reparse_point')
        if p.is_file():
            stat = p.stat(); used += stat.st_size; oldest = min(oldest, stat.st_mtime)
            incomplete += p.name.endswith('.partial')
            sealed += p.name.endswith('.segment.json')
    free = shutil.disk_usage(root).free
    reason = ('recording_budget_reached' if used+32*1024**2 > max_bytes else
              'disk_free_reserve' if free < reserve_bytes+32*1024**2 else
              'retention_review_due' if now-oldest > max_age_days*86400 else None)
    return dict(bytes=used, free_bytes=free, budget_bytes=max_bytes, reserve_bytes=reserve_bytes,
                incomplete_segments=incomplete, sealed_segments=sealed, blocked_reason=reason,
                automatic_deletion=False, review_after_days=max_age_days)


class SegmentRecorder:
    def __init__(self, root, owner_hash, *, data_live=False, segment_bytes=16*1024**2, segment_seconds=60, before_segment=None):
        if len(owner_hash)!=64 or any(c not in '0123456789abcdef' for c in owner_hash):
            raise ValueError('Invalid owner scope')
        if type(data_live) is not bool or not 1024<=segment_bytes<=16*1024**2 or not 1<=segment_seconds<=300:
            raise ValueError('Invalid recording bounds')
        self.root=Path(root).resolve(); self.owner_hash=owner_hash; self.data_live=data_live
        self.run_id=uuid4().hex
        self.path=self.root/datetime.now(timezone.utc).strftime('%Y-%m-%d')/self.run_id
        self.path.mkdir(parents=True,exist_ok=False)
        self.limit_bytes=segment_bytes; self.limit_seconds=segment_seconds
        self.before_segment=before_segment
        self.index=self.durable_index=self.number=self.raw_bytes=self.sealed_bytes=0
        self.segment_raw=0; self.handle=self.compressor=None
        self.counts=Counter(); self.types=Counter(); self.fields=Counter()
        self.last_event_at=self.last_depth_at=self.last_durable_at=None
        self.last_flush=time.monotonic(); self.segment_opened=None
        self.recent={1:{},2:{}}; self.maximum_recent_levels={1:0,2:0}; self.level2_observed=False
        self.provider_time_counts=Counter()
        self.closed=False

    def append(self, contract, kind, payload, *, received_at=None, monotonic_ns=None):
        if self.closed or not contract.startswith('CON.F.US.MNQ.') or kind not in {'connect','disconnect','gap','roll','quote','trade','depth','end','book_totals'}:
            raise ValueError('Invalid event or closed recorder')
        received_at=received_at or utc()
        provider_at=payload.get('timestamp') if kind in {'quote','trade','depth'} else None
        # Preserve the raw timestamp in payload even when it cannot be parsed.
        try:
            parsed=datetime.fromisoformat(provider_at.replace('Z','+00:00'))
            if parsed.tzinfo is None:raise ValueError('No timezone')
            provider_at=parsed.astimezone(timezone.utc).isoformat()
        except (AttributeError,TypeError,ValueError):provider_at=None
        event=dict(schema='mnq-depth-events-v1',owner_hash=self.owner_hash,contract_id=contract,
                   data_live=self.data_live,capture_id=self.run_id,index=self.index+1,received_at=received_at,
                   monotonic_ns=time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
                   kind=kind,payload=payload,provider_at=provider_at,source='projectx',synthetic=False)
        line=canonical(event)+b'\n'
        if len(line)>70000:raise ValueError('Oversized market event')
        now=time.monotonic()
        if self.handle and (self.segment_raw+len(line)>self.limit_bytes or now-self.segment_opened>=self.limit_seconds):
            self.seal()
        if self.handle is None:
            if self.before_segment:self.before_segment()
            self.partial=self.path/f'{self.number:08d}.ndjson.gz.partial'
            self.handle=self.partial.open('xb')
            self.compressor=gzip.GzipFile(filename='',mode='wb',fileobj=self.handle,compresslevel=3,mtime=0)
            self.segment_raw=0; self.segment_events=0; self.first_index=self.index+1
            self.segment_first_at=received_at; self.segment_opened=now; self.raw_hash=hashlib.sha256()
        self.compressor.write(line); self.raw_hash.update(line)
        self.index+=1; self.segment_events+=1; self.segment_raw+=len(line); self.raw_bytes+=len(line)
        self.last_event_at=received_at; self.segment_last_at=received_at; self.counts[kind]+=1
        if kind in {'connect','disconnect','gap','roll'}:self.recent={1:{},2:{}}
        if kind=='depth':
            self.last_depth_at=received_at; typ=payload.get('type'); self.types[str(typ)]+=1
            self.fields['with_seq']+='seq' in payload; self.fields['with_epoch']+='epoch' in payload
            if typ==6:self.recent={1:{},2:{}}
            fresh=False
            if provider_at:
                lag=(datetime.fromisoformat(received_at)-datetime.fromisoformat(provider_at)).total_seconds()
                category='fresh_0_to_2_seconds' if 0<=lag<=2 else 'future' if lag<0 else 'older_than_2_seconds'
                self.provider_time_counts[category]+=1; fresh=0<=lag<=2
            else:self.provider_time_counts['unparseable']+=1
            if type(typ) is int and typ in self.recent:
                p,q=payload.get('price'),payload.get('volume')
                if fresh and isinstance(p,(int,float)) and not isinstance(p,bool) and isinstance(q,(int,float)) and not isinstance(q,bool) and p>0 and q>0:
                    self.recent[typ][p]=now
                for side in (1,2):
                    self.recent[side]={p:t for p,t in self.recent[side].items() if now-t<=2}
                    self.maximum_recent_levels[side]=max(self.maximum_recent_levels[side],len(self.recent[side]))
                self.level2_observed |= all(len(v)>=2 for v in self.recent.values())
        if now-self.last_flush>=1:self.flush()
        return event

    def flush(self):
        if self.handle:
            self.compressor.flush(zlib.Z_SYNC_FLUSH); self.handle.flush(); os.fsync(self.handle.fileno())
            self.durable_index=self.index; self.last_durable_at=utc()
        self.last_flush=time.monotonic()

    def seal(self):
        if self.handle is None:return
        self.compressor.close(); self.handle.flush(); os.fsync(self.handle.fileno()); self.handle.close()
        self.handle=self.compressor=None
        completed=self.partial.with_suffix('')
        os.replace(self.partial,completed)
        size=completed.stat().st_size
        manifest=dict(schema=SCHEMA,file=completed.name,sha256=checksum(completed),bytes=size,
                      uncompressed_bytes=self.segment_raw,uncompressed_sha256=self.raw_hash.hexdigest(),
                      events=self.segment_events,first_index=self.first_index,last_index=self.index,
                      first_received_at=self.segment_first_at,last_received_at=self.segment_last_at,
                      owner_hash=self.owner_hash,data_live=self.data_live,capture_id=self.run_id,
                      synthetic=False,source='projectx',provider_sequence_guarantee=False,snapshot_completion_verified=False)
        # This immutable manifest is the commit marker used by backup snapshots.
        atomic_json(self.path/f'{self.number:08d}.segment.json',manifest)
        self.number+=1; self.sealed_bytes+=size; self.durable_index=self.index; self.last_durable_at=utc()

    def tick(self):
        if self.handle and time.monotonic()-self.segment_opened>=self.limit_seconds:self.seal()
        elif self.handle and time.monotonic()-self.last_flush>=1:self.flush()

    def close(self):
        if not self.closed:self.seal(); self.closed=True

    def status(self):
        return dict(run_id=self.run_id,received_events=self.index,durable_event_index=self.durable_index,
                    counts=dict(self.counts),depth_types=dict(self.types),provider_fields=dict(self.fields),
                    provider_time_counts=dict(self.provider_time_counts),level2_observed=self.level2_observed,
                    maximum_recent_explicit_ask_prices=self.maximum_recent_levels[1],maximum_recent_explicit_bid_prices=self.maximum_recent_levels[2],
                    price_count_basis='Distinct positive explicit depth updates with fresh provider times within a two-second receipt window; not a complete book.',
                    last_event_at=self.last_event_at,last_depth_at=self.last_depth_at,last_durable_at=self.last_durable_at,
                    sealed_segments=self.number,sealed_bytes=self.sealed_bytes,uncompressed_bytes=self.raw_bytes,
                    provider_sequence_guarantee=False,snapshot_completion_verified=False,model_inputs_enabled=False)


def verify_segments(root):
    root=Path(root).resolve(); segments=events=compressed=0
    for p in sorted(root.rglob('*.segment.json')):
        m=json.loads(p.read_text()); name=m.get('file','')
        if m.get('schema')!=SCHEMA or Path(name).name!=name or not name.endswith('.ndjson.gz'):
            raise ValueError('Invalid segment manifest')
        data=(p.parent/name).resolve()
        if not data.is_relative_to(root) or data.is_symlink() or checksum(data)!=m['sha256'] or data.stat().st_size!=m['bytes']:
            raise ValueError('Segment checksum mismatch')
        raw_hash=hashlib.sha256();count=raw_bytes=0
        with gzip.open(data,'rb') as f:
            for line in f:
                if len(line)>70000:raise ValueError('Oversized event')
                r=json.loads(line);raw_hash.update(line);raw_bytes+=len(line)
                if (r['schema']!='mnq-depth-events-v1' or r['index']!=m['first_index']+count or r['capture_id']!=m['capture_id']
                        or r['owner_hash']!=m['owner_hash'] or r['data_live'] is not m['data_live'] or r['synthetic'] is not False):
                    raise ValueError('Segment event scope/order mismatch')
                count+=1
        if count!=m['events'] or m['last_index']!=m['first_index']+count-1 or raw_hash.hexdigest()!=m['uncompressed_sha256'] or raw_bytes!=m['uncompressed_bytes']:
            raise ValueError('Segment content mismatch')
        segments+=1;events+=count;compressed+=m['bytes']
    return dict(verified_segments=segments,verified_events=events,compressed_bytes=compressed,
                incomplete_segments=len(list(root.rglob('*.partial'))))
