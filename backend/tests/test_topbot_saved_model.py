from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from threading import Event

import pytest

from app.services import topbot_saved_model as saved, topbot_backtest as runner
from app.services.probabilistic_protocol import digest, protocol
from app.services.probabilistic_shadow import scope_hash
from app.services.topbot_backtest_jobs import BacktestRequest
from test_probabilistic_strategy import fitted, NOW


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    model = fitted("bayesian_cells_v1")
    artifact = {"owner_hash": scope_hash("production-owner"), "root_symbol": "MNQ", "protocol_sha256": digest(protocol()),
        "model": model.to_dict(), "model_sha256": digest(model.to_dict()), "created_at": NOW.isoformat(), "validation_status": "unvalidated"}
    raw = json.dumps(artifact).encode()
    file_hash = sha256(raw).hexdigest()
    directory = tmp_path/"backtest-models"/scope_hash("alice")
    directory.mkdir(parents=True)
    (directory/f"{file_hash}.json").write_bytes(raw)
    manifest = {"owner_hash": scope_hash("alice"), "source_owner_hash": artifact["owner_hash"], "file_sha256": file_hash,
        "model_sha256": artifact["model_sha256"], "source_label": "Verified synthetic test snapshot", "verified_at": NOW.isoformat(),
        "source_code_sha256": {name: sha256(Path(saved.__file__).with_name(name).read_text(encoding="utf-8").encode()).hexdigest()
            for name in ("probabilistic_strategy.py", "topbot_mathematical.py", "topbot_session.py")}}
    (directory/"active.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(saved, "default_cache_root", lambda: tmp_path)
    return model, artifact, directory, manifest


def test_snapshot_preserves_exact_weights_and_returns_no_training_arrays(snapshot):
    original, artifact, _, _ = snapshot
    model, metadata = saved.load_saved_model("alice", expected_hash=artifact["model_sha256"])
    assert model.to_dict() == original.to_dict()
    assert metadata["model_sha256"] == digest(original.to_dict())
    assert metadata["model_binding"] == "saved_running_model"
    assert not {"samples", "model", "path", "owner_hash"} & metadata.keys()


def test_other_owner_cannot_read_snapshot(snapshot):
    with pytest.raises(ValueError, match="No verified running"):
        saved.load_saved_model("bob")


def test_checksum_and_code_mismatch_fail_closed(snapshot):
    _, _, directory, manifest = snapshot
    path = directory/f"{manifest['file_sha256']}.json"
    raw = path.read_bytes()
    path.write_bytes(raw+b" ")
    with pytest.raises(ValueError, match="checksum changed"):
        saved.load_saved_model("alice")
    path.write_bytes(raw)
    manifest["source_code_sha256"]["probabilistic_strategy.py"] = "0"*64
    (directory/"active.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="logic differs"):
        saved.load_saved_model("alice")


def test_selection_is_pinned_to_model_hash(snapshot):
    with pytest.raises(ValueError, match="changed after selection"):
        saved.load_saved_model("alice", expected_hash="0"*64)


def test_saved_model_default_never_falls_back_to_refit(snapshot, monkeypatch):
    monkeypatch.setattr(runner, "fit_prior_model", lambda *a, **k: pytest.fail("No implicit refit"))
    payload = BacktestRequest(start=NOW, end=NOW+timedelta(days=1))
    assert payload.model_source == "saved_running_model"
    with pytest.raises(ValueError, match="No verified running"):
        runner.run(payload, owner="bob", cancel=Event(), progress=lambda *a: None)


def test_training_overlap_is_rejected_before_candle_loading(snapshot, monkeypatch):
    model, _, _, _ = snapshot
    monkeypatch.setattr(runner, "get_default_databento_cache", lambda: pytest.fail("Must reject before loading history"))
    start = model.trained_through
    with pytest.raises(ValueError, match="avoid replaying its training data"):
        runner.run(BacktestRequest(start=start, end=start+timedelta(days=1)), owner="alice", cancel=Event(), progress=lambda *a: None)


def test_saved_model_run_does_not_train(snapshot, monkeypatch):
    from types import SimpleNamespace
    from test_probabilistic_strategy import candles
    model, artifact, _, _ = snapshot
    rows = candles(25)
    source = [SimpleNamespace(candle_timestamp=r.timestamp, open_price=r.open, high_price=r.high, low_price=r.low,
        close_price=r.close, volume=r.volume, source_raw_symbol="MNQU6", source_file_sha256="source") for r in rows]
    monkeypatch.setattr(runner, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=lambda *a: (rows[0].timestamp, NOW+timedelta(minutes=20)), open_candles=lambda **k: source))
    monkeypatch.setattr(runner, "fit_prior_model", lambda *a, **k: pytest.fail("Saved-model runs must not refit"))
    output = runner.run(BacktestRequest(start=NOW, end=NOW+timedelta(minutes=20), model_sha256=artifact["model_sha256"]),
        owner="alice", cancel=Event(), progress=lambda *a: None)
    assert output["config_snapshot"]["model_sha256"] == artifact["model_sha256"]
    assert output["config_snapshot"]["training_paths"] == len(model.samples)
    assert output["entry_diagnostics"]["model_binding"] == "saved_running_model"
