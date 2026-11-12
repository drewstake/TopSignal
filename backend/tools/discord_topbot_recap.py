"""Daily futures-session recaps for the independent Discord reporter."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__:
    from .discord_topbot import Discord, DeliveryError, payload
else:
    from discord_topbot import Discord, DeliveryError, payload

NY = ZoneInfo('America/New_York')
POST_TIME = time(17, 5)


def session_bounds(day):
    return (datetime.combine(day-timedelta(days=1), time(18), NY).astimezone(timezone.utc),
            datetime.combine(day, time(18), NY).astimezone(timezone.utc))


def latest_due(at):
    if at.tzinfo is None:
        raise ValueError('Recap clock needs timezone')
    local = at.astimezone(NY)
    day = local.date() if local.time() >= POST_TIME else local.date()-timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def amount(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Invalid recap amount')
    return result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def summarize(day, rows):
    start, end = session_bounds(day)
    wins = losses = breakeven = pending = total = 0
    gross = fees = net = Decimal('0.00')
    seen, modes = {}, set()
    estimated_commissions = False
    for row in rows:
        if row['id'] in seen:
            if seen[row['id']] != row:
                raise ValueError('Conflicting duplicate trade')
            continue
        seen[row['id']] = row
        runner = row.get('runner') or {}
        if row['execution_mode'] != 'live' or not runner.get('entry_at'):
            continue
        entered = datetime.fromisoformat(runner['entry_at'].replace('Z', '+00:00'))
        if entered.tzinfo is None:
            raise ValueError('Naive entry time')
        if not start <= entered < end:
            continue
        # The same validation/privacy contract as individual trade posts.
        if payload(row) is None:
            raise ValueError('Unverified session entry; recap withheld')
        total += 1
        modes.add('PRACTICE' if row['provider_simulated'] is True else
                  'LIVE' if row['provider_simulated'] is False else 'ACCOUNT TYPE UNVERIFIED')
        if runner.get('stats_status') != 'complete':
            pending += 1
            continue
        value = amount(runner['net_pnl_usd'])
        net += value
        gross += amount(runner['gross_pnl_usd'])
        fees += amount(runner['fees_usd'])
        wins += int(value > 0)
        losses += int(value < 0)
        breakeven += int(value == 0)
        estimated_commissions |= runner.get('commission_basis') == 'configured_round_turn_schedule'
    title_mode = ' / '.join(sorted(modes)) if modes else 'NO TRADES'
    description = ('Trades entered from 6 PM on the previous day to 6 PM on the report date, '
                   'New York time. Wins/losses use net P&L after fees; totals are USD.')
    if pending:
        description += ' **PROVISIONAL:** unresolved trades are excluded from P&L and win/loss totals. This post updates after reconciliation.'
    elif total == 0:
        description += ' No TopBot fills in this session.'
    if estimated_commissions:
        description += ' Commission uses the configured round-turn schedule where broker commission is unavailable.'
    fields = [{'name': label, 'value': str(value), 'inline': True} for label, value in [
        ('Total trades', total), ('Wins', wins), ('Losses', losses), ('Breakeven', breakeven),
        ('Pending results', pending), ('Win rate', f'{wins/(total-pending)*100:.1f}%' if total > pending else '—')]]
    fields += [{'name': label, 'value': value, 'inline': True} for label, value in [
        ('Gross P&L', f'${gross:+,.2f}'), ('Fees + commissions', f'${fees:,.2f}'),
        ('Net P&L', f'**${net:+,.2f}**')]]
    return {'allowed_mentions': {'parse': []}, 'embeds': [{
        'title': f'TopBot Daily Recap | {day.isoformat()} | {title_mode}',
        'description': description, 'color': 0xF1C40F if pending else 0x2ECC71 if net >= 0 else 0xE74C3C,
        'fields': fields, 'footer': {'text': f'TopBot session {day.isoformat()} | Scheduled 5:05 PM Eastern'},
    }]}, pending


def deliver_recap(db, discord, day, data, pending):
    key = day.isoformat()
    db.execute('INSERT OR IGNORE INTO recaps(day) VALUES(?)', (key,))
    db.commit()
    saved = db.execute('SELECT * FROM recaps WHERE day=?', (key,)).fetchone()
    if saved['status'] == 'sending':
        db.execute("UPDATE recaps SET status='uncertain' WHERE day=?", (key,))
        db.commit()
        return
    if saved['status'] in ('uncertain', 'missing_message'):
        return
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    if saved['digest'] == digest:
        return
    if not saved['message_id']:
        db.execute("UPDATE recaps SET status='sending' WHERE day=?", (key,))
        db.commit()
    try:
        identity = discord.send(data, saved['message_id'])
    except DeliveryError as exc:
        state = exc.kind if exc.kind in ('uncertain', 'missing_message') else 'ready'
        db.execute('UPDATE recaps SET status=? WHERE day=?', (state, key))
        db.commit()
        raise
    db.execute("UPDATE recaps SET message_id=?,digest=?,status='ready',pending=? WHERE day=?",
               (identity, digest, pending, key))
    db.commit()


class Recap:
    def __init__(self, config, source):
        self.config, self.source = config, source
        self.discord = Discord(Path(config['webhook_file']).read_text(encoding='utf-8-sig').strip(), config['channel_id'])
        self.verified = False

    def initialize(self, db, at=None):
        at = at or datetime.now(timezone.utc)
        db.execute('''CREATE TABLE IF NOT EXISTS recaps (
            day TEXT PRIMARY KEY, message_id TEXT, digest TEXT,
            status TEXT NOT NULL DEFAULT 'ready', pending INTEGER NOT NULL DEFAULT 1)''')
        db.execute("UPDATE recaps SET status='uncertain' WHERE status='sending'")
        destination = str(self.config['channel_id'])
        existing = db.execute("SELECT value FROM meta WHERE key='recap_destination'").fetchone()
        if existing and existing[0] != destination:
            raise ValueError('Recap destination scope mismatch')
        db.execute("INSERT OR IGNORE INTO meta VALUES('recap_destination',?)", (destination,))
        # No old history flood: begin with the day this feature is enabled.
        db.execute("INSERT OR IGNORE INTO meta VALUES('recap_next_day',?)", (at.astimezone(NY).date().isoformat(),))
        db.commit()

    def cycle(self, db, at=None):
        at = at or datetime.now(timezone.utc)
        if not self.verified:
            self.discord.verify()
            self.verified = True
        due = latest_due(at)
        next_day = date.fromisoformat(db.execute("SELECT value FROM meta WHERE key='recap_next_day'").fetchone()[0])
        # Durable daily jobs catch up after outages, at most five new reports per minute.
        added = 0
        while next_day <= due and added < 5:
            if next_day.weekday() < 5:
                db.execute('INSERT OR IGNORE INTO recaps(day) VALUES(?)', (next_day.isoformat(),))
                added += 1
            next_day += timedelta(days=1)
        db.execute("UPDATE meta SET value=? WHERE key='recap_next_day'", (next_day.isoformat(),))
        db.commit()
        jobs = db.execute('SELECT day FROM recaps WHERE day<=? AND (pending>0 OR day>=?) ORDER BY day',
                          (due.isoformat(), (due-timedelta(days=7)).isoformat())).fetchall()
        errors, retry = [], 60
        for job in jobs:
            day = date.fromisoformat(job[0])
            try:
                start, end = session_bounds(day)
                rows = self.source.rows(start=start, end=end)
                data, pending = summarize(day, rows)
                deliver_recap(db, self.discord, day, data, pending)
            except DeliveryError as exc:
                errors.append('recap_' + exc.kind)
                retry = max(retry, exc.retry_after)
                if exc.kind not in ('uncertain', 'missing_message'):
                    break
            except (ValueError, TypeError, KeyError):
                errors.append('recap_invalid_trade_data')
        if db.execute("SELECT 1 FROM recaps WHERE status IN ('uncertain','missing_message') LIMIT 1").fetchone():
            errors.append('recap_delivery_needs_review')
        return errors, retry
