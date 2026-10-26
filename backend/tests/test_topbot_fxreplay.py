"""Numerical parity for the exported FXReplay core; not an FXReplay engine test."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from app.services.probabilistic_protocol import digest, protocol
from app.services.probabilistic_strategy import Candle, Features, PathModel, Sample, features
from tools.export_topbot_fxreplay import ROOT, build_payload, compact_payload


def check_parity(model, payload, directory):
    rng = np.random.default_rng(20261024)
    cases = []
    for cell in range(4):
        for sigma in [.1, .5, 2, 2.31, 4, 7, 10, 14, 25 / np.sqrt(3), 15]:
            stop = max(4, np.ceil(sigma * np.sqrt(3) / .25) * .25)
            values = (0, 1 if cell & 2 else -1, 1 if cell & 1 else -1, 0)
            f = Features(values, stop, sigma, model.trained_through + timedelta(days=1), "unused")
            expected = model.forecast(f)
            cases.append({"f": {"sigma": sigma, "values": values, "stop": stop,
                                 "target": np.ceil(stop * 1.5 / .25) * .25, "cell": cell},
                          "expected": expected,
                          "action": expected["research_action"] if stop <= 25 else "HOLD"})
    feature_cases = []
    for i in range(20):
        candles = []
        previous = 20000.
        for j in range(21):
            c = previous + float(rng.normal(0, 3))
            candles.append(Candle(datetime(2026, 10, 23, 12, tzinfo=timezone.utc) + timedelta(minutes=5*j),
                                  previous, max(previous, c) + 1, min(previous, c) - 1, c,
                                  float(rng.integers(100, 2000)), "CON.F.US.MNQ.Z26", "fixture"))
            previous = c
        f = features(candles, as_of=candles[-1].timestamp + timedelta(minutes=5))
        rows = [{"t": int(b.timestamp.timestamp()), "o": b.open, "h": b.high,
                 "l": b.low, "c": b.close, "v": b.volume} for b in candles]
        feature_cases.append({"rows": rows, "values": f.values, "sigma": f.volatility, "stop": f.stop_points})
    source = (ROOT / "scripts/fxreplay/topbot-model.template.js").read_text(encoding="utf-8")
    core = source[source.index("function topbotFeatures"):source.index("const currentTime =")]
    runner = directory / "check-fxreplay.cjs"
    data = directory / "check-fxreplay.json"
    data.write_text(json.dumps({"model": compact_payload(payload, model),
                               "expectedPaths": payload["pathChunks"],
                               "cases": cases, "features": feature_cases}), encoding="utf-8")
    runner.write_text(core + """
const assert = require('node:assert/strict');
const data = JSON.parse(require('node:fs').readFileSync(process.argv[2], 'utf8'));
topbotDecodeModel(data.model);
assert.deepEqual(data.model.pathChunks, data.expectedPaths);
let maxError = 0;
function near(actual, expected) {
  const error = Math.abs(actual - expected); maxError = Math.max(maxError, error);
  assert.ok(error < 1e-9, `${actual} != ${expected}`);
}
let buys = 0, sells = 0, holds = 0;
for (const c of data.cases) {
  const result = topbotForecast(data.model, c.f);
  assert.equal(result.action, c.action === 'NO_TRADE' ? 'HOLD' : c.action);
  if (result.action === 'BUY') buys++; else if (result.action === 'SELL') sells++; else holds++;
  for (const action of ['BUY', 'SELL']) {
    near(result[action].mean, c.expected.forecasts[action].expected_net_usd);
    near(result[action].lower, c.expected.forecasts[action].lower_utility_usd);
    near(result[action].se, c.expected.forecasts[action].standard_error_usd);
  }
}
for (const c of data.features) {
  const f = topbotFeatures(c.rows);
  near(f.sigma, c.sigma); near(f.stop, c.stop);
  f.values.forEach((value, i) => near(value, c.values[i]));
  const broken = JSON.parse(JSON.stringify(c.rows));
  broken[10].t += 300; assert.equal(topbotFeatures(broken), null);
  broken[10] = {...c.rows[10], v: 0}; assert.equal(topbotFeatures(broken), null);
}
console.log(JSON.stringify({forecasts: data.cases.length, featureWindows: data.features.length,
                           invalidWindows: data.features.length * 2, buys, sells, holds, maxError}));
""", encoding="utf-8")
    result = subprocess.run(["node", str(runner), str(data)], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for cross-language parity")
@pytest.mark.parametrize("pool_mix", [0, .5, 1])
def test_fxreplay_matches_python_with_synthetic_paths(tmp_path, pool_mix):
    rng = np.random.default_rng(42)
    samples = []
    start = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)
    for i in range(1363):
        stamp = start + timedelta(days=i // 8, minutes=(i % 8) * 15)
        cell = i % 4
        values = (0., 1. if cell & 2 else -1., 1. if cell & 1 else -1., 0.)
        # Opposite drift in the momentum cells exercises BUY, SELL and HOLD.
        path, previous = [], 0.
        for j in range(3):
            opening = previous if j == 0 else previous + float(rng.normal(0, .3))
            close = opening + (1.5 if cell & 2 else -1.5) + float(rng.normal(0, .2))
            path.append((opening, max(opening, close) + .2, min(opening, close) - .2, close))
            previous = close
        f = Features(values, 4, 2, stamp, stamp.date().isoformat())
        norm = 2 * np.sqrt(3)
        tick_path = tuple(tuple(round(v * norm * 4) * .25 / norm for v in bar) for bar in path)
        samples.append(Sample(f, tick_path, stamp + timedelta(minutes=15)))
    model = PathModel("bayesian_cells_v1", tuple(samples), pool_mix=pool_mix)
    artifact = {"purpose": "dry_run_observation_only", "validation_status": "unvalidated",
                "routing_allowed": False, "protocol_sha256": digest(protocol()),
                "model": model.to_dict(), "model_sha256": digest(model.to_dict())}
    payload, _ = build_payload(artifact)
    assert payload["pathCount"] == 1363
    assert [len(chunk) for chunk in payload["pathChunks"]] == [800, 563]
    def assert_literal_limit(value):
        if isinstance(value, list):
            assert len(value) <= 1000
            for item in value:
                assert_literal_limit(item)
        elif isinstance(value, dict):
            for item in value.values():
                assert_literal_limit(item)
    assert_literal_limit(payload)
    report = check_parity(model, payload, tmp_path)
    assert report["forecasts"] == 40
    if pool_mix == 0:
        assert report["buys"] > 0 and report["sells"] > 0 and report["holds"] > 0
