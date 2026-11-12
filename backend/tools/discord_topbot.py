"""Read-only TopBot trade reporter. No app imports, broker calls, or trade writes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url


def now():
    return datetime.now(timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool):
        raise ValueError('Invalid numeric trade field')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Invalid numeric trade field')
    return value


def timestamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Trade timestamp needs timezone')
    return f'<t:{int(value.timestamp())}:f>'


def payload(row):
    """Only verified fills are public; use the runner's reconciled net P&L."""
    runner = row.get('runner') or {}
    if row['execution_mode'] != 'live' or not runner.get('entry_at'):
        return None
    if not runner.get('entry_fill_id') or runner.get('stats_status') == 'ambiguous_entry_fills':
        return None
    side = {'BUY': 'LONG', 'SELL': 'SHORT'}[row['side']]
    quantity = number(runner['quantity'])
    if quantity <= 0 or quantity != number(row['size']):
        raise ValueError('Unreconciled quantity')
    closed = runner.get('stats_status') == 'complete'
    mode = ('PRACTICE' if row['provider_simulated'] is True else
            'LIVE' if row['provider_simulated'] is False else 'ACCOUNT TYPE UNVERIFIED')
    symbol = row.get('symbol') or row['contract_id']
    # Contract/symbol is displayed as plain text, never a mention or URL.
    symbol = re.sub(r'[^A-Za-z0-9. _-]', '', symbol)[:80]
    fields = [
        {'name': 'Entry', 'value': f"{number(runner['entry_price']):,.2f}", 'inline': True},
        {'name': 'Contracts', 'value': f'{quantity:g}', 'inline': True},
        {'name': 'Entered', 'value': timestamp(runner['entry_at']), 'inline': True},
    ]
    for key, label in [('confirmed_stop', 'Verified stop'), ('confirmed_target', 'Verified target')]:
        if runner.get(key) is not None:
            fields.append({'name': label, 'value': f'{number(runner[key]):,.2f}', 'inline': True})
    color = 0x3498DB
    description = 'Entry fill verified. Waiting for the completed trade result.'
    if closed:
        if not runner.get('exit_fill_id'):
            raise ValueError('Missing verified exit')
        gross, fees, net = [number(runner[k]) for k in ('gross_pnl_usd', 'fees_usd', 'net_pnl_usd')]
        if fees < 0 or abs(gross-fees-net) > 0.011:
            raise ValueError('Inconsistent trade P&L')
        color = 0x2ECC71 if net >= 0 else 0xE74C3C
        description = 'Trade complete. P&L is in USD, based on reconciled fills.'
        fields += [
            {'name': 'Exit', 'value': f"{number(runner['exit_price']):,.2f}", 'inline': True},
            {'name': 'Exited', 'value': timestamp(runner['exit_at']), 'inline': True},
            {'name': 'Gross P&L', 'value': f'${gross:+,.2f}', 'inline': True},
            {'name': 'Fees + commissions', 'value': f'${fees:,.2f}', 'inline': True},
            {'name': 'Net P&L', 'value': f'**${net:+,.2f}**', 'inline': True},
        ]
        basis = runner.get('commission_basis')
        if basis == 'configured_round_turn_schedule':
            description += ' Commission uses the configured round-turn schedule.'
        reason = re.sub(r'[^A-Za-z0-9 _-]', '', str(runner.get('exit_reason', 'verified exit'))).replace('_', ' ')[:100]
        fields.append({'name': 'Exit reason', 'value': reason or 'Verified exit', 'inline': True})
    elif runner.get('stats_status') != 'open':
        description = 'Entry fill verified; exit reconciliation is pending. Final P&L is not yet available.'
    return {'allowed_mentions': {'parse': []}, 'embeds': [{
        'title': f"TopBot | {mode} | {symbol} {side} | {'CLOSED' if closed else 'OPEN'}",
        'description': description, 'color': color, 'fields': fields,
        'footer': {'text': f"TopBot trade #{row['id']} | Automatic fill report"},
    }]}


