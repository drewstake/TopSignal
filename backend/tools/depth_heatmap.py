"""Derived liquidity-heatmap history from committed raw depth recordings.

The independent depth recorder already captures every ProjectX depth/trade event
whether or not a browser is open. This module replays those committed raw
segments through the validated display-book kernel and stores compact, derived
heatmap frames in local SQLite under
``TOPSIGNAL_DATABENTO_CACHE_DIR/depth-heatmap-v1/<owner-hash>/``.

* Read-only with respect to recordings: only sealed, checksum-verified segments
  are read; active ``.partial`` files are never opened.
* Nothing is written to Supabase or any cloud table. Derived data is bounded by
  a per-owner size cap and a free-space reserve; reaching either stops derivation
  with a visible status instead of deleting anything.
* No interpolation: a frame exists only where the reconstructed book was
  connected, fresh (provider time within two seconds of the sample), uncrossed
  and on the contract's price grid. Every other interval is an explicit gap.
* Standalone (stdlib plus sibling tools) so the same reader can run on a
  recording host over the existing private SSH path. No trading imports.
"""
from __future__ import annotations

import argparse
from array import array
import base64
from collections import OrderedDict
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import time
from zoneinfo import ZoneInfo
import zlib

if __package__:
    from .depth_book import MarketByPriceBook, _Level
    from .read_depth_history import read_manifest, safe_path, verified_rows
    from .recording_inventory import directories
else:  # Standalone host entry point.
    from depth_book import MarketByPriceBook, _Level
    from read_depth_history import read_manifest, safe_path, verified_rows
    from recording_inventory import directories

SCHEMA = 'liquidity-heatmap-history-v1'
DERIVED_VERSION = 'depth-heatmap-derived-v1'
FRAME_MS = 500
TIER_MS = (500, 5_000, 60_000)
TRADE_GROUP_MS = (250, 5_000, 60_000)
LEVEL_LIMIT = 80
FRESH_MS = 2_000
FUTURE_MS = 1_000
TRADE_TIME_TOLERANCE_MS = 2_000
PRE_ROLL_MS = 5 * 60_000
MAX_SILENCE_SLOTS = 8 * 24 * 3600 * 1000 // FRAME_MS
MAX_RANGE_MS = (10 * 60_000, 3 * 3600_000, 26 * 3600_000)
MAX_TRADES = 8_000
MAX_GAPS = 2_000
MAX_OWNER_BYTES = 4 * 1024**3
# User-approved retention (November 11, 2026): keep the newest 30 derived sessions.
# Only derived session files are removed; they are rebuilt from raw recordings,
# which are never touched here.
KEEP_SESSIONS = 30
RETENTION_INTERVAL_MS = 10 * 60_000
SESSION_FILE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})\.sqlite3')
MAX_SESSION_DB_BYTES = 512 * 1024**2
FREE_RESERVE_BYTES = 15 * 1024**3
MAX_TICK = 2**31 - 2
MAX_TRADE_VOLUME = 1_000_000
CONTRACT_RE = re.compile(r'CON\.F\.US\.MNQ\.[A-Z0-9]{1,12}')
OWNER_RE = re.compile(r'[a-f0-9]{64}')
TICK_SIZES = {'MNQ': Decimal('0.25')}
NEW_YORK = ZoneInfo('America/New_York')
GAP_REASONS = ('disconnected', 'reset', 'awaiting_depth', 'stale', 'future', 'no_levels', 'crossed',
               'off_grid', 'warming', 'unreadable_segment', 'contract_changed')
BOUNDARY_KINDS = {'connect', 'disconnect', 'gap', 'roll', 'end'}
INT32_NONE = -2147483648


class StorageReview(RuntimeError):
    """Derived cache bound reached. Never resolved by deleting data."""


def tick_size_for(contract: str) -> Decimal:
    if not CONTRACT_RE.fullmatch(contract or ''):
        raise ValueError('Unsupported contract')
    return TICK_SIZES[contract.split('.')[3]]


def iso_ms(value) -> int:
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Times must include a timezone')
    return int(parsed.timestamp() * 1000)


def session_start_ms(at_ms: int) -> int:
    """Same 6 p.m. New York rule as read_depth_history.session_bounds."""
    local = datetime.fromtimestamp(at_ms / 1000, timezone.utc).astimezone(NEW_YORK)
    day = local.date() - timedelta(days=local.hour < 18)
    while day.weekday() in (4, 5):
        day -= timedelta(days=1)
    return int(datetime.combine(day, dtime(18), NEW_YORK).timestamp() * 1000)


def session_key(start_ms: int) -> str:
    return datetime.fromtimestamp(start_ms / 1000, timezone.utc).astimezone(NEW_YORK).date().isoformat()


