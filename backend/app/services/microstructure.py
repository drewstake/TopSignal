"""Isolated local research jobs and immutable notebook records.

All identities and source paths come from the authenticated server context.
No application database connection or brokerage credential enters this module.
"""
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from threading import BoundedSemaphore, Event, RLock, Thread
import time
from uuid import uuid4

from tools.depth_recording import atomic_json, canonical
from tools.read_depth_history import safe_path
from tools.read_depth_research import read_research
from .depth_history import history_source_path
from .microstructure_models import ResearchRequest
from .microstructure_study import run_study, ENGINE_VERSION
from .probabilistic_shadow import cache_root, scope_hash

BUDGET = 512 * 1024**2
RESERVE = 5 * 1024**3
MAX_RESULT = 24 * 1024**2
MAX_FILES = 5000
INSTANCE = uuid4().hex
_lock = RLock()
_slots = BoundedSemaphore(2)
_active = {}
logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def root():
    path = cache_root() / 'microstructure-research-v1'
    safe_path(path, cache_root())
    return path


def owner_root(user_id, account_id):
    if type(account_id) is not int or account_id <= 0:
        raise ValueError('Invalid account')
    path = root() / scope_hash(str(user_id)) / str(account_id)
    safe_path(path, root())
    return path


def path_for(user_id, account_id, kind, identity, suffix='.json'):
    if kind not in {'jobs', 'results', 'experiments', 'bookmarks'} or not re.fullmatch('[a-f0-9]{32}', identity):
        raise ValueError('Invalid artifact ID')
    path = owner_root(user_id, account_id) / kind / (identity+suffix)
    return safe_path(path, root())


def storage():
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    used = count = 0
    oldest = time.time()
    for path in base.rglob('*'):
        safe_path(path, base)
        if path.is_file():
            count += 1
            if count > MAX_FILES:
                raise ValueError('Research inventory requires review (5,000 files)')
            stat = path.stat()
            used += stat.st_size
            oldest = min(oldest, stat.st_mtime)
    free = shutil.disk_usage(base).free
    reason = 'research_budget' if used+MAX_RESULT > BUDGET else 'free_space_reserve' if free < RESERVE+MAX_RESULT else '30_day_review' if time.time()-oldest > 30*86400 else None
    return dict(bytes=used, files=count, budget_bytes=BUDGET, reserve_bytes=RESERVE, free_bytes=free,
                blocked_reason=reason, review_after_days=30, automatic_deletion=False)


def write(path, value, *, immutable=False):
    with _lock:
        encoded = canonical(value)
        if len(encoded) > MAX_RESULT:
            raise ValueError('Research artifact exceeds 24 MiB')
        safe_path(path, root())
        limits = storage()
        if limits['blocked_reason']:
            raise ValueError('Research storage refused: '+limits['blocked_reason'])
        if immutable and path.exists():
            raise ValueError('Research versions are immutable')
        atomic_json(path, value)


def read(path):
    # Windows denies atomic replace while another thread has an open reader.
    with _lock:
        safe_path(path, root())
        if not path.is_file():
            raise FileNotFoundError('Research artifact not found')
        if path.stat().st_size > MAX_RESULT:
            raise ValueError('Oversized research artifact')
        value = json.loads(path.read_text(encoding='utf-8'))
        if path.parent.name == 'experiments':
            digest = value.get('version_digest')
            if not digest or hashlib.sha256(canonical({k:v for k,v in value.items() if k != 'version_digest'})).hexdigest() != digest:
                raise ValueError('Experiment version checksum mismatch')
        return value


