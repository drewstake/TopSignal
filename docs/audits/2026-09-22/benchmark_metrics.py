"""Synthetic in-memory analytics timing; never imports or persists history."""
from datetime import datetime, timedelta, timezone
import json
import statistics
import time
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from app.services.projectx_metrics import TradeMetricSample, compute_trade_summary, compute_point_payoff_by_basis

results = []
for count in (1000, 10000, 100000):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = [TradeMetricSample(timestamp=start + timedelta(minutes=i),
                             pnl=25 if i % 3 else -30, fees=1.22, size=1, symbol="MNQ")
            for i in range(count)]
    timings = []
    for _ in range(3):
        before = time.perf_counter()
        compute_trade_summary(rows)
        compute_point_payoff_by_basis(rows, point_bases=["MNQ", "NQ", "MES", "ES"])
        timings.append(round((time.perf_counter() - before) * 1000, 2))
    results.append({"rows": count, "milliseconds": timings, "median_ms": statistics.median(timings)})
print(json.dumps(results, indent=2))
