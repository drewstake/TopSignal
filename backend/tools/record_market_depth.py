"""Independent paper-only continuous recorder, using a read-only market client."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import shutil

SUBSCRIPTION_TIMEOUT_SECONDS = 15
TRANSPORT_TIMEOUT_SECONDS = 45

if __package__:
    from .depth_recording import SegmentRecorder, StorageLimit, atomic_json, inventory, utc, verify_segments
else:
    from depth_recording import SegmentRecorder, StorageLimit, atomic_json, inventory, utc, verify_segments


def _wall():
    return datetime.now(timezone.utc).timestamp()


class CapacityMonitor:
    """One bounded inventory job; never scan the archive on the socket thread.

    A full inventory walks every retained file. On the recording disk that walk
    ran for most of every 30-second interval and, whenever one exceeded 90
    seconds, ended the recorder until the scheduler started it again. Those
    restarts are the gaps in recorded history.

    The limits are unchanged and stay enforced between walks. Every uncompressed
    byte written since the last walk STARTED is charged against its result (a
    deliberate overestimate), free space is read from the volume on every check,
    and review age is measured from the oldest file already seen. A slow or
    failed walk is reported and retried. Capture stops only when no walk has
    completed within the stale limit, never silently waiving the limits.
    """
    STALL_SECONDS = 90
    RESTART_ALLOWANCE = 256 * 1024**2

    def __init__(self, root, config, *, cache=None, clock=None):
        self.root, self.config = root, config
        self.clock = clock or (lambda: time.monotonic())  # resolved per call
        self.interval = min(3600, max(30, int(config.get('capacity_scan_interval_seconds', 900))))
        self.stale_after = min(86400, max(self.interval*2, int(config.get('capacity_scan_stale_seconds', 21600))))
        self.cache = Path(cache) if cache else None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='depth-capacity')
        self.pending = None
        self.baseline = self.scan_bytes = self.failures = self.allowance = 0
        self.last_error = None
        self.started = self.clock()
        cached = self._cached()
        if cached is None:
            self.value = self._scan()
            self.completed = self.clock()
            self.completed_wall = _wall()
            self.last_scan_seconds = self.completed - self.started
            self._remember()
            self.next = self.completed + self.interval
        else:
            # A recent result from the previous run avoids a blocking walk before
            # reconnecting. Charge a fixed allowance for whatever that run wrote
            # after its last walk, and refresh in the background immediately.
            self.value, age = cached
            self.completed = self.clock() - age
            self.completed_wall = _wall() - age
            self.last_scan_seconds = self.value.pop('scan_seconds', None)
            self.allowance = self.RESTART_ALLOWANCE
            self.next = self.clock()

    def _cached(self):
        try:
            saved = json.loads(self.cache.read_text(encoding='utf-8'))
            age = _wall() - float(saved['at'])
            value = saved['value']
            if not 0 <= age < self.stale_after or saved['root'] != str(self.root) or type(value['bytes']) is not int:
                return None
            return value, age
        except (AttributeError, OSError, ValueError, KeyError, TypeError):
            return None

    def _remember(self):
        if self.cache is None:
            return
        try:
            atomic_json(self.cache, dict(at=self.completed_wall, root=str(self.root),
                                         value=dict(self.value, scan_seconds=self.last_scan_seconds)))
        except OSError:
            pass  # Convenience only; the next start performs a full walk.

    def _scan(self):
        # Sealing atomically renames .partial files while this read-only scan is
        # running. Retry that specific race, without concealing other I/O errors.
        for attempt in range(3):
            try:
                return inventory(self.root, max_bytes=self.config['max_bytes'], reserve_bytes=self.config['reserve_bytes'],
                                 max_age_days=self.config['review_after_days'])
            except FileNotFoundError:
                if attempt == 2:
                    raise

    def check(self, written=0, *, opening=False):
        now = self.clock()
        if self.pending and self.pending.done():
            try:
                self.value = self.pending.result()
            except Exception as exc:
                self.failures += 1
                self.last_error = type(exc).__name__
                self.next = now + min(self.interval, 60*self.failures)
            else:
                self.baseline, self.allowance, self.failures, self.last_error = self.scan_bytes, 0, 0, None
                self.completed, self.completed_wall = now, _wall()
                self.last_scan_seconds = now - self.started
                self.next = now + self.interval
                self._remember()
            self.pending = None
        if self.pending is None and now >= self.next:
            self.scan_bytes, self.started = written, now
            self.pending = self.pool.submit(self._scan)
        value = dict(self.value, scan_seconds=self.last_scan_seconds, scan_pending=self.pending is not None,
                     scan_age_seconds=now - self.completed, scan_interval_seconds=self.interval,
                     scan_stalled=self.pending is not None and now - self.started > self.STALL_SECONDS,
                     scan_failures=self.failures, scan_error=self.last_error)
        growth = max(0, written - self.baseline) + self.allowance
        value['uncompressed_growth_allowance'] = growth
        value['free_bytes'] = shutil.disk_usage(self.root).free
        oldest = value.get('oldest_mtime')
        value['blocked_reason'] = (
            'recording_budget_reached' if value['bytes'] + growth + 32*1024**2 > self.config['max_bytes'] else
            'disk_free_reserve' if value['free_bytes'] < self.config['reserve_bytes'] + 32*1024**2 else
            'retention_review_due' if oldest is not None and _wall() - oldest > self.config['review_after_days']*86400 else
            'capacity_check_stale' if now - self.completed > self.stale_after else None)
        return value

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


class ProcessLock:
    def __init__(self,path):self.path=Path(path);self.handle=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.handle=self.path.open('a+b')
        if self.handle.tell()==0:self.handle.write(b'0');self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.handle.close();raise RuntimeError('Recorder already running')
        return self
    def __exit__(self,*args):self.handle.close()


def load_context(config):
    from dotenv import load_dotenv
    backend=Path(config['backend_root']).resolve()
    load_dotenv(backend/'.env',override=True);sys.path.insert(0,str(backend))
    for name in ('TOPSIGNAL_LIVE_EXECUTION_ENABLED','TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION'):
        if os.getenv(name)!='false':raise RuntimeError('Paper-only environment required')
    from app.db import SessionLocal,engine
    from app.services.projectx_credentials import get_projectx_credentials
    from app.services.probabilistic_shadow import scope_hash,cache_root
    from app.services.depth_capture import ReadOnlyMarketClient
    from sqlalchemy import text
    with SessionLocal() as db:
        if engine.dialect.name!='postgresql':raise RuntimeError('Host credential lookup requires read-only PostgreSQL')
        db.execute(text('SET TRANSACTION READ ONLY'))
        row=db.execute(text('select user_id,execution_mode,contract_id from bot_configs where id=:id'),{'id':config['bot_config_id']}).mappings().one()
        if row['execution_mode']!='dry_run' or not row['contract_id'].startswith('CON.F.US.MNQ.'):
            raise RuntimeError('Paper MNQ configuration required')
        owner=scope_hash(str(row['user_id']));credentials=get_projectx_credentials(db,user_id=str(row['user_id']))
    root=cache_root().resolve()/'depth-recordings-v1'
    if root!=Path(config['recording_root']).resolve():raise RuntimeError('Recording root must be under configured filesystem cache')
    base=next(os.getenv(k) for k in ('PROJECTX_API_BASE_URL','PROJECTX_BASE_URL','PROJECTX_GATEWAY_URL','TOPSTEP_API_BASE_URL','TOPSTEPX_API_BASE_URL') if os.getenv(k))
    client=ReadOnlyMarketClient(base_url=base,username=credentials.username,api_key=credentials.api_key,timeout_seconds=15)
    engine.dispose()
    return client,owner,root


async def record(config,client,owner,root,*,seconds=0):
    from app.services.depth_capture import active_mnq
    from app.services.projectx_client import validate_projectx_url,ProjectXClientError
    from app.services.projectx_hubs import _open_hub,_append_query,_signalr_handshake
    runtime=Path(config['runtime_root']);status_path=runtime/'status.json';stop=runtime/'STOP'
    started=time.monotonic();recorder=None;contract=None;connections=0;errors=0;failure_streak=0;last_error=None;acks={};connection_at=None;last_frame_at=None
    storage={};last_status=0.;state='starting';reason=None;capacity=None
    def written_bytes():
        if recorder is None:return 0
        if hasattr(recorder, 'totals'):return recorder.raw.raw_bytes + recorder.totals.raw_bytes
        return recorder.raw_bytes
    def stopped():return stop.exists() or (seconds>0 and time.monotonic()-started>=seconds)
    def checkpoint(force=False):
        nonlocal last_status,storage
        now=time.monotonic()
        storage=capacity.check(written_bytes())
        if force or now-last_status>=5:
            atomic_json(status_path,dict(schema='mnq-depth-recorder-status-v1',at=utc(),pid=os.getpid(),state=state,reason=reason,
                started_at=begin,contract_id=contract,data_live=False,paper_only=True,broker_mutation_calls=0,
                connections=connections,reconnects=max(0,connections-1),errors=errors,last_error=last_error,
                subscription_acknowledgements=acks,connection_started_at=connection_at,last_frame_at=last_frame_at,storage=storage,
                recording=recorder.status() if recorder else None))
            recording = recorder.status() if recorder else {}
            totals = recorder.totals.status() if recorder and hasattr(recorder, 'totals') else {}
            atomic_json(root/'recording-health.json',dict(owner_hash=owner,at=utc(),state=state,reason=reason,
                last_write_at=recording.get('last_durable_at'),last_event_at=recording.get('last_event_at'),
                last_totals_write_at=totals.get('last_durable_at'),
                recorded_measurements=totals.get('received_events'),durable_measurement_index=totals.get('durable_event_index'),
                pending_measurements=totals.get('received_events',0)-totals.get('durable_event_index',0),
                pending_events=recording.get('received_events',0)-recording.get('durable_event_index',0),
                rejected_events=recording.get('counts',{}).get('gap',0),dropped_measurements=None,
                loss_accounting='Network omissions and process-crash losses are unknown; rejected envelopes are counted.',
                reconnects=max(0,connections-1),last_error=last_error,capacity_scan_seconds=storage.get('scan_seconds'),
                capacity_scan_age_seconds=storage.get('scan_age_seconds'),capacity_scan_stalled=storage.get('scan_stalled'),
                capacity_scan_failures=storage.get('scan_failures'),storage_blocked_reason=storage.get('blocked_reason'),
                recorded_bytes=storage.get('bytes'),disk_free_bytes=storage.get('free_bytes'),
                pid=os.getpid(),started_at=begin,connection_started_at=connection_at))
            last_status=now
    begin=utc()
    def capacity_check():
        nonlocal storage
        storage=capacity.check(written_bytes(), opening=True)
        if storage['blocked_reason']:raise StorageLimit(storage['blocked_reason'])
    try:
        capacity=CapacityMonitor(root,config,cache=runtime/'capacity-cache.json')
        checkpoint(True)
        if storage['blocked_reason']:raise StorageLimit(storage['blocked_reason'])
        recorder=SegmentRecorder(root,owner,segment_seconds=config['segment_seconds'],before_segment=capacity_check)
        if config.get('record_contract_totals', False):
            if __package__:
                from .depth_totals import TotalsRecorder
            else:
                from depth_totals import TotalsRecorder
            recorder=TotalsRecorder(recorder,before_segment=capacity_check)
        while not stopped():
            checkpoint()
            if storage['blocked_reason']:raise StorageLimit(storage['blocked_reason'])
            try:
                rows=await asyncio.to_thread(client.search_contracts,search_text='MNQ',live=False)
                selected=active_mnq(rows)
                if contract and selected!=contract:recorder.append(contract,'roll',{'next_contract':selected})
                contract=selected
                token=await asyncio.to_thread(client.get_access_token)
                hub=validate_projectx_url(os.getenv('PROJECTX_MARKET_HUB_URL') or 'https://rtc.topstepx.com/hubs/market',websocket=True)
                url=_append_query(hub,{'access_token':token})
                async with _open_hub(url,open_timeout=10,close_timeout=2,max_size=1048576,max_queue=64) as socket:
                    pending=await _signalr_handshake(socket)
                    connections+=1;connection_at=utc();acks={};state='subscribing';reason=None
                    recorder.append(contract,'connect',{'connection':connections,'source':'projectx','snapshot_completion_verified':False})
                    targets=('SubscribeContractQuotes','SubscribeContractTrades','SubscribeContractMarketDepth')
                    for i,target in enumerate(targets):
                        await socket.send(json.dumps({'type':1,'invocationId':str(i),'target':target,'arguments':[contract]})+'\x1e')
                    connected=time.monotonic();last_frame=connected;last_frame_at=None
                    next_ping=connected+10;next_roll=connected+300
                    checkpoint(True)
                    while not stopped():
                        recorder.tick();checkpoint()
                        if storage['blocked_reason']:raise StorageLimit(storage['blocked_reason'])
                        if len(acks)<len(targets) and time.monotonic()-connected>=SUBSCRIPTION_TIMEOUT_SECONDS:
                            raise TimeoutError('Subscription acknowledgement timeout')
                        # Server heartbeats keep a closed/quiet market distinct from
                        # a silently stalled connection. No price event is required.
                        if time.monotonic()-last_frame>=TRANSPORT_TIMEOUT_SECONDS:
                            raise TimeoutError('Transport heartbeat timeout')
                        if time.monotonic()>=next_ping:
                            await socket.send('{"type":6}\x1e');next_ping=time.monotonic()+10
                        if time.monotonic()>=next_roll:
                            rows=await asyncio.to_thread(client.search_contracts,search_text='MNQ',live=False)
                            next_roll=time.monotonic()+300
                            if active_mnq(rows)!=contract:reason='active_contract_changed';break
                        if pending:
                            frames=pending;pending=[]
                        else:
                            try:raw=await asyncio.wait_for(socket.recv(),timeout=1.)
                            except asyncio.TimeoutError:continue
                            if isinstance(raw,bytes):raw=raw.decode('utf-8')
                            try:frames=[json.loads(s) for s in raw.split('\x1e') if s]
                            except (ValueError,TypeError):
                                recorder.append(contract,'gap',{'reason':'malformed_frame'});raise ConnectionError('Malformed frame')
                        received=utc();mono=time.monotonic_ns();last_frame=time.monotonic();last_frame_at=received
                        if state=='recording' and time.monotonic()-connected>=30:failure_streak=0
                        for frame in frames:
                            if frame.get('type')==3:
                                ident=frame.get('invocationId')
                                if ident in {'0','1','2'}:
                                    acks[targets[int(ident)]]='rejected' if frame.get('error') else 'accepted'
                                    if frame.get('error'):raise PermissionError('Market subscription rejected')
                                    if len(acks)==len(targets):state='recording'
                                continue
                            if frame.get('type')==7:raise ConnectionError('Provider closed stream')
                            kind={'GatewayDepth':'depth','GatewayQuote':'quote','GatewayTrade':'trade'}.get(frame.get('target'))
                            if not kind:continue
                            args=frame.get('arguments',[])
                            if len(args)!=2 or args[0]!=contract:
                                recorder.append(contract,'gap',{'reason':'unexpected_market_envelope'});continue
                            entries=args[1] if isinstance(args[1],list) else [args[1]]
                            for payload in entries:
                                if not isinstance(payload,dict):
                                    recorder.append(contract,'gap',{'reason':'unrecognized_payload','raw':payload});continue
                                try:recorder.append(contract,kind,payload,received_at=received,monotonic_ns=mono)
                                except ValueError:
                                    recorder.append(contract,'gap',{'reason':'invalid_or_oversized_payload'});raise ConnectionError('Invalid event')
                    recorder.append(contract,'disconnect',{'reason':reason or 'collector_stop'})
                    recorder.seal()
            except StorageLimit:raise
            except Exception as exc:
                # Exception strings may contain bearer tokens: persist type/code only.
                errors+=1;failure_streak+=1;last_error=dict(at=utc(),type=type(exc).__name__,status_code=getattr(exc,'status_code',None))
                if isinstance(exc,OSError) and getattr(exc,'errno',None) in {13,28,30}:raise
                state='reconnecting';reason='provider_or_transport_error'
                if contract:recorder.append(contract,'disconnect',{'reason':type(exc).__name__})
                recorder.seal();checkpoint(True)
                # Re-authenticate on the next supervised process start for credentials/auth errors.
                if isinstance(exc,PermissionError) or getattr(exc,'status_code',None) in {401,403}:raise
                for _ in range(min(60,2**min(failure_streak,6))):
                    if stopped():break
                    checkpoint();await asyncio.sleep(1)
        state='stopped';reason='stop_file' if stop.exists() else 'bounded_verification_complete'
    except StorageLimit as exc:
        state='storage_blocked';reason=str(exc)
    except BaseException as exc:
        state='failed';reason=type(exc).__name__
        raise
    finally:
        if recorder:
            if contract:
                try:recorder.append(contract,'end',{'reason':reason or 'process_end'})
                except StorageLimit:pass
            recorder.close()
        if capacity:
            try:checkpoint(True)
            finally:capacity.close()
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--verify',type=Path)
    parser.add_argument('--seconds',type=int,default=0)
    args=parser.parse_args()
    if args.verify:print(json.dumps(verify_segments(args.verify)));return
    if not args.config:parser.error('--config required')
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    if not (32*1024**2<=config['max_bytes']<=50*1024**3 and config['reserve_bytes']>=5*1024**3
            and 1<=config['review_after_days']<=30 and 1<=config['segment_seconds']<=300 and args.seconds>=0):
        raise ValueError('Invalid bounds')
    runtime=Path(config['runtime_root']);runtime.mkdir(parents=True,exist_ok=True)
    with ProcessLock(runtime/'recorder.lock'):
        if (runtime/'STOP').exists():return
        try:
            client,owner,root=load_context(config)
            asyncio.run(record(config,client,owner,root,seconds=args.seconds))
        except BaseException as exc:
            path=runtime/'status.json'
            prior=json.loads(path.read_text()) if path.exists() else {}
            atomic_json(path,dict(prior,at=utc(),state='failed',reason=type(exc).__name__,paper_only=True,broker_mutation_calls=0))
            raise SystemExit(1) from None


if __name__=='__main__':main()