def source_read(user_id, query, cancel=None):
    cancel = cancel or Event()
    query = dict(query, owner_hash=scope_hash(str(user_id)))
    config_path = history_source_path()
    if not config_path.exists():
        return dict(read_research(cache_root()/'depth-recordings-v1', query, cancelled=cancel.is_set), source='This backend')
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    for key in ('python_path', 'research_reader_path', 'research_recording_root'):
        value = config.get(key, '')
        if not re.fullmatch(r'[A-Za-z]:/[A-Za-z0-9_./-]+', value) or '..' in value:
            raise ValueError('Research reader is not installed in the server-owned source configuration')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+', config['ssh_target']):
        raise ValueError('Invalid server-owned source configuration')
    args = ['ssh', '-T', '-i', config['identity_file'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=5', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', config['ssh_target'],
            config['python_path'], '-B', config['research_reader_path'], '--root', config['research_recording_root']]
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout:
        stdin.write(canonical(query)); stdin.seek(0)
        process = subprocess.Popen(args, stdin=stdin, stdout=stdout, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.wait(.1):
                    raise InterruptedError('Research cancelled')
                if time.monotonic()-started > 120:
                    raise ValueError('Host read timed out; choose a shorter window')
                if os.fstat(stdout.fileno()).st_size > MAX_RESULT:
                    raise ValueError('Host response exceeds 24 MiB; shorten the window')
            stdout.seek(0)
            content = stdout.read(MAX_RESULT+1)
            if len(content) > MAX_RESULT:
                raise ValueError('Host response exceeds 24 MiB')
            try:
                result = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError('Private recording host is unavailable') from None
            if process.returncode or result.get('error'):
                raise ValueError(str(result.get('error', 'Private recording host is unavailable'))[:200])
            if len(result.get('points', [])) > 3000 or len(result.get('frames', [])) > 1200:
                raise ValueError('Host response exceeds sample bounds')
            return dict(result, source=str(config.get('label', 'Recording host'))[:80])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def code_version():
    backend = Path(__file__).resolve().parents[2]
    names = ['tools/depth_features.py', 'tools/depth_totals.py', 'tools/read_depth_research.py', 'tools/depth_book.py',
             'app/services/microstructure.py', 'app/services/microstructure_models.py', 'app/services/microstructure_study.py',
             'app/services/projectx_order_book.py']
    return hashlib.sha256(b''.join((backend/p).read_bytes() for p in names)).hexdigest()


def list_artifacts(user_id, account_id):
    base = owner_root(user_id, account_id)
    result = {}
    for kind in ('jobs', 'experiments', 'bookmarks'):
        entries = []
        for path in (base/kind).glob('*.json'):
            value = read(path)
            if kind == 'jobs':
                if value['state'] in {'queued', 'running', 'cancelling'} and value.get('instance') != INSTANCE:
                    value = dict(value, state='interrupted', message='Backend restarted; submit a new reproducible run.')
                value = {k: value[k] for k in ('id', 'state', 'created_at', 'kind', 'message', 'progress', 'request') if k in value}
            entries.append(value)
            if len(entries) > 1500:
                raise ValueError('Notebook inventory requires review')
        result[kind] = sorted(entries, key=lambda v: v['created_at'], reverse=True)[:200]
        result[kind+'_total'] = len(entries)
    return dict(result, storage=storage(), engine_version=ENGINE_VERSION)


def get_job(user_id, account_id, identity, *, include_result=False):
    value = read(path_for(user_id, account_id, 'jobs', identity))
    if value['state'] in {'queued', 'running', 'cancelling'} and value.get('instance') != INSTANCE:
        value = dict(value, state='interrupted', message='Backend restarted; submit a new run.')
    if include_result and value['state'] == 'complete':
        path = path_for(user_id, account_id, 'results', identity, '.json.gz')
        safe_path(path, root())
        if path.stat().st_size > MAX_RESULT:
            raise ValueError('Oversized result')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != value['result_sha256']:
            raise ValueError('Result checksum mismatch')
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            decoded = stream.read(MAX_RESULT+1)
        if len(decoded) > MAX_RESULT:
            raise ValueError('Oversized result')
        value['result'] = json.loads(decoded)
    return value


def submit(user_id, account_id, request: ResearchRequest):
    with _lock:
        lane = (str(user_id), account_id)
        if lane in [v[0] for v in _active.values()] or not _slots.acquire(False):
            raise RuntimeError('A research job is already running; wait or cancel it')
        try:
            if request.experiment_id:
                experiment = read(path_for(user_id, account_id, 'experiments', request.experiment_id))
                if request.forward:
                    if not experiment['frozen']:
                        raise ValueError('Forward observation requires a frozen version')
                    if experiment['code_version'] != code_version():
                        raise ValueError('Research code changed since freezing; fork and revalidate this experiment')
                    if any(w.start <= datetime.fromisoformat(experiment['forward_after']) for w in request.windows):
                        raise ValueError('Forward windows must begin after the freeze and all previously examined data')
                    expected = experiment['request']
                    if request.study.model_dump(mode='json') != expected['study'] or request.scope != expected['scope'] or request.tick_distances != expected['tick_distances']:
                        raise ValueError('Frozen feature, condition and execution rules cannot change')
                elif experiment['frozen']:
                    raise ValueError('Use forward observation or fork a frozen experiment')
            elif request.forward:
                raise ValueError('Choose a frozen experiment for forward observation')
            identity = uuid4().hex
            job = dict(id=identity, kind=request.kind, state='queued', progress=0, message='Queued',
                       created_at=now(), instance=INSTANCE, request=request.model_dump(mode='json'), code_version=code_version(),
                       research_only=True)
            write(path_for(user_id, account_id, 'jobs', identity), job, immutable=True)
            cancel = Event()
            _active[identity] = (lane, cancel)
            Thread(target=_run, args=(user_id, account_id, request, job, cancel), daemon=True, name='microstructure-research').start()
            return job
        except BaseException:
            _slots.release()
            raise


def _run(user_id, account_id, request, job, cancel):
    path = path_for(user_id, account_id, 'jobs', job['id'])
    def publish(**fields):
        job.update(fields)
        write(path, job)
    try:
        publish(state='running', message='Reading committed raw segments', progress=.02)
        batches = []
        for ordinal, window in enumerate(sorted(request.windows, key=lambda v: v.start)):
            if cancel.is_set():
                raise InterruptedError('Research cancelled')
            batch = source_read(user_id, dict(start=window.start.isoformat(), end=window.end.isoformat(),
                scope=request.scope, tick_distances=request.tick_distances, replay=request.kind == 'window'), cancel)
            if request.forward:
                experiment = read(path_for(user_id, account_id, 'experiments', request.experiment_id))
                if batch.get('measurement_code_hash') not in experiment['measurement_code_hashes']:
                    raise ValueError('Recording feature code changed since freezing; fork and revalidate')
            # Independently reconstructed windows never silently share book state.
            for p in batch['points']:
                p['reconstruction_id'] = f"{ordinal}:{p['reconstruction_id']}"
            batches.append(batch)
            publish(progress=.05+.8*(ordinal+1)/len(request.windows), message=f"Read {ordinal+1}/{len(request.windows)} windows")
        if cancel.is_set():
            raise InterruptedError('Research cancelled')
        if request.kind == 'window':
            result = batches[0]
        else:
            result = run_study([p for b in batches for p in b['points']], request.study, cancelled=cancel.is_set)
            result['coverage'] = [{k: v for k, v in b.items() if k not in {'points', 'frames'}} for b in batches]
            result['tested_variations_before_this_run'] = sum(1 for p in path.parent.glob('*.json') if p.stem != job['id'])
            result['confirmation'] = 'Exploratory, repeated testing is not independent confirmation.'
        result['code_version'] = job['code_version']
        result['request'] = request.model_dump(mode='json')
        result['engine_version'] = ENGINE_VERSION
        result['completed_at'] = now()
        payload = canonical(result)
        if len(payload) > MAX_RESULT:
            raise ValueError('Result exceeds 24 MiB; narrow the study')
        compressed = gzip.compress(payload, compresslevel=3, mtime=0)
        target = path_for(user_id, account_id, 'results', job['id'], '.json.gz')
        with _lock:
            if cancel.is_set():
                raise InterruptedError('Research cancelled')
            limits = storage()
            if limits['blocked_reason']:
                raise ValueError('Research storage refused: '+limits['blocked_reason'])
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.partial')
            with temporary.open('xb') as stream:
                stream.write(compressed); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
            publish(state='complete', progress=1, message='Complete', result_sha256=hashlib.sha256(compressed).hexdigest(),
                    result_bytes=len(compressed), completed_at=now())
    except InterruptedError:
        publish(state='cancelled', message='Cancelled; completed source recordings are unchanged')
    except Exception as exc:
        logger.exception('microstructure_job_failed')
        try:
            publish(state='failed', message=str(exc)[:240] if isinstance(exc, ValueError) else 'Research failed; check source availability and local storage')
        except Exception:
            # Keep a small terminal status even when optional writes hit their budget.
            job.update(state='failed', message='Research storage unavailable; review capacity')
            try:
                atomic_json(path, job)
            except OSError:
                pass
    finally:
        with _lock:
            _active.pop(job['id'], None)
            _slots.release()


def cancel_job(user_id, account_id, identity):
    value = get_job(user_id, account_id, identity)
    with _lock:
        running = _active.get(identity)
        if running and running[0] == (str(user_id), account_id):
            running[1].set()
            return dict(value, state='cancelling', message='Cancellation requested')
    return value


def save_bookmark(user_id, account_id, request):
    job = get_job(user_id, account_id, request.job_id, include_result=True)
    if job['state'] != 'complete' or job['kind'] != 'window':
        raise ValueError('Choose a completed replay window')
    point = next((p for p in job['result']['points'] if datetime.fromisoformat(p['timestamp']) == request.timestamp), None)
    if point is None:
        raise ValueError('Bookmark must reference an actual recorded measurement')
    value = dict(id=uuid4().hex, created_at=now(), note=request.note, job_id=request.job_id,
        timestamp=point['timestamp'], contract_id=point['contract_id'], capture_id=point['capture_id'], index=point['index'],
        generation=point['generation'], feature_version=point['feature_version'], data_id=job['result']['data_id'],
        scope=job['request']['scope'], settings=job['request'], code_version=job['code_version'])
    write(path_for(user_id, account_id, 'bookmarks', value['id']), value, immutable=True)
    return value


def save_experiment(user_id, account_id, request):
    job = get_job(user_id, account_id, request.job_id, include_result=True)
    if job['state'] != 'complete' or job['kind'] != 'study':
        raise ValueError('Choose a completed study')
    if request.freeze and job['code_version'] != code_version():
        raise ValueError('Re-run with the current research code before freezing')
    version = 1
    family = uuid4().hex
    if request.parent_id:
        parent = read(path_for(user_id, account_id, 'experiments', request.parent_id))
        version, family = parent['version']+1, parent['family_id']
    created = now()
    cutoff = max(datetime.fromisoformat(v) for v in [created, *[w['end'] for w in job['request']['windows']]]).astimezone(timezone.utc).isoformat()
    value = dict(id=uuid4().hex, family_id=family, version=version, parent_id=request.parent_id, created_at=created,
        hypothesis=request.hypothesis, notes=request.notes, frozen=request.freeze, forward_after=cutoff,
        request=job['request'], result_job_id=job['id'], result_sha256=job['result_sha256'], code_version=job['code_version'],
        data_versions=[c['data_id'] for c in job['result']['coverage']],
        measurement_code_hashes=sorted({c.get('measurement_code_hash', 'unavailable') for c in job['result']['coverage']}),
        headline=job['result']['tester']['metrics'], status=job['result']['status'],
        tested_variations=job['result']['tested_variations_before_this_run']+1,
        holdout='Session boundaries are declared by the researcher. All runs remain visible; previously inspected periods are not newly untouched.')
    value['version_digest'] = hashlib.sha256(canonical(value)).hexdigest()
    write(path_for(user_id, account_id, 'experiments', value['id']), value, immutable=True)
    return value


def export_result(user_id, account_id, identity):
    job = get_job(user_id, account_id, identity, include_result=True)
    if job['state'] != 'complete':
        raise ValueError('Results are not complete')
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    if job['kind'] == 'window':
        headers = ['timestamp', 'contract_id', 'provider_at', 'mid_price', 'best_bid', 'best_ask', 'spread',
                   'bid_contracts', 'ask_contracts', 'imbalance', 'bid_levels', 'ask_levels', 'best_bid_quantity', 'best_ask_quantity',
                   'bid_depth_ratio', 'ask_depth_ratio', 'volatility', 'baseline_warmup', 'quality_flags',
                   'capture_id', 'index', 'generation', 'feature_version']
        activity_fields = ['trade_count', 'traded_volume', 'trade_count_rate', 'volume_rate', 'bid_depletion_rate',
                           'bid_refill_rate', 'ask_depletion_rate', 'ask_refill_rate', 'invalid_trades']
        writer.writerow(['research_only', 'job_id', 'scope', 'data_id', *headers,
                         *[f'{name}_{seconds}s' for seconds in (1,5,15) for name in activity_fields]])
        for point in job['result']['points']:
            values = [';'.join(point[k]) if k == 'quality_flags' else point.get(k) for k in headers]
            writer.writerow([True, identity, job['request']['scope'], job['result']['data_id'], *values,
                *[point.get('activity', {}).get(str(seconds), {}).get(name) for seconds in (1,5,15) for name in activity_fields]])
        return out.getvalue()
    writer.writerow(['research_only', 'job_id', 'timestamp_utc', 'contract', 'record_type', 'horizon_seconds', 'valid',
                     'return_points', 'net_usd', 'reason', 'scope', 'code_version', 'data_version'])
    result = job['result']
    rows = result.get('episodes', []) if job['kind'] == 'study' else result['points']
    for row in rows:
        outcomes = row.get('outcomes', {'': dict(valid=not row.get('quality_flags'), reason=';'.join(row.get('quality_flags', [])))})
        for h, v in outcomes.items():
            writer.writerow([True, identity, row['timestamp'], row['contract_id'], 'episode' if h else 'measurement', h,
                v['valid'], v.get('return_points'), '', v.get('reason', ''), job['request']['scope'], job['code_version'],
                result.get('data_id', ';'.join(c['data_id'] for c in result.get('coverage', [])))])
    for trade in result.get('tester', {}).get('trades', []):
        writer.writerow([True, identity, trade['timestamp'], trade['contract_id'], 'simulated_trade', '', trade['status'], '',
            trade.get('net'), trade['reason'], job['request']['scope'], job['code_version'], ''])
    return out.getvalue()
