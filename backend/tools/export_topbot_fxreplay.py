"""Export a supplied frozen observation model as a local FXReplay V2 indicator.

No training, database, broker calls, or source-artifact changes. Keep generated
path-containing scripts under TOPSIGNAL_DATABENTO_CACHE_DIR, outside Git.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import math
import struct

import numpy as np
from scipy.stats import t as student_t

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.probabilistic_protocol import digest, protocol
from app.services.probabilistic_strategy import Features, PathModel, ET
from app.services.topbot_session import entry_boundary_reason


def build_payload(artifact: dict) -> tuple[dict, PathModel]:
    if (artifact.get("purpose") != "dry_run_observation_only"
            or artifact.get("validation_status") != "unvalidated"
            or artifact.get("routing_allowed") is not False
            or artifact.get("protocol_sha256") != digest(protocol())
            or artifact.get("model_sha256") != digest(artifact.get("model"))):
        raise ValueError("Expected an intact, protocol-matching observation artifact")
    model = PathModel.from_dict(artifact["model"])
    if model.version != "bayesian_cells_v1":
        raise ValueError("Only the current Bayesian cells strategy is supported")
    if np.max(np.abs(model._feature_values)) > 50:
        raise ValueError("Training features exceed the model domain")
    days = sorted({s.features.day for s in model.samples})
    day_codes = {day: i for i, day in enumerate(days)}
    cells = [(2 if s.features.values[1] >= 0 else 0) + (1 if s.features.values[2] >= 0 else 0)
             for s in model.samples]
    states = []
    for cell in range(4):
        query = Features((0, 1 if cell & 2 else -1, 1 if cell & 1 else -1, 0), 4, 2,
                         model.trained_through + timedelta(days=1), "unused")
        weights = model.weights(query)
        totals = np.bincount(model._day_codes, weights=weights)
        effective = float(1 / np.sum(totals ** 2))
        states.append({"count": cells.count(cell), "effectiveDays": effective,
                       "critical": float(student_t.ppf(1 - .01 / 8, max(1, effective - 1)))})
    first_year = model.trained_through.year
    calendar_end = datetime(first_year + 2, 1, 1, tzinfo=timezone.utc)
    sessions = {}
    day = datetime(first_year, 1, 1, tzinfo=ET)
    while day < calendar_end:
        allowed = []
        for minute in range(575, 931, 5):
            stamp = day.replace(hour=minute // 60, minute=minute % 60).astimezone(timezone.utc)
            if entry_boundary_reason(stamp) is None:
                allowed.append(int(stamp.timestamp()))
        if allowed:
            sessions[str(allowed[0] // 86400)] = [allowed[0], allowed[-1]]
        day += timedelta(days=1)
    paths = [[cells[i], day_codes[s.features.day], *[v for bar in s.path for v in bar]]
             for i, s in enumerate(model.samples)]
    # FXReplay rejects array literals with more than 1,000 elements. Keep each
    # literal below that cap without removing or rounding any training paths.
    chunk_size = 800
    return {
        "version": model.version, "modelSha256": digest(artifact["model"]),
        "trainedThrough": model.trained_through.isoformat(),
        "earliestDecision": int((model.trained_through + timedelta(minutes=15)).timestamp()),
        "calendarEnd": int(calendar_end.timestamp()), "sessions": sessions,
        "poolMix": model.pool_mix, "dayCount": len(days), "states": states,
        "pathCount": len(paths), "pathChunkSize": chunk_size,
        "pathChunks": [paths[i:i + chunk_size] for i in range(0, len(paths), chunk_size)],
    }, model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    payload, model = build_payload(artifact)
    root = Path(os.environ.get("TOPSIGNAL_DATABENTO_CACHE_DIR", "backend/storage/databento")).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    destination = root.resolve() / "exports" / "fxreplay"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "TopBot-Current-Model-FXReplay.js"
    code = render_source(payload, model)
    output.write_text(code, encoding="utf-8")
    print(json.dumps({"output": str(output), "bytes": output.stat().st_size,
                      "training_paths": len(model.samples), "trained_through": payload["trainedThrough"],
                      "model_sha256": payload["modelSha256"]}))


def compact_payload(payload: dict, model: PathModel) -> dict:
    """Lossless tick/IEEE754 encoding, verified against every original float."""
    compact = {k: v for k, v in payload.items() if k != "pathChunks"}
    packed = bytearray()
    def uint(value):
        while value >= 128:
            packed.append((value & 127) | 128)
            value >>= 7
        packed.append(value)
    for i, sample in enumerate(model.samples):
        row = payload["pathChunks"][i // payload["pathChunkSize"]][i % payload["pathChunkSize"]]
        uint(row[0]); uint(row[1])
        norm = sample.features.volatility * math.sqrt(3)
        packed.extend(struct.pack("<d", norm))
        for value in row[2:]:
            tick = round(value * norm * 4)
            if tick * .25 / norm != value:
                raise ValueError("A training path cannot be represented losslessly in quarter-point ticks")
            uint(tick * 2 if tick >= 0 else -tick * 2 - 1)
    compact["encodedPaths"] = base64.b64encode(packed).decode("ascii")
    return compact


def render_source(payload: dict, model: PathModel) -> str:
    template = (ROOT / "scripts/fxreplay/topbot-model.template.js").read_text(encoding="utf-8")
    packed_json = json.dumps(compact_payload(payload, model), separators=(",", ":"), allow_nan=False)
    # A native JSON parse also avoids constructing a huge JavaScript literal AST.
    return template.replace("__TOPBOT_MODEL__", "JSON.parse(" + json.dumps(packed_json) + ")")


if __name__ == "__main__":
    main()