def session_close_ms(start_ms: int) -> int:
    # Sessions open 6 p.m. and close 5 p.m. the next day; no DST change falls inside one.
    local = datetime.fromtimestamp(start_ms / 1000, timezone.utc).astimezone(NEW_YORK)
    return int(datetime.combine(local.date() + timedelta(days=1), dtime(17), NEW_YORK).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Compact columnar blobs (little-endian typed arrays + JSON header, zlib)

def _le(values: array) -> bytes:
    if sys.byteorder == 'big':
        values = array(values.typecode, values)
        values.byteswap()
    return values.tobytes()


def pack(columns: dict, meta: dict | None = None) -> bytes:
    header = dict(meta or {}, columns=[[name, column.typecode, len(column)] for name, column in columns.items()])
    head = json.dumps(header, separators=(',', ':'), allow_nan=False).encode()
    body = b''.join(_le(column) for column in columns.values())
    return zlib.compress(len(head).to_bytes(4, 'little') + head + body, 6)


def unpack(blob: bytes, *, max_bytes=64 * 1024**2) -> tuple[dict, dict]:
    raw = zlib.decompressobj().decompress(blob, max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('Derived chunk exceeds bound')
    size = int.from_bytes(raw[:4], 'little')
    header = json.loads(raw[4:4 + size])
    offset, columns = 4 + size, {}
    for name, typecode, length in header.pop('columns'):
        column = array(typecode)
        width = column.itemsize * length
        column.frombytes(raw[offset:offset + width])
        if sys.byteorder == 'big':
            column.byteswap()
        if len(column) != length:
            raise ValueError('Truncated derived chunk')
        columns[name] = column
        offset += width
    return header, columns


# ---------------------------------------------------------------------------
# Book state checkpoint (exact resume of a long-running capture)

def _dt(value):
    return value.isoformat() if value is not None else None


def _pdt(value):
    return datetime.fromisoformat(value) if value is not None else None


def _dec(value):
    return str(value) if value is not None else None


def _pdec(value):
    return Decimal(value) if value is not None else None


def book_state(book: MarketByPriceBook) -> dict:
    def levels(side):
        return [[_dec(l.price), _dec(l.size), _dec(l.volume), _dec(l.current_volume), _dt(l.timestamp)] for l in side.values()]
    return dict(contract=book.contract_id, bids=levels(book._bids), asks=levels(book._asks), sequence=book._sequence,
                last=_dt(book._last_timestamp), last_reset=_dt(book._last_reset_timestamp),
                level_times=[[side, _dec(price), _dt(at)] for (side, price), at in book._level_timestamps.items()],
                explicit=[[side, _dec(price)] for side, price in book._explicit_depth_levels],
                best_prices=[[side, _dec(price)] for side, price in book._best_quote_prices.items()],
                best_times=[[side, _dt(at)] for side, at in book._best_quote_timestamps.items()],
                fingerprints=[[_dt(f[0]), f[1], _dec(f[2]), _dec(f[3]), _dec(f[4])] for f in book._recent_fingerprints])


def restore_book(state: dict) -> MarketByPriceBook:
    book = MarketByPriceBook(state['contract'])
    for target, rows in ((book._bids, state['bids']), (book._asks, state['asks'])):
        for price, size, volume, current, at in rows:
            target[Decimal(price)] = _Level(Decimal(price), Decimal(size), Decimal(volume), _pdec(current), _pdt(at))
    book._sequence = int(state['sequence'])
    book._last_timestamp, book._last_reset_timestamp = _pdt(state['last']), _pdt(state['last_reset'])
    book._level_timestamps = {(side, Decimal(price)): _pdt(at) for side, price, at in state['level_times']}
    book._explicit_depth_levels = {(side, Decimal(price)) for side, price in state['explicit']}
    book._best_quote_prices = {side: Decimal(price) for side, price in state['best_prices']}
    book._best_quote_timestamps = {side: _pdt(at) for side, at in state['best_times']}
    for at, kind, price, volume, current in state['fingerprints']:
        fingerprint = (_pdt(at), kind, _pdec(price), _pdec(volume), _pdec(current))
        book._recent_fingerprints.append(fingerprint)
        book._recent_fingerprint_set.add(fingerprint)
    return book


# ---------------------------------------------------------------------------
# Reconstruction

class ChunkSink:
    """Derived output of one raw segment, split by contract and New York session."""

    def __init__(self):
        self.parts = {}
        self._hours = {}

    def _session(self, at_ms):
        # Session boundaries fall on whole New York hours, so cache by UTC hour.
        hour = at_ms // 3_600_000
        cached = self._hours.get(hour)
        if cached is None:
            start = session_start_ms(at_ms)
            cached = self._hours[hour] = (start, session_close_ms(start))
        return cached

    def _part(self, contract, at_ms):
        start, close = self._session(at_ms)
        if at_ms >= close:
            return None  # Daily halt / weekend: outside every session, nothing to show.
        key = (contract, start)
        part = self.parts.get(key)
        if part is None:
            part = self.parts[key] = dict(contract=contract, session_start=start, frames=[], gaps=[], trades={},
                                          first=at_ms, last=at_ms, notes=[])
        part['first'] = min(part['first'], at_ms)
        part['last'] = max(part['last'], at_ms)
        return part

    def touch(self, contract, at_ms):
        if contract:
            self._part(contract, at_ms)

    def frame(self, contract, slot, bids, asks):
        part = self._part(contract, slot)
        if part is None:
            return
        part['frames'].append((slot, bids, asks))
        part['last'] = max(part['last'], slot + FRAME_MS)

    def gap(self, contract, slot, reason):
        part = self._part(contract, slot)
        if part is None:
            return
        gaps = part['gaps']
        if gaps and gaps[-1][1] == slot and gaps[-1][2] == reason:
            gaps[-1][1] = slot + FRAME_MS
        else:
            gaps.append([slot, slot + FRAME_MS, reason])
        part['last'] = max(part['last'], slot + FRAME_MS)

    def trade(self, contract, at_ms, tick, side, volume):
        part = self._part(contract, at_ms)
        if part is None:
            return
        key = (at_ms // TRADE_GROUP_MS[0], tick, side)
        group = part['trades'].get(key)
        if group is None:
            part['trades'][key] = [at_ms, volume, 1]
        else:
            group[0] = min(group[0], at_ms)
            group[1] += volume
            group[2] += 1

    def note(self, contract, at_ms, kind):
        part = self._part(contract, at_ms) if contract else None
        if part is not None:
            part['notes'].append([at_ms, kind])
        return part is not None


class HeatmapEngine:
    """Sequential reconstruction of one recorder capture.

    Frame at slot ``s`` (a multiple of 500 ms of receipt time) is the book after
    every event received at or before ``s``, mirroring the live browser sampler.
    Boundary semantics follow ``depth_totals.BookTotals``.
    """

    def __init__(self, capture_id: str):
        self.capture_id = capture_id
        self.contract = None
        self.book = None
        self.tick = None
        self.connected = True
        self.last_epoch = None
        self.last_received = None
        self.last_mono = None
        self.last_index = None
        self.next_slot = None
        self.boundary = None
        self.generation = 0
        self.pending_note = None
        self._ticks = {}

    # -- checkpoint ---------------------------------------------------------
    def state(self) -> dict:
        return dict(version=DERIVED_VERSION, capture=self.capture_id, contract=self.contract,
                    book=book_state(self.book) if self.book else None, connected=self.connected,
                    last_epoch=self.last_epoch, last_received=self.last_received, last_mono=self.last_mono,
                    last_index=self.last_index, next_slot=self.next_slot, boundary=self.boundary,
                    generation=self.generation, pending_note=self.pending_note)

    @classmethod
    def restore(cls, state: dict) -> 'HeatmapEngine':
        if state.get('version') != DERIVED_VERSION:
            raise ValueError('Checkpoint version changed')
        engine = cls(state['capture'])
        engine.contract = state['contract']
        engine.tick = tick_size_for(engine.contract) if engine.contract else None
        engine.book = restore_book(state['book']) if state['book'] else None
        for name in ('connected', 'last_epoch', 'last_received', 'last_mono', 'last_index', 'next_slot', 'boundary', 'generation'):
            setattr(engine, name, state[name])
        engine.pending_note = state.get('pending_note')
        return engine

    # -- helpers ------------------------------------------------------------
    def _tick(self, price: Decimal):
        cached = self._ticks.get(price)
        if cached is None:
            try:
                ratio = price / self.tick
                cached = int(ratio) if ratio == ratio.to_integral_value() and abs(ratio) <= MAX_TICK else None
            except ArithmeticError:
                cached = None
            if len(self._ticks) > 20_000:
                self._ticks.clear()
            self._ticks[price] = cached
        return cached

    def _clear(self, reason):
        if self.book is not None:
            self.book.clear_for_reconnect()
        self.last_epoch = None
        self.boundary = reason
        self.generation += 1

    def _emit(self, slot, sink):
        contract = self.contract
        if sink is None or contract is None:
            return
        if self.pending_note and sink.note(contract, slot, self.pending_note):
            # Disclose a rebuilt book on the first slot that belongs to a session.
            self.pending_note = None
        reason = None
        if not self.connected:
            reason = 'disconnected'
        elif self.boundary:
            reason = 'contract_changed' if self.boundary == 'contract_changed' else 'reset'
        elif self.book is None or self.book._last_timestamp is None:
            reason = 'awaiting_depth'
        else:
            provider = int(self.book._last_timestamp.timestamp() * 1000)
            if slot - provider > FRESH_MS:
                reason = 'stale'
            elif provider - slot > FUTURE_MS:
                reason = 'future'
        if reason is None:
            bids_sorted = sorted(self.book._bids, reverse=True)[:LEVEL_LIMIT]
            asks_sorted = sorted(self.book._asks)[:LEVEL_LIMIT]
            if not bids_sorted and not asks_sorted:
                reason = 'no_levels'
            elif bids_sorted and asks_sorted and bids_sorted[0] >= asks_sorted[0]:
                reason = 'crossed'
            else:
                bids, asks = [], []
                for prices, source, target in ((bids_sorted, self.book._bids, bids), (asks_sorted, self.book._asks, asks)):
                    for price in prices:
                        tick = self._tick(price)
                        if tick is None:
                            reason = 'off_grid'
                            break
                        size = source[price].size
                        target.append((tick, min(65535, max(0, int(size.to_integral_value())))))
                    if reason:
                        break
                if reason is None:
                    sink.frame(contract, slot, bids, asks)
                    return
        sink.gap(contract, slot, reason)

    def _advance(self, until, sink):
        """Emit every slot strictly before ``until`` (exclusive) using the current state."""
        if self.next_slot is None:
            return
        steps = (until - self.next_slot + FRAME_MS - 1) // FRAME_MS
        if steps > MAX_SILENCE_SLOTS:
            # A capture silent for over a week: never fabricate; jump with an explicit gap.
            if sink is not None and self.contract:
                sink.note(self.contract, self.next_slot, 'silence_skipped')
            self.next_slot += (steps - 1) * FRAME_MS
        while self.next_slot < until:
            self._emit(self.next_slot, sink)
            if self.boundary:
                self.boundary = None
            self.next_slot += FRAME_MS

    # -- event feed -----------------------------------------------------------
    def feed(self, event: dict, sink: ChunkSink | None):
        received = iso_ms(event['received_at'])
        contract, kind, payload = event['contract_id'], event['kind'], event['payload']
        if not CONTRACT_RE.fullmatch(contract):
            raise ValueError('Invalid contract')
        if self.last_received is not None and received < self.last_received:
            # Receipt clock reversal: never emit slots backwards.
            self._clear('receipt_time_reversed')
        else:
            self._advance(received, sink)
        if self.next_slot is None:
            self.next_slot = -(-received // FRAME_MS) * FRAME_MS
        mono = event.get('monotonic_ns')
        if self.last_mono is not None and type(mono) is int and mono < self.last_mono:
            self._clear('receipt_gap')
        if contract != self.contract:
            self.contract = contract
            self.tick = tick_size_for(contract)
            self._ticks = {}
            self.book = MarketByPriceBook(contract)
            if self.last_index is not None:
                self.boundary = 'contract_changed'
                self.generation += 1
        if sink is not None:
            sink.touch(contract, received)
        if kind in BOUNDARY_KINDS:
            self._clear(kind)
            self.connected = kind not in {'disconnect', 'end'}
        elif kind == 'depth':
            self.connected = True
            epoch = payload.get('epoch')
            if epoch is not None and self.last_epoch is not None and epoch != self.last_epoch:
                self._clear('epoch_changed')
            if epoch is not None:
                self.last_epoch = epoch
            if payload.get('type') == 6:
                self.boundary = 'reset'
            self.book.apply(payload)
        elif kind == 'trade' and sink is not None:
            price, volume = payload.get('price'), payload.get('volume')
            if (all(type(v) in (int, float) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in (price, volume))
                    and volume <= MAX_TRADE_VOLUME):
                tick = self._tick(Decimal(str(price)))
                if tick is not None:
                    at = received
                    try:
                        provider = iso_ms(event['provider_at'])
                        if 0 <= received - provider <= TRADE_TIME_TOLERANCE_MS:
                            at = provider
                    except (TypeError, ValueError):
                        pass
                    typ = payload.get('type')
                    side = 1 if typ == 0 and type(typ) is int else 2 if typ == 1 and type(typ) is int else 0
                    sink.trade(contract, at, tick, side, float(volume))
        self.last_received = received if self.last_received is None else max(self.last_received, received)
        if type(mono) is int:
            self.last_mono = mono
        self.last_index = event['index']


# ---------------------------------------------------------------------------
# Chunk encoding and tier aggregation

def _aggregate(frames, bucket_ms):
    buckets = OrderedDict()
    for slot, bids, asks in frames:
        start = slot // bucket_ms * bucket_ms
        bucket = buckets.get(start)
        if bucket is None:
            bucket = buckets[start] = [0, {}, None, None, -1]
        bucket[0] += 1
        levels = bucket[1]
        for side, rows in ((1, bids), (2, asks)):
            for tick, qty in rows:
                entry = levels.get(tick)
                if entry is None:
                    levels[tick] = [qty, 1, side]
                else:
                    entry[0] += qty
                    entry[1] += 1
                    entry[2] |= side
        if slot >= bucket[4]:
            bucket[2] = bids[0][0] if bids else None
            bucket[3] = asks[0][0] if asks else None
            bucket[4] = slot
    return buckets


def encode_tier0(frames) -> bytes:
    times, nb, na, ticks, qty = array('q'), array('H'), array('H'), array('i'), array('H')
    for slot, bids, asks in frames:
        times.append(slot)
        nb.append(len(bids))
        na.append(len(asks))
        for tick, size in bids:
            ticks.append(tick)
            qty.append(size)
        for tick, size in asks:
            ticks.append(tick)
            qty.append(size)
    return pack(dict(time=times, nb=nb, na=na, tick=ticks, qty=qty), dict(tier=0))


def encode_buckets(buckets, tier) -> bytes:
    times, valid, count, best_bid, best_ask, last = (array('q'), array('H'), array('H'), array('i'), array('i'), array('q'))
    ticks, sums, counts, sides = array('i'), array('I'), array('H'), array('B')
    for start, (frames, levels, bid, ask, last_slot) in buckets.items():
        times.append(start)
        valid.append(frames)
        count.append(len(levels))
        best_bid.append(INT32_NONE if bid is None else bid)
        best_ask.append(INT32_NONE if ask is None else ask)
        last.append(last_slot)
        for tick in sorted(levels):
            total, seen, side = levels[tick]
            ticks.append(tick)
            sums.append(min(4_294_967_295, int(total)))
            counts.append(min(65535, seen))
            sides.append(side)
    return pack(dict(time=times, valid=valid, count=count, best_bid=best_bid, best_ask=best_ask, last=last,
                     tick=ticks, sum=sums, seen=counts, side=sides), dict(tier=tier))


def encode_trades(groups, tier) -> bytes:
    bucket_ms = TRADE_GROUP_MS[tier]
    merged = {}
    for (bucket, tick, side), (at, volume, prints) in groups.items():
        key = (bucket * TRADE_GROUP_MS[0] // bucket_ms, tick, side)
        old = merged.get(key)
        if old is None:
            merged[key] = [at, volume, prints]
        else:
            old[0] = min(old[0], at)
            old[1] += volume
            old[2] += prints
    keys, times, ticks, sides, volumes, prints = array('q'), array('q'), array('i'), array('B'), array('f'), array('I')
    for (bucket, tick, side), (at, volume, count) in sorted(merged.items()):
        keys.append(bucket)
        times.append(at)
        ticks.append(tick)
        sides.append(side)
        volumes.append(volume)
        prints.append(count)
    return pack(dict(bucket=keys, time=times, tick=ticks, side=sides, volume=volumes, prints=prints), dict(tier=tier))


# ---------------------------------------------------------------------------
# Local stores

SESSION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, segment_key TEXT NOT NULL, contract_id TEXT NOT NULL, data_live INTEGER NOT NULL,
  capture_id TEXT NOT NULL, first_index INTEGER NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  gaps TEXT NOT NULL, notes TEXT NOT NULL, UNIQUE(segment_key, contract_id));
CREATE INDEX IF NOT EXISTS chunks_range ON chunks(contract_id, data_live, start_ms, end_ms);
CREATE TABLE IF NOT EXISTS tier_data (chunk_id INTEGER NOT NULL, kind TEXT NOT NULL, data BLOB NOT NULL,
  PRIMARY KEY(chunk_id, kind));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''

STATE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS segments (segment_key TEXT PRIMARY KEY, capture_id TEXT NOT NULL, first_index INTEGER NOT NULL,
  last_received_ms INTEGER NOT NULL, status TEXT NOT NULL, processed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS captures (capture_id TEXT PRIMARY KEY, last_index INTEGER NOT NULL, state BLOB NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


def _connect(path: Path, schema: str, *, max_bytes: int | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    # WAL avoids transient rollback-journal files beside the database.
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA synchronous=NORMAL')
    if max_bytes:
        page = connection.execute('PRAGMA page_size').fetchone()[0]
        connection.execute(f'PRAGMA max_page_count={max_bytes // page}')
    connection.executescript(schema)
    return connection


def _readonly(path: Path) -> sqlite3.Connection:
    # A WAL reader may need to create the shared-memory index, so open normally
    # and forbid writes for the life of the connection.
    connection = sqlite3.connect(path, timeout=5)
    connection.execute('PRAGMA query_only=1')
    return connection


def owner_dir(cache: Path, owner: str) -> Path:
    if not OWNER_RE.fullmatch(owner):
        raise ValueError('Invalid owner scope')
    return cache / owner


def owner_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob('*'):
        if path.is_file():
            total += path.stat().st_size
    return total


# ---------------------------------------------------------------------------
# Incremental inventory of committed raw segments

class Inventory:
    """Directory-mtime cached scan; unchanged run directories are not relisted."""

    def __init__(self, root: Path):
        self.root = Path(root)
        # run -> (mtime, {marker name: entry, or None when invalid or before the window})
        self.runs = {}
        self.errors = 0

    def scan(self, *, since_ms: int | None = None):
        root = self.root
        found = []
        if not root.exists():
            return found
        safe_path(root, root)
        live_runs = set()
        for day, _ in directories(root, r'\d{4}-\d{2}-\d{2}'):
            for run, metadata in directories(day, '[a-f0-9]{32}'):
                live_runs.add(run)
                stamp = metadata[3]
                cached = self.runs.get(run)
                known = cached[1] if cached else {}
                if cached is None or cached[0] != stamp:
                    safe_path(run, root)
                    # Only markers not seen before are read; a long capture is not reparsed per segment.
                    with os.scandir(run) as entries:
                        markers = sorted((Path(e.path), e.stat(follow_symlinks=False)) for e in entries if e.name.endswith('.segment.json'))
                    for marker, info in markers:
                        if marker.name in known:
                            continue
                        known[marker.name] = None
                        try:
                            m = read_manifest(marker, root, validated_parent=run, file_info=info)
                            owner = m.get('owner_hash', '')
                            if (not OWNER_RE.fullmatch(owner) or m['schema'] != 'mnq-depth-segment-v1' or m['synthetic'] is not False
                                    or m['source'] != 'projectx' or m['capture_id'] != run.name):
                                raise ValueError('Invalid segment scope')
                            first, last = iso_ms(m['first_received_at']), iso_ms(m['last_received_at'])
                            if first > last or not 0 < m['uncompressed_bytes'] <= 16 * 1024**2 + 70000:
                                raise ValueError('Invalid segment bounds')
                            if since_ms is not None and last < since_ms:
                                continue  # Before the window; the window only moves forward.
                            known[marker.name] = dict(owner=owner, capture=run.name, first_index=m['first_index'],
                                                      last_index=m['last_index'], first_ms=first, last_ms=last,
                                                      data_live=m['data_live'], marker=marker, manifest=m,
                                                      key=f"{run.name}:{m['first_index']}:{m['sha256']}")
                        except (ValueError, KeyError, OSError, TypeError):
                            self.errors += 1
                    self.runs[run] = (stamp, known)
                for name, entry in list(known.items()):
                    if entry is None:
                        continue
                    if since_ms is not None and entry['last_ms'] < since_ms:
                        known[name] = None  # Dropped from memory once the session window passes it.
                        continue
                    found.append(entry)
        for stale in set(self.runs) - live_runs:
            self.runs.pop(stale, None)
        return found


# ---------------------------------------------------------------------------
# Deriver

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class Deriver:
    def __init__(self, root: Path, cache: Path, *, max_owner_bytes=MAX_OWNER_BYTES,
                 free_reserve=FREE_RESERVE_BYTES, clock=None, pid=None, keep_sessions=KEEP_SESSIONS):
        self.root = Path(root)
        self.cache = Path(cache)
        self.inventory = Inventory(self.root)
        self.engines = {}
        self.max_owner_bytes = max_owner_bytes
        self.free_reserve = free_reserve
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.pid = pid or os.getpid()
        self.states = {}
        self.sessions = {}
        self.errors = 0
        self.keep_sessions = max(1, int(keep_sessions))
        self.retention_at = {}

    def close(self):
        for connection in list(self.states.values()) + list(self.sessions.values()):
            connection.close()
        self.states.clear()
        self.sessions.clear()

    def _state(self, owner):
        connection = self.states.get(owner)
        if connection is None:
            directory = owner_dir(self.cache, owner)
            directory.mkdir(parents=True, exist_ok=True)
            safe_path(directory, self.cache)
            connection = self.states[owner] = _connect(directory / 'state.sqlite3', STATE_SCHEMA, max_bytes=256 * 1024**2)
        return connection

    def _session(self, owner, start):
        key = (owner, start)
        connection = self.sessions.get(key)
        if connection is None:
            directory = owner_dir(self.cache, owner) / 'sessions'
            directory.mkdir(parents=True, exist_ok=True)
            safe_path(directory, self.cache)
            connection = _connect(directory / f'{session_key(start)}.sqlite3', SESSION_SCHEMA, max_bytes=MAX_SESSION_DB_BYTES)
            connection.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('derived_version', DERIVED_VERSION))
            connection.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('session_start_ms', str(start)))
            connection.commit()
            # Keep only a few open session files; older sessions are rarely written.
            while len(self.sessions) >= 4:
                self.sessions.pop(next(iter(self.sessions))).close()
            self.sessions[key] = connection
        return connection

    def set_status(self, owner, **values):
        connection = self._state(owner)
        values = dict(values, updated_at=_now_iso(), pid=self.pid, derived_version=DERIVED_VERSION)
        connection.execute('INSERT OR REPLACE INTO status VALUES (?,?)', ('deriver', json.dumps(values, allow_nan=False)))
        connection.commit()

    def report_error(self, error):
        for owner in list(self.states):
            try:
                self.set_status(owner, state='error', reason=error)
            except sqlite3.Error:
                pass

    def lease(self, owner) -> bool:
        """Single active deriver per owner without a lock file in the backed-up tree."""
        connection = self._state(owner)
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute("SELECT value FROM status WHERE key='lease'").fetchone()
            now = self.clock()
            if row:
                holder = json.loads(row[0])
                if holder['pid'] != self.pid and now - holder['at'] < 60_000:
                    connection.execute('ROLLBACK')
                    return False
            connection.execute('INSERT OR REPLACE INTO status VALUES (?,?)', ('lease', json.dumps(dict(pid=self.pid, at=now))))
            connection.execute('COMMIT')
            return True
        except Exception:
            connection.execute('ROLLBACK')
            raise

    def apply_retention(self, owner) -> list[str]:
        """Delete derived session files beyond the newest `keep_sessions` (never the current one).

        Only `sessions/YYYY-MM-DD.sqlite3` files (and their WAL/SHM) of this owner
        are eligible. A file that cannot be removed now (for example open in a
        reader) is retried on a later pass.
        """
        directory = owner_dir(self.cache, owner) / 'sessions'
        if not directory.is_dir():
            return []
        safe_path(directory, self.cache)
        current = session_key(session_start_ms(self.clock()))
        keys = sorted({match.group(1) for path in directory.iterdir()
                       if path.is_file() and (match := SESSION_FILE_RE.fullmatch(path.name))}, reverse=True)
        keep = set(keys[:self.keep_sessions]) | {current}
        removed = []
        for key in keys:
            if key in keep:
                continue
            for open_key, connection in list(self.sessions.items()):
                if open_key[0] == owner and session_key(open_key[1]) == key:
                    connection.close()
                    self.sessions.pop(open_key)
            try:
                for suffix in ('.sqlite3', '.sqlite3-wal', '.sqlite3-shm'):
                    path = directory / f'{key}{suffix}'
                    if path.exists():
                        safe_path(path, self.cache)
                        path.unlink()
                removed.append(key)
            except OSError:
                continue
        oldest = min(keep)
        oldest_start = int(datetime.combine(datetime.fromisoformat(oldest).date(), dtime(18), NEW_YORK).timestamp() * 1000)
        state = self._state(owner)
        # Bookkeeping for sessions no longer kept: processed-segment keys and ended captures' checkpoints.
        pruned = state.execute('DELETE FROM segments WHERE last_received_ms < ?', (oldest_start - PRE_ROLL_MS,)).rowcount
        cutoff = datetime.fromtimestamp((oldest_start - PRE_ROLL_MS) / 1000, timezone.utc).isoformat()
        state.execute('DELETE FROM captures WHERE updated_at < ?', (cutoff,))
        state.execute('INSERT OR REPLACE INTO status VALUES (?,?)', ('retention', json.dumps(dict(
            keep_sessions=self.keep_sessions, kept=sorted(keep), removed=removed, pruned_segment_rows=pruned, at=_now_iso()))))
        state.commit()
        return removed

    def check_storage(self, owner):
        directory = owner_dir(self.cache, owner)
        if owner_bytes(directory) > self.max_owner_bytes:
            raise StorageReview('derived_cache_budget_reached')
        if shutil.disk_usage(self.cache).free < self.free_reserve:
            raise StorageReview('disk_free_reserve')

    def run_once(self, *, budget_seconds=None, should_stop=None) -> dict:
        now = self.clock()
        target = session_start_ms(now) - PRE_ROLL_MS
        entries = self.inventory.scan(since_ms=target - PRE_ROLL_MS - 120_000)
        by_owner = {}
        for entry in entries:
            by_owner.setdefault(entry['owner'], {}).setdefault(entry['capture'], []).append(entry)
        started = time.monotonic()
        summary = dict(processed=0, pending=0, owners=len(by_owner))
        for owner, captures in by_owner.items():
            if not self.lease(owner):
                self.set_status(owner, state='standby', reason='another_deriver_active')
                continue
            state = self._state(owner)
            done = {row[0] for row in state.execute('SELECT segment_key FROM segments WHERE last_received_ms >= ?', (target,))}
            pending_total = sum(1 for segments in captures.values() for s in segments
                                if s['key'] not in done and s['last_ms'] >= target)
            processed = 0
            for segments in captures.values():
                segments.sort(key=lambda entry: entry['first_index'])
            try:
                for capture, segments in sorted(captures.items(), key=lambda item: item[1][0]['first_ms']):
                    pending = [s for s in segments if s['key'] not in done and s['last_ms'] >= target]
                    if not pending:
                        continue
                    for segment in self._plan(owner, capture, segments, pending):
                        if ((budget_seconds is not None and time.monotonic() - started > budget_seconds)
                                or (should_stop is not None and should_stop())):
                            break
                        write = segment['key'] not in done and segment['last_ms'] >= target
                        self._process(owner, capture, segment, write=write)
                        if write:
                            processed += 1
                            done.add(segment['key'])
                self.set_status(owner, state='current' if processed >= pending_total else 'building',
                                pending_segments=max(0, pending_total - processed), session_start_ms=target + PRE_ROLL_MS,
                                processed_through_ms=max((s['last_ms'] for c in captures.values() for s in c if s['key'] in done), default=None),
                                skipped_manifests=self.inventory.errors)
            except StorageReview as exc:
                self.set_status(owner, state='storage_review_required', reason=str(exc), pending_segments=pending_total - processed)
                raise
            summary['processed'] += processed
            summary['pending'] += max(0, pending_total - processed)
            if now - self.retention_at.get(owner, -RETENTION_INTERVAL_MS) >= RETENTION_INTERVAL_MS:
                self.retention_at[owner] = now
                try:
                    summary.setdefault('removed_sessions', []).extend(self.apply_retention(owner))
                except (OSError, sqlite3.Error, ValueError):
                    pass  # Retried on the next interval; derivation continues.
        return summary

    def _plan(self, owner, capture, segments, pending):
        """Resume exactly from a checkpoint, else rebuild the book with pre-roll."""
        first = pending[0]
        engine = self.engines.get((owner, capture))
        if engine is None:
            row = self._state(owner).execute('SELECT last_index, state FROM captures WHERE capture_id=?', (capture,)).fetchone()
            if row:
                try:
                    engine = HeatmapEngine.restore(json.loads(zlib.decompress(row[1])))
                except (ValueError, KeyError, TypeError, zlib.error):
                    engine = None
        if engine is not None and engine.last_index == first['first_index'] - 1:
            self.engines[(owner, capture)] = engine
            return [s for s in segments if s['first_index'] >= first['first_index']]
        # No exact predecessor state: start a fresh book. If the capture began
        # earlier, replay up to five minutes before the first pending segment as
        # warm-up (no output) and disclose that the book was rebuilt mid-capture.
        self.engines[(owner, capture)] = HeatmapEngine(capture)
        earlier = [s for s in segments if s['first_index'] < first['first_index']]
        warm = [s for s in earlier if s['last_ms'] >= first['first_ms'] - PRE_ROLL_MS]
        if earlier and earlier[0]['first_index'] == 1 and len(earlier) - len(warm) <= 5:
            warm = earlier  # The capture start is close: replay it exactly.
        # Warm-up must be one contiguous run of indexes ending at the first pending segment.
        while warm and any(b['first_index'] != a['last_index'] + 1 for a, b in zip(warm, warm[1:] + [first])):
            warm = warm[1:]
        rebuilt = (warm[0] if warm else first)['first_index'] != 1
        later = [s for s in segments if s['first_index'] > first['first_index']]
        return warm + [dict(first, rebuilt=rebuilt)] + later

    def _process(self, owner, capture, segment, *, write):
        engine = self.engines[(owner, capture)]
        if engine.last_index is not None and segment['first_index'] != engine.last_index + 1:
            # A missing segment in this capture: never bridge it with stale book state.
            engine = self.engines[(owner, capture)] = HeatmapEngine(capture)
            segment = dict(segment, rebuilt=True)
        if write and (segment.get('rebuilt') or (engine.last_index is None and segment['first_index'] != 1)):
            # Disclosed on the first emitted slot that falls inside a session.
            engine.pending_note = 'book_rebuilt_mid_capture'
        sink = ChunkSink() if write else None
        try:
            rows = verified_rows(segment['marker'], segment['manifest'], self.root, raw_events=True)
            for event in rows:
                if event['owner_hash'] != owner:
                    raise ValueError('Owner scope mismatch')
                engine.feed(event, sink)
            status = 'derived'
        except Exception:  # noqa: BLE001 - any bad record must not wedge derivation in a restart loop.
            # Corrupt, unreadable or out-of-range evidence: explicit gap, fresh book afterwards.
            self.errors += 1
            self.engines[(owner, capture)] = HeatmapEngine(capture)
            sink = ChunkSink() if write else None
            if sink is not None and engine.contract:
                for slot in range(segment['first_ms'] // FRAME_MS * FRAME_MS, segment['last_ms'] + 1, FRAME_MS):
                    sink.gap(engine.contract, slot, 'unreadable_segment')
            status = 'unreadable'
        if write:
            self.check_storage(owner)
            for part in sink.parts.values():
                self._write_chunk(owner, segment, part)
            state = self._state(owner)
            current = self.engines[(owner, capture)]
            blob = zlib.compress(json.dumps(current.state(), separators=(',', ':')).encode(), 6)
            state.execute('INSERT OR REPLACE INTO captures VALUES (?,?,?,?)', (capture, current.last_index or 0, blob, _now_iso()))
            state.execute('INSERT OR REPLACE INTO segments VALUES (?,?,?,?,?,?)',
                          (segment['key'], capture, segment['first_index'], segment['last_ms'], status, _now_iso()))
            state.commit()

    def _write_chunk(self, owner, segment, part):
        frames = part['frames']
        connection = self._session(owner, part['session_start'])
        try:
            with connection:
                # A retried segment replaces its earlier chunk and blobs; nothing is orphaned.
                for (old,) in connection.execute('SELECT id FROM chunks WHERE segment_key=? AND contract_id=?',
                                                 (segment['key'], part['contract'])).fetchall():
                    connection.execute('DELETE FROM tier_data WHERE chunk_id=?', (old,))
                    connection.execute('DELETE FROM chunks WHERE id=?', (old,))
                cursor = connection.execute(
                    'INSERT INTO chunks (segment_key, contract_id, data_live, capture_id, first_index, start_ms, end_ms, gaps, notes) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    (segment['key'], part['contract'], int(segment['data_live']), segment['capture'], segment['first_index'],
                     part['first'], part['last'], json.dumps(part['gaps']), json.dumps(part['notes'])))
                chunk = cursor.lastrowid
                blobs = [('depth0', encode_tier0(frames))]
                for tier in (1, 2):
                    blobs.append((f'depth{tier}', encode_buckets(_aggregate(frames, TIER_MS[tier]), tier)))
                for tier in (0, 1, 2):
                    blobs.append((f'trades{tier}', encode_trades(part['trades'], tier)))
                connection.executemany('INSERT INTO tier_data VALUES (?,?,?)', [(chunk, kind, blob) for kind, blob in blobs])
        except sqlite3.OperationalError as exc:
            if 'full' in str(exc).lower():
                raise StorageReview('session_file_budget_reached') from exc
            raise


def follow(root: Path, cache: Path, *, poll_seconds=5.0, stop: threading.Event | None = None,
           paused: threading.Event | None = None):
    stop = stop or threading.Event()
    deriver = Deriver(root, cache)
    try:
        while not stop.is_set():
            if paused is not None and paused.is_set():
                for owner in deriver.states:
                    deriver.set_status(owner, state='paused', reason='recording_or_replay_priority')
                stop.wait(poll_seconds)
                continue
            wait = poll_seconds
            try:
                summary = deriver.run_once(budget_seconds=20,
                    should_stop=lambda: stop.is_set() or (paused is not None and paused.is_set()))
                if summary['pending']:
                    wait = 0.2  # Catch up promptly while still yielding between batches.
            except StorageReview:
                wait = 300
            except Exception as exc:  # noqa: BLE001 - report every failure instead of crash-looping silently.
                deriver.report_error(type(exc).__name__)
                wait = 30
            stop.wait(wait)
    finally:
        deriver.close()


# ---------------------------------------------------------------------------
# Reader

def _b64(column: array) -> str:
    return base64.b64encode(_le(column)).decode('ascii')


def _merge_intervals(intervals, tolerance=2_000):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + tolerance:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _merge_gaps(gaps):
    merged = []
    for start, end, reason in sorted(gaps):
        if merged and merged[-1][2] == reason and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, reason])
    return merged


def deriver_status(cache: Path, owner: str) -> dict | None:
    path = owner_dir(cache, owner) / 'state.sqlite3'
    if not path.exists():
        return None
    connection = _readonly(path)
    try:
        row = connection.execute("SELECT value FROM status WHERE key='deriver'").fetchone()
        return json.loads(row[0]) if row else None
    finally:
        connection.close()


def _read_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise RuntimeError('Heatmap history is still preparing. Retry shortly.')


def read_heatmap(cache: Path, query: dict, *, now_ms: int | None = None, deadline=None) -> dict:
    _read_deadline(deadline)
    result = _read_heatmap(cache, query, now_ms=now_ms, deadline=deadline)
    # Stamped when the response is complete; the browser aligns its live clock to it.
    result['server_time_ms'] = int(time.time() * 1000)
    return result


def _read_heatmap(cache: Path, query: dict, *, now_ms: int | None = None, deadline=None) -> dict:
    cache = Path(cache)
    owner, contract, live = query.get('owner_hash', ''), query.get('contract_id', ''), query.get('data_live', False)
    tier = query.get('tier', 0)
    summary = query.get('summary', False)
    if type(summary) is not bool:
        raise ValueError('Invalid summary flag')
    if not OWNER_RE.fullmatch(owner) or not CONTRACT_RE.fullmatch(contract) or type(live) is not bool or tier not in (0, 1, 2):
        raise ValueError('Invalid heatmap scope')
    start, end = query.get('start_ms'), query.get('end_ms')
    if type(start) is not int or type(end) is not int or not 0 < end - start <= MAX_RANGE_MS[tier]:
        raise ValueError('Invalid heatmap range for the selected resolution')
    now_ms = now_ms or int(time.time() * 1000)
    session_start = session_start_ms(now_ms)
    session_end = session_close_ms(session_start)
    start, end = max(start, session_start), min(end, session_end, now_ms + 1000)
    tick_size = tick_size_for(contract)
    directory = owner_dir(cache, owner)
    path = directory / 'sessions' / f'{session_key(session_start)}.sqlite3'
    result = dict(schema=SCHEMA, contract_id=contract, data_live=live, tick_size=float(tick_size), level_limit=LEVEL_LIMIT,
                  frame_ms=FRAME_MS, tier=tier, bucket_ms=TIER_MS[tier], trade_group_ms=TRADE_GROUP_MS[tier],
                  session=dict(start_ms=session_start, end_ms=session_end, key=session_key(session_start)),
                  range=dict(start_ms=start, end_ms=max(start, end)), server_time_ms=None,
                  history_through_ms=None, coverage=[], gaps=[], notes=[], overlapping_excluded=0,
                  frames=None, trades=None, trades_truncated=False, trades_min_volume=None,
                  deriver=deriver_status(cache, owner) if directory.exists() else None,
                  sealed_only=True, exchange_completeness_verified=False, snapshot_completion_verified=False)
    if not path.exists():
        return result
    safe_path(path, cache)
    connection = _readonly(path)
    try:
        if deadline is not None:
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if connection.execute("SELECT value FROM meta WHERE key='derived_version'").fetchone() != (DERIVED_VERSION,):
            return dict(result, notes=[[session_start, 'derived_version_mismatch']])
        rows = connection.execute('SELECT id, capture_id, start_ms, end_ms, gaps, notes FROM chunks '
                                  'WHERE contract_id=? AND data_live=? ORDER BY start_ms, capture_id, first_index',
                                  (contract, int(live))).fetchall()
        # Overlapping captures would double-count trades: keep the earlier capture.
        accepted, spans, excluded = [], {}, 0
        for row in rows:
            _read_deadline(deadline)
            chunk_id, capture, chunk_start, chunk_end = row[:4]
            clash = any(other != capture and chunk_start < span[1] - 2_000 and chunk_end > span[0] + 2_000
                        for other, items in spans.items() for span in items[-3:])
            if clash:
                excluded += 1
                continue
            spans.setdefault(capture, []).append((chunk_start, chunk_end))
            accepted.append(row)
        coverage = _merge_intervals([(r[2], r[3]) for r in accepted])
        gaps = _merge_gaps([g for r in accepted for g in json.loads(r[4])])
        notes = [n for r in accepted for n in json.loads(r[5])]
        if len(gaps) > MAX_GAPS:
            # Keep the longest gaps; short stale blips remain visible at finer zoom.
            gaps = sorted(sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:MAX_GAPS])
            result['gaps_truncated'] = True
        result.update(coverage=coverage, gaps=gaps, notes=notes[:200], overlapping_excluded=excluded,
                      history_through_ms=coverage[-1][1] if coverage else None)
        selected = [r[0] for r in accepted if r[3] >= start and r[2] <= end]
        depth_kind, trade_kind = f'depth{tier}', ('none' if summary else f'trades{tier}')
        blobs = {}
        for i in range(0, len(selected), 500):
            _read_deadline(deadline)
            ids = selected[i:i + 500]
            marks = ','.join('?' * len(ids))
            for chunk_id, kind, data in connection.execute(
                    f'SELECT chunk_id, kind, data FROM tier_data WHERE chunk_id IN ({marks}) AND kind IN (?,?)', (*ids, depth_kind, trade_kind)):
                blobs[(chunk_id, kind)] = data
    finally:
        connection.close()
    origin = start // TIER_MS[tier] * TIER_MS[tier]
    level_seen = array('H')
    if tier == 0:
        frames = {}
        for chunk_id in selected:
            _read_deadline(deadline)
            blob = blobs.get((chunk_id, depth_kind))
            if blob is None:
                continue
            _, c = unpack(blob)
            offset = 0
            for i, slot in enumerate(c['time']):
                width = c['nb'][i] + c['na'][i]
                if start <= slot < end and slot not in frames:
                    frames[slot] = (c['nb'][i], c['na'][i], c['tick'][offset:offset + width], c['qty'][offset:offset + width])
                offset += width
        times, counts, bids, best_bid, best_ask, ticks, qty, side = (array('I'), array('H'), array('H'), array('i'), array('i'),
                                                                     array('i'), array('f'), array('B'))
        for slot in sorted(frames):
            nb, na, t, q = frames[slot]
            times.append(slot - origin)
            best_bid.append(t[0] if nb else INT32_NONE)
            best_ask.append(t[nb] if na else INT32_NONE)
            if summary:  # Navigator: coverage and quote path only, no level arrays.
                counts.append(0)
                bids.append(0)
                continue
            counts.append(nb + na)
            bids.append(nb)
            ticks.extend(t)
            qty.extend(float(v) for v in q)
            side.extend([1] * nb + [2] * na)
        valid = array('H', [1] * len(times))
    else:
        buckets = {}
        for chunk_id in selected:
            _read_deadline(deadline)
            blob = blobs.get((chunk_id, depth_kind))
            if blob is None:
                continue
            _, c = unpack(blob)
            offset = 0
            for i, bucket_start in enumerate(c['time']):
                n = c['count'][i]
                if origin <= bucket_start < end:
                    bucket = buckets.get(bucket_start)
                    if bucket is None:
                        bucket = buckets[bucket_start] = [0, {}, INT32_NONE, INT32_NONE, -1]
                    bucket[0] += c['valid'][i]
                    levels = bucket[1]
                    for j in (() if summary else range(offset, offset + n)):
                        entry = levels.get(c['tick'][j])
                        if entry is None:
                            levels[c['tick'][j]] = [c['sum'][j], c['seen'][j], c['side'][j]]
                        else:
                            entry[0] += c['sum'][j]
                            entry[1] += c['seen'][j]
                            entry[2] |= c['side'][j]
                    if c['last'][i] >= bucket[4]:
                        bucket[2], bucket[3], bucket[4] = c['best_bid'][i], c['best_ask'][i], c['last'][i]
                offset += n
        level_seen = array('H')
        times, counts, bids, best_bid, best_ask, ticks, qty, side, valid = (array('I'), array('H'), array('H'), array('i'),
                                                                            array('i'), array('i'), array('f'), array('B'), array('H'))
        for bucket_start in sorted(buckets):
            frames_seen, levels, bid, ask, _ = buckets[bucket_start]
            if bucket_start + TIER_MS[tier] <= start:
                continue
            times.append(bucket_start - origin)
            bids.append(0)
            best_bid.append(bid)
            best_ask.append(ask)
            valid.append(min(65535, frames_seen))
            if summary:
                counts.append(0)
                continue
            counts.append(len(levels))
            for tick in sorted(levels):
                total, seen, sides = levels[tick]
                ticks.append(tick)
                qty.append(total / seen)
                level_seen.append(min(65535, seen))
                side.append(sides)
    groups = {}
    for chunk_id in selected:
        _read_deadline(deadline)
        blob = blobs.get((chunk_id, trade_kind))
        if blob is None:
            continue
        _, c = unpack(blob)
        for i, at in enumerate(c['time']):
            key = (c['bucket'][i], c['tick'][i], c['side'][i])
            if not start - TRADE_GROUP_MS[tier] < at < end + TRADE_GROUP_MS[tier]:
                continue
            group = groups.get(key)
            if group is None:
                groups[key] = [at, c['volume'][i], c['prints'][i]]
            else:
                group[0] = min(group[0], at)
                group[1] += c['volume'][i]
                group[2] += c['prints'][i]
    trades = [(at, tick, side_code, volume, prints) for (_, tick, side_code), (at, volume, prints) in groups.items() if start <= at < end]
    if len(trades) > MAX_TRADES:
        trades.sort(key=lambda t: t[3], reverse=True)
        result['trades_truncated'] = True
        result['trades_min_volume'] = trades[MAX_TRADES - 1][3]
        trades = trades[:MAX_TRADES]
    trades.sort()
    t_time, t_tick, t_side, t_volume, t_prints = array('I'), array('i'), array('B'), array('f'), array('I')
    for at, tick, side_code, volume, prints in trades:
        t_time.append(at - origin)
        t_tick.append(tick)
        t_side.append(side_code)
        t_volume.append(volume)
        t_prints.append(prints)
    result['time_origin_ms'] = origin
    result['frames'] = dict(count=len(times), time=_b64(times), level_count=_b64(counts), bid_count=_b64(bids),
                            # Coarse tiers: samples in which each level was observed (its mean's basis).
                            seen=_b64(level_seen) if tier else None,
                            best_bid=_b64(best_bid), best_ask=_b64(best_ask), valid=_b64(valid),
                            tick=_b64(ticks), qty=_b64(qty), side=_b64(side), levels=len(ticks))
    result['trades'] = dict(count=len(t_time), time=_b64(t_time), tick=_b64(t_tick), side=_b64(t_side),
                            volume=_b64(t_volume), prints=_b64(t_prints))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    follower = sub.add_parser('follow', help='Continuously derive the current session from committed recordings.')
    follower.add_argument('--root', required=True, type=Path)
    follower.add_argument('--cache', required=True, type=Path)
    follower.add_argument('--poll-seconds', type=float, default=5.0)
    follower.add_argument('--parent-stdin', action='store_true', help='Exit when the supervising process closes stdin.')
    reader = sub.add_parser('read', help='Read one bounded window (JSON query on stdin).')
    reader.add_argument('--cache', required=True, type=Path)
    once = sub.add_parser('once', help='Derive pending segments once and print a summary.')
    once.add_argument('--root', required=True, type=Path)
    once.add_argument('--cache', required=True, type=Path)
    once.add_argument('--now-ms', type=int)
    args = parser.parse_args()
    if args.command == 'follow':
        if os.name == 'nt':
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000)  # Background CPU/IO/memory.
        stop = threading.Event()
        paused = threading.Event()
        if args.parent_stdin:
            paused.set()  # The parent explicitly grants IO after checking recording/replay pressure.
            def watch():
                try:
                    for command in sys.stdin:
                        if command.strip() == 'resume':
                            paused.clear()
                        elif command.strip() == 'pause':
                            paused.set()
                finally:
                    stop.set()
            threading.Thread(target=watch, daemon=True).start()
        follow(args.root, args.cache, poll_seconds=args.poll_seconds, stop=stop, paused=paused)
    elif args.command == 'once':
        deriver = Deriver(args.root, args.cache, clock=(lambda: args.now_ms) if args.now_ms else None)
        try:
            started = time.perf_counter()
            summary = deriver.run_once()
            print(json.dumps(dict(summary, seconds=round(time.perf_counter() - started, 2))))
        finally:
            deriver.close()
    else:
        try:
            query = json.loads(sys.stdin.read(4097))
            print(json.dumps(read_heatmap(args.cache, query, deadline=time.monotonic()+20), separators=(',', ':'), allow_nan=False))
        except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, RuntimeError):
            print(json.dumps({'error': 'Heatmap history could not be read.'}))
            raise SystemExit(1)


if __name__ == '__main__':
    main()
