from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from tools import research_legacy_fees as driver


def fake_comparison(result, *, fail=False):
    class Engine:
        def __init__(self, fee, slippage):
            self.settings = SimpleNamespace(commission_per_contract=fee, slippage_ticks=slippage)

        def run(self):
            if fail:
                raise RuntimeError("synthetic engine failure")
            return result

    replay = SimpleNamespace(BacktestEngine=Engine)

    def main():
        args = sys.argv
        option = lambda name: args[args.index(name) + 1]
        returned = Engine(float(option("--commission-per-side")), int(option("--slippage"))).run()
        assert returned is result
        original_report = {"results": {option("--variants"): {
            "trades_sha256": sha256(json.dumps(returned["trades"], sort_keys=True).encode()).hexdigest()}}}
        Path(option("--output")).write_text(json.dumps(original_report), encoding="utf-8")

    return SimpleNamespace(__file__="frozen-comparison.py", main=main), replay


def example_result(fee=0.61):
    return {"assumptions": {"commission_per_contract": fee, "slippage_ticks": 2},
            "trades": [{"id": "synthetic", "quantity": 1, "commission": fee * 2,
                        "gross_pnl": 10.0, "net_pnl": 10.0-fee*2,
                        "nested_execution_details": {"all_fields": [1, 2, 3]}}],
            "equity_curve": [{"timestamp": "synthetic", "equity": 50000}], "extra_complete_result": {"retained": True}}


def example_case(fee=0.61):
    return {"key": "synthetic", "variant": "no_chase", "period": "selection", "slippage_ticks": 2, "commission_per_side": fee}


@pytest.mark.parametrize("fee", [0.61, 1.20])
def test_capture_preserves_complete_result_and_ledger_without_changing_engine(tmp_path, fee):
    result = example_result(fee)
    comparison, replay = fake_comparison(result)
    original_run, original_argv = replay.BacktestEngine.run, sys.argv
    output = tmp_path / "case"
    status = driver.capture_case(comparison, replay, case=example_case(fee), directory=output,
                                 cache_root=tmp_path / "cache", fixture=tmp_path / "fixture.py")
    assert status["status"] == "completed"
    assert driver.read_json(output / "replay.json") == result
    assert driver.read_json(output / "trades.json") == result["trades"]
    assert replay.BacktestEngine.run is original_run
    assert sys.argv is original_argv


def test_capture_refuses_existing_output_without_touching_it(tmp_path):
    output = tmp_path / "case"
    output.mkdir()
    sentinel = output / "historical.json"
    sentinel.write_text("preserve me", encoding="utf-8")
    comparison, replay = fake_comparison(example_result())
    original_run = replay.BacktestEngine.run
    with pytest.raises(FileExistsError):
        driver.capture_case(comparison, replay, case=example_case(), directory=output,
                            cache_root=tmp_path / "cache", fixture=tmp_path / "fixture.py")
    assert sentinel.read_text() == "preserve me"
    assert replay.BacktestEngine.run is original_run


def test_capture_failure_is_preserved_and_engine_restored(tmp_path):
    comparison, replay = fake_comparison(example_result(), fail=True)
    original_run, original_argv = replay.BacktestEngine.run, sys.argv
    output = tmp_path / "case"
    status = driver.capture_case(comparison, replay, case=example_case(), directory=output,
                                 cache_root=tmp_path / "cache", fixture=tmp_path / "fixture.py")
    assert status["status"] == "failed"
    assert driver.read_json(output / "failed.json")["error_type"] == "RuntimeError"
    assert driver.read_json(output / "failed.json")["error_message"] == "synthetic engine failure"
    assert "RuntimeError: synthetic engine failure" in (output / "failure.log").read_text()
    assert not (output / "completed.json").exists()
    assert replay.BacktestEngine.run is original_run
    assert sys.argv is original_argv


def test_wrong_round_trip_fee_fails_and_retains_offending_result(tmp_path):
    result = example_result(0.61)
    result["trades"][0]["commission"] = 2.40
    comparison, replay = fake_comparison(result)
    output = tmp_path / "case"
    status = driver.capture_case(comparison, replay, case=example_case(), directory=output,
                                 cache_root=tmp_path / "cache", fixture=tmp_path / "fixture.py")
    assert status["status"] == "failed"
    assert "round-trip fee" in status["error_message"]
    assert driver.read_json(output / "replay.json") == result
    assert driver.read_json(output / "trades.json") == result["trades"]
    assert not (output / "completed.json").exists()


def test_prepared_source_and_cache_changes_are_rejected(tmp_path):
    source = tmp_path / "sources/example.py"
    source.parent.mkdir()
    source.write_text("original source", encoding="utf-8")
    cache = tmp_path / "array.npy"
    cache.write_bytes(b"original cache bytes")
    manifest = {"status": "prepared_not_launched", "cases": driver.cases(),
                "source_files": {"example.py": {"sha256": driver.digest(source)}},
                "cache": {"pinned_files": {str(cache): {"sha256": driver.digest(cache), "bytes": cache.stat().st_size}}}}
    driver.write_new(tmp_path / "manifest.json", manifest)
    assert driver.validate_prepared(tmp_path) == manifest
    source.write_text("changed source", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Frozen source hash mismatch"):
        driver.validate_prepared(tmp_path)
    source.write_text("original source", encoding="utf-8")
    cache.write_bytes(b"modified cache bytes")
    with pytest.raises(RuntimeError, match="Pinned cache/archive changed"):
        driver.validate_prepared(tmp_path)


def test_execution_rejects_live_working_tree_driver_before_loading_engine(tmp_path):
    with pytest.raises(RuntimeError, match="captured driver"):
        driver.execute(SimpleNamespace(prepared_dir=tmp_path, period="selection"))
