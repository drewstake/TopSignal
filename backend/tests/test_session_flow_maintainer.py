"""No broker calls; exercise restart recovery against an isolated loopback API."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs

import pytest

from tools import maintain_session_flow as helper


def status(path, **changes):
    result = dict(at=helper.utc(), state='recording', storage=dict(
        free_bytes=100*1024**3, blocked_reason=None, scan_stalled=False), recording={})
    result.update(changes)
    path.write_text(json.dumps(result))
    return path


def flow(contract='CON.F.US.MNQ.Z26', **changes):
    result = dict(schema='session-provider-flow-v1', contract_id=contract, data_live=False,
        generated_at=helper.utc(), through=helper.utc(), coverage=dict(
            status='observed', pending_segments=0, stale=False, reasons={}))
    result.update(changes)
    return result


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.contract = 'CON.F.US.MNQ.Z26'
        self.result = flow()

    def check_ready(self):
        self.calls.append('ready')

    def current_contract(self):
        self.calls.append('scope')
        return self.contract

    def flow(self, contract):
        self.calls.append('flow')
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize('change', [dict(state='stopped'), dict(at='invalid'),
    dict(at='2000-01-01T00:00:00+00:00'), dict(storage={}), dict(recording=None),
    dict(storage=dict(free_bytes=100*1024**3, scan_stalled=True)),
    dict(storage=dict(free_bytes=100*1024**3, blocked_reason='capacity')),
    dict(storage=dict(free_bytes=1))])
def test_recorder_priority_prevents_even_auth_requests(tmp_path, change):
    api = FakeAPI()
    worker = helper.Maintainer(api, status(tmp_path/'recorder.json', **change))
    result, delay = worker.step()
    assert result['reason'] == 'recorder_priority' and delay == 60
    assert api.calls == []


def test_recorder_guard_rechecks_after_network_discovery(tmp_path):
    path = status(tmp_path/'recorder.json')
    api = FakeAPI()
    def scope():
        status(path, state='stopped')
        return api.contract
    api.current_contract = scope
    result, _ = helper.Maintainer(api, path).step()
    assert result['reason'] == 'recorder_priority'
    assert 'flow' not in api.calls


def test_missing_malformed_and_future_status_are_not_healthy(tmp_path):
    path = tmp_path/'status.json'
    assert not helper.recorder_ready(path)
    path.write_text('{')
    assert not helper.recorder_ready(path)
    status(path)
    assert not helper.recorder_ready(path, now=time.time()-120)


def test_warmup_budget_cools_down_then_resumes_and_resets_on_success(tmp_path):
    api = FakeAPI()
    api.result = helper.Unavailable('http_503', 5)
    worker = helper.Maintainer(api, status(tmp_path/'r.json'))
    for _ in range(119):
        assert worker.step()[1] == 5
    result, delay = worker.step()
    assert result['state'] == 'cooldown' and delay == 300
    api.result = flow()
    result, delay = worker.step()
    assert result['state'] == 'current' and delay == 60
    assert worker.warming == 0 and worker.successes == 1


@pytest.mark.parametrize('bad', [flow(data_live=True), flow(contract='CON.F.US.MNQ.H27'),
    flow(generated_at='2000-01-01T00:00:00+00:00'), flow(coverage={}),
    flow(coverage=dict(status='observed', pending_segments=-1, stale=False)),
    flow(coverage=dict(status='made_up', pending_segments=0, stale=False))])
def test_wrong_scope_stale_and_invalid_data_never_become_current(tmp_path, bad):
    api = FakeAPI()
    api.result = bad
    result, delay = helper.Maintainer(api, status(tmp_path/'r.json')).step()
    assert result['state'] == 'waiting' and result['successes'] == 0 and delay == 60


def test_partial_coverage_preserved_and_closed_session_does_not_spin(tmp_path):
    api = FakeAPI()
    api.result = flow(coverage=dict(status='partial', pending_segments=0, stale=False,
                                   reasons={'receipt_silence': 1}))
    worker = helper.Maintainer(api, status(tmp_path/'r.json'))
    result, delay = worker.step()
    assert result['coverage']['reasons'] == {'receipt_silence': 1}
    assert result['state'] == 'current' and delay == 60
    api.result['coverage']['stale'] = True
    result, delay = worker.step()
    assert result['state'] == 'stale' and delay == 60


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


@pytest.fixture
def api_server():
    state = dict(tokens=set(), requests=[], ready=True, healthy=True, account=7,
                 contract='CON.F.US.MNQ.Z26', pending=2, starts=0, flow_calls=0,
                 redirect=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.handle_api()

        def do_GET(self):
            self.handle_api()

        def handle_api(self):
            path = urlsplit(self.path).path
            state['requests'].append((self.command, path))
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            code, body = 200, {}
            if state['redirect']:
                self.send_response(302)
                self.send_header('Location', state['redirect'])
                self.end_headers()
                return
            if path == '/ready':
                body = {'status': 'ready' if state['ready'] else 'starting'}
            elif path == '/health/worker':
                body = {'healthy': state['healthy']}
            elif path == '/auth/local/device' and self.command == 'POST':
                state['starts'] += 1
                token = f'session-{state["starts"]}'
                state['tokens'].add(token)
                body = {'access_token': token, 'expires_at': time.time()+3600}
            elif token not in state['tokens']:
                code = 401
            elif path == '/auth/local/logout' and self.command == 'POST':
                state['tokens'].remove(token)
            elif path == '/api/bots':
                # Disabled configuration deliberately proves independence from arm state.
                body = {'items': [dict(id=40, account_id=state['account'],
                    contract_id=state['contract'], enabled=False, desired_state='stopped')]}
            elif path == '/api/accounts/7/session-order-flow' and self.command == 'GET':
                state['flow_calls'] += 1
                assert parse_qs(urlsplit(self.path).query)['data_live'] == ['false']
                if state['pending']:
                    state['pending'] -= 1
                    code, body = 503, {'detail': 'preparing'}
                else:
                    body = flow(state['contract'])
            else:
                code = 404
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join()


def test_automatic_restart_recovery_auth_renewal_and_contract_roll(tmp_path, api_server):
    state, port = api_server
    api = helper.LocalAPI(7, 40, port)
    worker = helper.Maintainer(api, status(tmp_path/'r.json'))
    try:
        assert worker.step()[0]['reason'] == 'http_503'
        assert worker.step()[0]['reason'] == 'http_503'
        assert worker.step()[0]['state'] == 'current'
        assert state['starts'] == 1  # Token reused, never persisted.
        # API outage/restart: readiness unavailable, then cold cache and revoked session.
        state.update(ready=False, pending=1)
        state['tokens'].clear()
        assert worker.step()[0]['reason'] == 'api_not_ready'
        state['ready'] = True
        assert worker.step()[0]['reason'] == 'http_401'
        assert worker.step()[0]['reason'] == 'http_503'
        assert worker.step()[0]['state'] == 'current'
        assert state['starts'] == 2
        state['contract'] = 'CON.F.US.MNQ.H27'
        result, _ = worker.step()
        assert result['state'] == 'current' and result['contract_id'] == state['contract']
        # Recreate the helper itself (scheduler restart), no persisted auth needed.
        api.close()
        fresh_api = helper.LocalAPI(7, 40, port)
        try:
            fresh = helper.Maintainer(fresh_api, tmp_path/'r.json')
            assert fresh.step()[0]['state'] == 'current'
        finally:
            fresh_api.close()
        assert not state['tokens']
        assert all(method == 'GET' or path in ('/auth/local/device', '/auth/local/logout')
                   for method, path in state['requests'])
        assert not any('/start' in p or '/stop' in p or '/orders' in p for _, p in state['requests'])
    finally:
        api.close()


def test_account_change_never_falls_back_and_redirect_never_forwards_token(tmp_path, api_server):
    state, port = api_server
    api = helper.LocalAPI(7, 40, port)
    worker = helper.Maintainer(api, status(tmp_path/'r.json'))
    try:
        state['account'] = 8
        assert worker.step()[0]['reason'] == 'configured_account_unavailable'
        assert state['flow_calls'] == 0
        state['redirect'] = '/should-not-follow'
        assert worker.step()[0]['reason'] == 'http_302'
        assert not any(p == '/should-not-follow' for _, p in state['requests'])
        state['redirect'] = None
    finally:
        api.close()


def test_status_is_bounded_and_does_not_contain_auth(tmp_path):
    api = FakeAPI()
    worker = helper.Maintainer(api, status(tmp_path/'r.json'))
    path = tmp_path/'status.json'
    for _ in range(10):
        result, _ = worker.step()
        helper.publish(path, result)
    result = json.loads(path.read_text())
    assert result['successes'] == 10
    assert path.stat().st_size < 4096
    assert 'token' not in path.read_text()
    assert not path.with_suffix('.pending').exists()


def test_api_rejects_trading_writes_even_if_called_accidentally():
    api = helper.LocalAPI(7, 40)
    with pytest.raises(ValueError):
        api._request('/api/bots/40/start', {})
    with pytest.raises(ValueError):
        api._request('/api/bots', {})


def test_real_helper_process_starts_publishes_and_revokes_own_session(tmp_path, api_server):
    state, port = api_server
    state['pending'] = 0
    recorder = status(tmp_path/'recorder.json')
    published = tmp_path/'status.json'
    config = tmp_path/'config.json'
    config.write_text(json.dumps(dict(account_id=7, bot_config_id=40, api_port=port,
        recorder_status=str(recorder), status_path=str(published))))
    command = [sys.executable, '-B', str(Path(helper.__file__).resolve()), '--config', str(config), '--once']
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert json.loads(published.read_text())['state'] == 'current'
        assert state['tokens'] == set()
        assert not result.stdout  # No access tokens or service payloads in stdout.
    starts = state['starts']
    (tmp_path/'STOP').touch()
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert state['starts'] == starts