class Source:
    def __init__(self, env_path, config_id):
        values = dotenv_values(env_path)
        url = make_url(values['DATABASE_URL'])
        if url.get_backend_name() != 'postgresql' or url.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('Reporter requires local execution PostgreSQL')
        if set(url.query) & {'host', 'hostaddr', 'service', 'servicefile', 'dbname'}:
            raise ValueError('Database routing overrides prohibited')
        if any(os.environ.get(k) for k in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE')):
            raise ValueError('Ambient database routing override')
        self.engine = create_engine(url, hide_parameters=True, pool_size=1, max_overflow=0,
                                    pool_pre_ping=True, connect_args={'connect_timeout': 10})
        self.config_id = int(config_id)

    @contextmanager
    def connection(self):
        with self.engine.connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            yield connection
            connection.rollback()

    def rows(self, after=None, ids=None, start=None, end=None):
        # Joins preserve tenant/config/account ownership. Only runner entry audits qualify.
        sql = """SELECT o.id,o.side,o.size,o.status AS order_status,o.execution_mode,c.symbol,o.contract_id,
                    a.provider_simulated, o.raw_request->'timeExit'->'runner' AS runner
                 FROM bot_order_attempts o
                 JOIN bot_configs c ON c.id=o.bot_config_id AND c.user_id=o.user_id
                 JOIN accounts a ON a.external_id=CAST(o.account_id AS text)
                    AND a.user_id=o.user_id AND a.provider=c.provider
                 WHERE c.id=:config_id AND c.strategy_type='topbot_adaptive'
                   AND o.execution_mode='live'
                   AND o.raw_request->'timeExit'->'runner' IS NOT NULL
              """
        params = {'config_id': self.config_id}
        if start is not None:
            query = text(sql + " AND CAST(o.raw_request->'timeExit'->'runner'->>'entry_at' AS timestamptz) >= :start"
                         " AND CAST(o.raw_request->'timeExit'->'runner'->>'entry_at' AS timestamptz) < :end"
                         " ORDER BY o.id LIMIT 10001")
            params.update(start=start, end=end)
        elif after is not None:
            sql += ' AND o.id > :after ORDER BY o.id LIMIT 100'
            params['after'] = int(after)
            query = text(sql)
        else:
            if not ids:
                return []
            query = text(sql+' AND o.id IN :ids ORDER BY o.id').bindparams(bindparam('ids', expanding=True))
            params['ids'] = ids
        with self.connection() as connection:
            rows = [dict(row) for row in connection.execute(query, params).mappings()]
            if start is not None and len(rows) > 10000:
                raise ValueError('Session report exceeds row limit')
            return rows

    def baseline(self):
        with self.connection() as connection:
            config = connection.execute(text("SELECT id FROM bot_configs WHERE id=:id AND strategy_type='topbot_adaptive'"), {'id': self.config_id}).scalar_one()
            maximum = connection.execute(text('SELECT COALESCE(MAX(id),0) FROM bot_order_attempts WHERE bot_config_id=:id'), {'id': config}).scalar_one()
        # Capture pending/open entries, including entries submitted just before startup.
        active = []
        cursor = 0
        while True:
            rows = self.rows(after=cursor)
            if not rows:
                break
            for row in rows:
                runner = row.get('runner') or {}
                pending = row['order_status'] in ('pending', 'submitted', 'submission_unknown')
                if row['id'] <= maximum and runner.get('stats_status') not in ('complete', 'not_filled') and (runner.get('entry_at') or pending):
                    active.append(row['id'])
            cursor = rows[-1]['id']
        return maximum, active


def state_db(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY, message_id TEXT, digest TEXT,
            status TEXT NOT NULL DEFAULT 'ready', closed INTEGER NOT NULL DEFAULT 0,
            changed REAL NOT NULL DEFAULT 0);
    ''')
    # A crash after starting POST has an unknown delivery outcome. Never blindly duplicate it.
    db.execute("UPDATE deliveries SET status='uncertain' WHERE status='sending'")
    db.commit()
    return db


class DeliveryError(Exception):
    def __init__(self, kind, retry_after=30):
        super().__init__(kind)
        self.kind, self.retry_after = kind, max(1, min(3600, retry_after))


class Discord:
    def __init__(self, url, channel_id):
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.netloc != 'discord.com' or parts.query or parts.fragment
                or not re.fullmatch(r'/api(?:/v10)?/webhooks/\d+/[A-Za-z0-9_-]+', parts.path)):
            raise ValueError('Invalid Discord webhook endpoint')
        self.url, self.channel_id = url, str(channel_id)
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'TopSignal-TopBot-Reporter/1.0'

    def request(self, method, suffix='', data=None):
        try:
            result = self.session.request(method, self.url+suffix, json=data, timeout=(10, 20), allow_redirects=False)
        except requests.RequestException:
            raise DeliveryError('uncertain' if method == 'POST' else 'network') from None
        if result.status_code == 429:
            try:
                delay = float(result.json().get('retry_after', 30))
            except (ValueError, TypeError):
                delay = 30
            raise DeliveryError('rate_limited', delay)
        if not 200 <= result.status_code < 300:
            kind = ('uncertain' if method == 'POST' and result.status_code >= 500 else
                    'missing_message' if method == 'PATCH' and result.status_code == 404 else
                    'authentication' if result.status_code in (401, 403, 404) else 'http_error')
            raise DeliveryError(kind)
        try:
            value = result.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise DeliveryError('uncertain' if method == 'POST' else 'invalid_response') from None

    def verify(self):
        value = self.request('GET')
        if str(value.get('channel_id')) != self.channel_id:
            raise ValueError('Webhook channel mismatch')

    def send(self, data, message_id=None):
        result = self.request('PATCH' if message_id else 'POST',
                              f'/messages/{message_id}' if message_id else '?wait=true', data)
        identity = str(result.get('id', ''))
        if not identity.isdigit() or str(result.get('channel_id')) != self.channel_id:
            raise DeliveryError('invalid_response' if message_id else 'uncertain')
        return identity


def deliver(db, discord, row):
    saved = db.execute('SELECT * FROM deliveries WHERE id=?', (row['id'],)).fetchone()
    if saved['status'] == 'sending':
        db.execute("UPDATE deliveries SET status='uncertain' WHERE id=?", (row['id'],))
        db.commit()
        return
    if saved['status'] in ('uncertain', 'missing_message'):
        return
    data = payload(row)
    if data is None:
        if (row.get('runner') or {}).get('stats_status') == 'not_filled' or row.get('order_status') in ('blocked', 'rejected', 'error', 'cancelled'):
            db.execute('UPDATE deliveries SET closed=1,changed=? WHERE id=?', (time.time(), row['id']))
            db.commit()
        return
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    if saved['digest'] == digest:
        return
    # Persist before the non-idempotent network operation.
    if not saved['message_id']:
        db.execute("UPDATE deliveries SET status='sending' WHERE id=?", (row['id'],))
        db.commit()
    try:
        message_id = discord.send(data, saved['message_id'])
    except DeliveryError as exc:
        status = exc.kind if exc.kind in ('uncertain', 'missing_message') else 'ready'
        db.execute('UPDATE deliveries SET status=? WHERE id=?', (status, row['id']))
        db.commit()
        raise
    db.execute("UPDATE deliveries SET message_id=?,digest=?,status='ready',closed=?,changed=? WHERE id=?",
               (message_id, digest, int(row['runner'].get('stats_status') == 'complete'), time.time(), row['id']))
    db.commit()


def cycle(db, source, discord):
    cursor = int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    rows = source.rows(after=cursor)
    if rows:
        with db:
            db.executemany('INSERT OR IGNORE INTO deliveries(id) VALUES(?)', [(r['id'],) for r in rows])
            db.execute("UPDATE meta SET value=? WHERE key='cursor'", (str(rows[-1]['id']),))
    # Revisit incomplete trades indefinitely; catch final-report corrections for seven days.
    ids = [r[0] for r in db.execute('SELECT id FROM deliveries WHERE closed=0 OR changed>? ORDER BY id', (time.time()-7*86400,))]
    errors, delay = [], 5
    for offset in range(0, len(ids), 100):
        for row in source.rows(ids=ids[offset:offset+100]):
            try:
                deliver(db, discord, row)
            except DeliveryError as exc:
                errors.append(exc.kind)
                delay = max(delay, exc.retry_after)
                if exc.kind not in ('uncertain', 'missing_message'):
                    return errors, delay
            except (ValueError, KeyError, TypeError):
                errors.append('invalid_trade_data')
    return errors, delay


@contextmanager
def process_lock(path):
    with open(path, 'a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def status_file(root, value):
    temporary = root/'status.tmp'
    temporary.write_text(json.dumps(dict(at=now(), pid=os.getpid(), **value)), encoding='utf-8')
    temporary.replace(root/'status.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--check', action='store_true', help='Read source and verify webhook; never post')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    source = Source(config['env_path'], config['bot_config_id'])
    root = Path(config['runtime_root'])
    root.mkdir(parents=True, exist_ok=True)
    discord = Discord(Path(config['webhook_file']).read_text(encoding='utf-8-sig').strip(), config['channel_id'])
    discord.verify()
    recap = None
    if config.get('recap'):
        if __package__:
            from .discord_topbot_recap import Recap
        else:
            from discord_topbot_recap import Recap
        recap = Recap(config['recap'], source)
    if args.check:
        if recap:
            recap.discord.verify()
        maximum, active = source.baseline()
        print(json.dumps(dict(channel_verified=True, latest_entry_id=maximum, active_entries=len(active))))
        return
    with process_lock(root/'reporter.lock'):
        db = state_db(root/'delivery.sqlite3')
        if not db.execute("SELECT 1 FROM meta WHERE key='cursor'").fetchone():
            maximum, active = source.baseline()
            with db:
                db.execute("INSERT INTO meta VALUES('cursor',?)", (str(maximum),))
                db.execute("INSERT INTO meta VALUES('started_at',?)", (now(),))
                db.execute("INSERT INTO meta VALUES('destination',?)", (str(config['channel_id']),))
                db.execute("INSERT INTO meta VALUES('config_id',?)", (str(config['bot_config_id']),))
                db.executemany('INSERT INTO deliveries(id) VALUES(?)', [(i,) for i in active])
        for key, expected in [('destination', config['channel_id']), ('config_id', config['bot_config_id'])]:
            if db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0] != str(expected):
                raise ValueError('Delivery state scope mismatch')
        if recap:
            recap.initialize(db)
        recap_errors, next_recap = [], 0
        while not (root/'STOP').exists():
            try:
                errors, delay = cycle(db, source, discord)
                if recap and time.monotonic() >= next_recap:
                    try:
                        recap_errors, retry = recap.cycle(db)
                    except Exception as exc:
                        db.rollback()
                        recap_errors, retry = ['recap_' + type(exc).__name__], 60
                    next_recap = time.monotonic() + max(60, retry)
                errors += recap_errors
                counts = dict(db.execute('SELECT status,COUNT(*) FROM deliveries GROUP BY status').fetchall())
                attention = bool(errors or counts.get('uncertain') or counts.get('missing_message'))
                status_file(root, dict(state='attention' if attention else 'watching', errors=errors,
                                      deliveries=counts, channel_id=config['channel_id']))
            except Exception as exc:
                # URLs, database credentials, raw SQL, and provider details never reach logs.
                db.rollback()
                status_file(root, dict(state='retrying', error_type=type(exc).__name__))
                delay = 30
            for _ in range(math.ceil(delay)):
                if (root/'STOP').exists():
                    break
                time.sleep(1)
        status_file(root, dict(state='stopped'))
        db.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None
