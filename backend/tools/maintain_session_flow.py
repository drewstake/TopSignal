"""Keep session order flow warm through the existing loopback API.

Independent, optional host helper. No database, broker, bot lifecycle, raw archive
or application imports. Only auth POSTs and allowlisted read-only GETs are used.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


MAX_RESPONSE = 1024 * 1024
CONTRACT = re.compile(r'CON\.F\.US\.MNQ\.[A-Z0-9]{1,12}')


class Unavailable(Exception):
    def __init__(self, reason, retry=60):
        super().__init__(reason)
        self.reason, self.retry = reason, retry


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a local device session to another origin.


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('Oversized local JSON')
    return json.loads(raw.decode('utf-8-sig'))


def publish(path, state):
    path = Path(path)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(state, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


class LocalAPI:
    def __init__(self, account, config_id, port=8000):
        if any(type(v) is not int or v <= 0 for v in (account, config_id, port)) or port > 65535:
            raise ValueError('Positive account/config IDs and a valid local port required')
        self.account, self.config_id = account, config_id
        self.base = f'http://127.0.0.1:{port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.token, self.expires = None, 0

    def _request(self, path, body=None):
        allowed = {'/ready', '/health/worker?require_enabled=true', '/api/bots',
                   '/auth/local/device', '/auth/local/logout'}
        flow = f'/api/accounts/{self.account}/session-order-flow?'
        if path not in allowed and not path.startswith(flow):
            raise ValueError('Endpoint not allowed')
        if (body is not None) != (path in {'/auth/local/device', '/auth/local/logout'}):
            raise ValueError('Only local session auth may use POST')
        headers = {'Origin': 'http://127.0.0.1:4173', 'X-TopSignal-Local-Access': 'device',
                   'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(self.base + path, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(req, timeout=25 if path.startswith(flow) else 5) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise Unavailable('response_too_large')
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                self.token, self.expires = None, 0
            retry = 5 if exc.code == 503 else 60
            if exc.code == 429:
                retry = 300
            raise Unavailable(f'http_{exc.code}', retry) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise Unavailable('api_unavailable') from None

    def authenticate(self):
        if self.token and self.expires > time.time() + 60:
            return
        self.close()
        result = self._request('/auth/local/device', {})
        token, expires = result.get('access_token'), result.get('expires_at')
        if (not isinstance(token, str) or not token or type(expires) not in (int, float)
                or not math.isfinite(expires) or expires <= time.time() + 60):
            raise Unavailable('invalid_auth_response')
        self.token, self.expires = token, expires

    def close(self):
        if self.token:
            try:
                self._request('/auth/local/logout', {})
            except Unavailable:
                pass  # Tokens are memory-only and expire on the server.
        self.token, self.expires = None, 0

    def check_ready(self):
        if self._request('/ready').get('status') != 'ready':
            raise Unavailable('api_not_ready')
        if self._request('/health/worker?require_enabled=true').get('healthy') is not True:
            raise Unavailable('worker_not_healthy')

    def current_contract(self):
        self.authenticate()
        configs = self._request('/api/bots').get('items', [])
        matches = [c for c in configs if c.get('id') == self.config_id]
        if len(matches) != 1 or matches[0].get('account_id') != self.account:
            raise Unavailable('configured_account_unavailable')
        contract = matches[0].get('contract_id', '')
        if not isinstance(contract, str) or not CONTRACT.fullmatch(contract):
            raise Unavailable('configured_contract_unavailable')
        # Contract discovery does not depend on enabled/armed/selected-account state.
        return contract

    def flow(self, contract):
        query = urllib.parse.urlencode({'contract_id': contract, 'data_live': 'false'})
        return self._request(f'/api/accounts/{self.account}/session-order-flow?{query}')


def recorder_ready(path, now=None):
    """Fail closed on missing/stale status and pressure; never scan the archive."""
    now = time.time() if now is None else now
    try:
        s = read_json(path)
        age = now - datetime.fromisoformat(s['at']).timestamp()
        storage = s['storage']
        if (s['state'] != 'recording' or not 0 <= age <= 30
                or storage.get('blocked_reason') or storage.get('scan_stalled')):
            return False
        free = storage['free_bytes']
        if type(free) is not int or free < 15 * 1024**3 or not isinstance(s['recording'], dict):
            return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


class Maintainer:
    def __init__(self, api, recorder_status):
        self.api, self.recorder_status = api, recorder_status
        self.attempts, self.successes, self.warming = 0, 0, 0
        self.last_success = None

    def step(self):
        state = {'at': utc(), 'pid': os.getpid(), 'attempts': self.attempts,
                 'successes': self.successes, 'last_success_at': self.last_success}
        try:
            if not recorder_ready(self.recorder_status):
                raise Unavailable('recorder_priority')
            self.api.check_ready()
            contract = self.api.current_contract()
            # Authentication / scope discovery can take time. Recheck immediately
            # before starting the bounded optional read.
            if not recorder_ready(self.recorder_status):
                raise Unavailable('recorder_priority')
            self.attempts += 1
            started = time.monotonic()
            result = self.api.flow(contract)
            if (result.get('schema') != 'session-provider-flow-v1'
                    or result.get('contract_id') != contract or result.get('data_live') is not False):
                raise Unavailable('flow_scope_mismatch')
            coverage = result['coverage']
            # Do not promote old cached data after a restart or clock change.
            age = time.time() - datetime.fromisoformat(result['generated_at']).timestamp()
            if not 0 <= age <= 120:
                raise Unavailable('stale_response')
            pending = coverage['pending_segments']
            if (type(pending) is not int or pending < 0 or type(coverage['stale']) is not bool
                    or coverage['status'] not in {'building', 'partial', 'observed'}):
                raise Unavailable('invalid_coverage')
            building = coverage['status'] == 'building' or pending > 0
            state.update(state='warming' if building else 'stale' if coverage['stale'] else 'current',
                         contract_id=contract, through=result['through'], coverage=coverage,
                         request_seconds=round(time.monotonic()-started, 3))
            if not building and not coverage['stale']:
                self.warming = 0
                self.successes += 1
                self.last_success = utc()
                delay = 60
            elif building:
                self.warming += 1
                delay = 5
            else:
                delay = 60  # Market closure or missing source: do not spin.
        except Unavailable as exc:
            state.update(state='waiting', reason=exc.reason)
            delay = exc.retry
            if delay == 5:
                self.warming += 1
        except (ValueError, KeyError, TypeError, AttributeError):
            state.update(state='waiting', reason='invalid_response')
            delay = 60
        if self.warming >= 120:
            state.update(state='cooldown', reason='warmup_budget')
            delay, self.warming = 300, 0
        state.update(attempts=self.attempts, successes=self.successes,
                     last_success_at=self.last_success, retry_seconds=delay)
        return state, delay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = read_json(args.config)
    api = LocalAPI(config['account_id'], config['bot_config_id'], config.get('api_port', 8000))
    maintainer = Maintainer(api, config['recorder_status'])
    status = Path(config['status_path'])
    stop_file = status.parent / 'STOP'
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set() and not stop_file.exists():
            state, delay = maintainer.step()
            publish(status, state)
            if args.once:
                break
            # Inspect STOP every second, without opening status/raw recordings.
            for _ in range(delay):
                if stop.wait(1) or stop_file.exists():
                    break
    finally:
        api.close()


if __name__ == '__main__':
    main()
