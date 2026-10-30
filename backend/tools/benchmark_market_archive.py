"""Measure a synthetic full MNQ session in a NEW disposable local directory."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import random
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.market_archive import Archive, expected_minutes, native_bar, put_bar, refresh_day, stamp
from app.services.market_archive_events import event_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    archive = Archive('synthetic-sizing-only', args.output)
    contract = 'CON.F.US.MNQ.Z26'
    times = expected_minutes(date(2026, 10, 28))
    now = times[-1]+timedelta(minutes=2)
    rng = random.Random(42)
    tracemalloc.start()
    started = time.monotonic()
    with archive.transaction() as db:
        for at in times:
            price = 25000+rng.randrange(-200, 200)*.25
            put_bar(db, native_bar(dict(timestamp=at, open=price, high=price+5, low=price-5,
                                       close=price+1, volume=rng.randrange(10, 2000)), contract), stamp(now))
        refresh_day(db, contract, times[-1].date(), now, stamp(now))
    def size():
        return sum(p.stat().st_size for p in archive.path.parent.iterdir() if p.is_file())
    candle_bytes = size()
    for i in range(276):
        rows = []
        at = times[0]+timedelta(minutes=5*i)
        for j in range(25):
            price = 25000+((i+j)%77)*.25
            rows.append({**native_bar(dict(timestamp=at-timedelta(minutes=5*(25-j)), open=price,
                high=price+5, low=price-5, close=price+1, volume=1100), contract, 5),
                'fetched_at': stamp(at), 'first_fetched_at': stamp(at), 'provider_revision_hash': str(j)})
        archive.decision(dict(decision_id=i, config=32, at=stamp(at), contract=contract, candles=rows,
                             forecast={'forecasts': {'BUY': {'probability_net_positive': .52, 'expected_net_usd': -1.3}}}, action='HOLD'))
    decision_bytes = size()-candle_bytes
    batch = []
    for i in range(1380*12):
        at = times[0]+timedelta(seconds=5*i)
        bid = 25000+rng.randrange(-200, 200)*.25
        batch.append(event_record(1, 'GatewayQuote', dict(contractId=contract, timestamp=stamp(at), bestBid=bid,
                               bestAsk=bid+.25, lastPrice=bid), at+timedelta(milliseconds=200)))
        if len(batch) == 256:
            archive.events(batch)
            batch = []
    if batch:
        archive.events(batch)
    quote_bytes = size()-candle_bytes-decision_bytes
    _, peak = tracemalloc.get_traced_memory()
    print(json.dumps(dict(workload='synthetic representative sizing, not feed coverage', session_minutes=1380,
        quote_interval_seconds=5, candles_and_derived_bytes=candle_bytes, decision_increment_bytes=decision_bytes,
        quote_increment_bytes=quote_bytes, projected_250_session_year_bytes={
            'candles_and_derived': candle_bytes*250, 'decisions': decision_bytes*250, 'sampled_quotes': quote_bytes*250},
        peak_python_traced_bytes=peak, elapsed_seconds=round(time.monotonic()-started, 3),
        exclusions='New model versions, extra corrections and broker events; no automatic deletion; allow 2x headroom'), indent=2))


if __name__ == '__main__':
    main()
