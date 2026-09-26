"""Read-only causal roll history and provider parity; never import to SQL."""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from .probabilistic_strategy import BAR, Candle, ET, utc
from .probabilistic_protocol import protocol


def read_databento(root: Path, *, owner: str = "offline-research") -> tuple[list[Candle], str, dict]:
    from .databento_cache import DatabentoReplayStore
    store = DatabentoReplayStore(root, build_missing_timeframes=False)
    bounds = store.history_bounds("MNQ")
    if not bounds:
        raise ValueError("No local MNQ replay history. An explicit history import is required.")
    start = max(bounds[0], datetime.fromisoformat(protocol()["first_research_session"]).replace(tzinfo=ET))
    sequence = store.open_candles(user_id=owner, contract_id="CON.F.US.MNQ", root_symbol="MNQ",
        unit="minute", unit_number=1, start=start, end=bounds[1], closed_by=bounds[1])
    rows, quality = aggregate_observed_minutes(sequence, owner=owner, closed_by=bounds[1])
    fingerprint = sha256()
    for row in rows:
        fingerprint.update(json.dumps(asdict(row), default=str, sort_keys=True, allow_nan=False).encode())
    quality.update(source="local_databento_causal_volume_roll", roll_policy=protocol()["roll_policy"],
                   timestamp_convention="UTC interval start; label/decision use interval end",
                   no_trade_minutes="Unobserved minutes are flagged, never interpolated; provider outages cannot be inferred from absence alone.")
    return rows, fingerprint.hexdigest(), quality


def aggregate_observed_minutes(sequence, *, owner: str, closed_by: datetime | None = None) -> tuple[list[Candle], dict]:
    rows, group = [], []
    current = None
    first_observation = None
    quality = {"observed_minutes": 0, "empty_minutes_by_day": {}, "mixed_contract_buckets_dropped": 0,
               "dec_31_2021_0000_to_1700_et_minutes": 0}
    def flush():
        if not group:
            return
        symbols = {r.source_raw_symbol for r in group}
        if len(symbols) != 1 or not next(iter(symbols)):
            quality["mixed_contract_buckets_dropped"] += 1
            return
        stamp = datetime.fromtimestamp(current, timezone.utc)
        if first_observation is not None and stamp < first_observation:
            return  # Known archive truncation is not an unobserved quiet minute.
        if closed_by is not None and stamp + BAR > utc(closed_by):
            return
        day = stamp.astimezone(ET).date().isoformat()
        quality["empty_minutes_by_day"][day] = quality["empty_minutes_by_day"].get(day, 0) + 5 - len(group)
        # Identity carries the delivery contract from the causal roll stream.
        rows.append(Candle(stamp, float(group[0].open_price), max(float(r.high_price) for r in group),
                           min(float(r.low_price) for r in group), float(group[-1].close_price),
                           sum(float(r.volume) for r in group), "CON.F.US.MNQ."+next(iter(symbols)), owner))
    previous = None
    for row in sequence:
        stamp = utc(row.candle_timestamp)
        if first_observation is None:
            first_observation = stamp
        if previous is not None and stamp <= previous:
            raise ValueError("Replay minutes must be unique and chronological")
        previous = stamp
        quality["observed_minutes"] += 1
        local = stamp.astimezone(ET)
        if local.date().isoformat() == "2021-12-31" and local.hour < 17:
            quality["dec_31_2021_0000_to_1700_et_minutes"] += 1
        bucket = int(stamp.timestamp()) // 300 * 300
        if bucket != current:
            flush()
            group = []
            current = bucket
        group.append(row)
    flush()
    return rows, quality


def parity_report(databento: list[Candle], projectx: list[Candle]) -> dict:
    # Raw delivery symbols vary in spelling; contract month/year must agree.
    import re
    def key(row):
        suffix = row.contract_id.split(".")[-1]
        match = re.search(r"([FGHJKMNQUVXZ])(\d{1,2})$", suffix)
        if match:
            digits = int(match[2])
            cycle = 10 if len(match[2]) == 1 else 100
            base = row.timestamp.year // cycle * cycle + digits
            year = min((base-cycle, base, base+cycle), key=lambda value: abs(value-row.timestamp.year))
            delivery = (match[1], year)
        else:
            delivery = suffix
        return row.timestamp, delivery
    reference = {key(row): row for row in databento}
    matched = exact = 0
    differences = dict.fromkeys(("open", "high", "low", "close", "volume"), 0)
    for row in projectx:
        other = reference.get(key(row))
        if other is None:
            continue
        matched += 1
        errors = {name: getattr(row, name) != getattr(other, name) for name in differences}
        for name, value in errors.items():
            differences[name] += int(value)
        exact += not any(errors.values())
    return {"projectx_bars": len(projectx), "overlap_bars": matched, "exact_ohlcv_matches": exact,
            "exact_match_rate": exact/matched if matched else None, "mismatches": differences,
            "timestamp_convention": "UTC bar open; compare only identical delivery and five-minute interval",
            "status": "measured" if matched else "no_overlap", "trusted_parity": False}
