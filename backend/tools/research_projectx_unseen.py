"""Frozen, offline evaluation adapter. Preparation reads only three metadata files.

Real price reads require a separate root authorization artifact after A07 audit.
No production data store, provider endpoint, strategy setting or engine is edited.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
POOL = BACKEND / "storage/research/quarantine/projectx-mnqu26-20260904/complete-newer-pool"
PROTOCOL = ROOT / "docs/topbot-unseen-opening-drive-protocol-2026-09-04.md"
A06 = BACKEND / "storage/research/experiments/20260905T005448.370797Z-fee061-opening-8482f9451110"
MANIFEST_SHA = "25e354280208ae795f402dd88155b3f87c1652b4b976c5b31002fc462dff4576"
QA_SHA = "f71d270c9ef31d56381bd8e6c6ceba1c83b4bea4458113de33c2eaf466d413ba"
CONTRACT = "CON.F.US.MNQ.U26"
SOURCE = "projectx_quarantined_dated_contract"
REVISION = "projectx_unseen_opening_drive_v1"
ORIGINAL_FIXTURE_NORMALIZED_SHA = "d0230d261f3e5f00f6f876756086b873987eb540ae2c6a6b1798ba2b376d80e6"
UTC = timezone.utc
MINUTE = timedelta(minutes=1)
START = datetime(2026, 7, 12, 22, tzinfo=UTC)
END = datetime(2026, 9, 4, 21, tzinfo=UTC)
READY = datetime(2026, 7, 13, 14, tzinfo=UTC)
SCENARIOS = (1, 2, 4)
CRITERIA = {
    "preliminary_only": True, "confirmation_minimum_trades": 200,
    "confirmation_minimum_calendar_months": 6, "complete_sessions_expected": 40,
    "necessary_preliminary_conditions": ["positive_net_at_1_tick", "positive_net_at_2_ticks"],
    "four_tick_role": "disclosed stress; same observations, not independent evidence",
    "data_or_execution_integrity_failure": "invalid_evaluation_stop_no_repair_or_reselection",
    "zero_trades": "inconclusive_no_evidence",
    "failed_positive_net_condition": "fails_predeclared_preliminary_screen_not_a_statistical_proof_of_no_edge",
    "all_conditions_met": "preliminary_support_only_confirmation_requirements_remain_unmet",
    "no_retuning_or_date_subselection_after_opening_pool": True,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(value):
    return sha_bytes(json.dumps(value, sort_keys=True, default=str).encode())


def instant(value):
    require(isinstance(value, str), "timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp must have timezone")
    return result.astimezone(UTC)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def offline():
    for key in tuple(os.environ):
        if key.startswith(("PROJECTX_", "TOPSTEP_", "TOPSTEPX_", "DATABENTO_", "SUPABASE_")) or key in {"DATABASE_URL", "CREDENTIALS_ENCRYPTION_KEY"}:
            os.environ.pop(key, None)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite+pysqlite:///:memory:",
        TOPSIGNAL_DB_SCHEMA_INIT="skip", TOPSIGNAL_LIVE_EXECUTION_ENABLED="false",
        TOPSIGNAL_BOT_WORKER_ENABLED="false", TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false")

    def deny_network(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Unseen evaluator is offline")
    sys.addaudithook(deny_network)


def read_metadata(pool=POOL):
    """Strict allowlist: never stat, hash, parse or open any history price file."""
    blobs = {name: (pool / name).read_bytes() for name in ("manifest.json", "structural-qa.json", "contract-lookup.json")}
    require(sha_bytes(blobs["manifest.json"]) == MANIFEST_SHA, "pool manifest changed")
    require(sha_bytes(blobs["structural-qa.json"]) == QA_SHA, "pool structural QA changed")
    manifest, qa, contract = (json.loads(blobs[name]) for name in ("manifest.json", "structural-qa.json", "contract-lookup.json"))
    require(manifest["contract_id"] == CONTRACT and manifest["contract_metadata_verified"], "dated contract not verified")
    require(sha_bytes(blobs["contract-lookup.json"]) == manifest["files"]["contract-lookup.json"]["sha256"], "contract metadata hash changed")
    mapped = manifest["databento_dated_reference"]["definition"]
    require(mapped["raw_symbol"] == "MNQU6" and mapped["contract_key"] == "MNQU6@2026" and mapped["instrument_id"] == 42004800, "dated crosswalk mismatch")
    c = contract["response"]["contract"]
    require(c["id"] == CONTRACT and c["name"] == "MNQU6" and c["tickSize"] == .25 and c["tickValue"] == .5, "contract tick or identity mismatch")
    require(qa["checked_rows"] == 55240 and not any(qa["errors"].values()) and qa["bar_source_is_databento"] is False, "pool structural QA did not pass")
    require(manifest["coverage"]["total_rows"] == 55240 and manifest["coverage"]["duplicate_timestamps_across_windows"] == 0, "pool coverage mismatch")
    days = manifest["coverage"]["rows_by_trading_day_et"]
    require(days["2026-07-10"] == 40 and len(days) == 41 and all(count == 1380 for day, count in days.items() if day != "2026-07-10"), "expected warmup and forty complete sessions unavailable")
    require(instant(manifest["end_exclusive_utc"]) == END and instant(manifest["coverage"]["first_utc"]) == datetime(2026,7,10,20,20,tzinfo=UTC), "pool boundary mismatch")
    for window in manifest["windows"]:
        name = window["file"]
        require(Path(name).name == name and name.startswith("history-") and name.endswith(".json"), "invalid price filename")
        require(not window["missing_regular_session_minutes"] and not window["out_of_regular_session_minutes"] and not window["duplicate_timestamps"], "window coverage invalid")
    return {"manifest": manifest, "structural_qa": qa, "contract": contract,
        "metadata_sha256": {name: sha_bytes(blob) for name, blob in blobs.items()},
        "history_file_hashes_from_prior_manifest_only": {w["file"]: manifest["files"][w["file"]] for w in manifest["windows"]},
        "history_price_files_opened_during_preparation": False}


@dataclass(frozen=True, slots=True)
class ProjectXCandle:
    candle_timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    source_file_sha256: str
    fetched_at: datetime | None
    unit_number: int = 1
    user_id: str = "offline-unseen"
    contract_id: str = CONTRACT
    symbol: str = "MNQ"
    live: bool = False
    unit: str = "minute"
    is_partial: bool = False
    raw_payload: None = None
    source: str = SOURCE
    source_raw_symbol: str = "MNQU6"
    # This is the independently verified Databento definition crosswalk ID,
    # explicitly NOT a numeric identifier returned by ProjectX.
    source_instrument_id: int = 42004800
    source_instrument_id_namespace: str = "databento_dated_definition_crosswalk"
    roll_policy_version: str = "fixed_MNQU6_2026_no_roll_no_adjustment"

    @property
    def nominal_close_time(self):
        return self.candle_timestamp + MINUTE * self.unit_number


def number(value):
    require(not isinstance(value, bool) and isinstance(value, (int, float, Decimal)), "OHLCV numeric type invalid")
    result = Decimal(str(value))
    require(result.is_finite(), "OHLCV nonfinite")
    return result


def parse_window(envelope, *, window, initiated_at, file_sha256, expiry_ns):
    """Pure parser. Only synthetic envelopes may be supplied before authorization."""
    require(isinstance(envelope, dict) and set(envelope) == {"request", "response"}, "invalid history envelope")
    request, response = envelope["request"], envelope["response"]
    expected = {"contractId": CONTRACT, "live": False, "startTime": window["start_utc"],
        "endTime": window["end_exclusive_utc"], "unit": 2, "unitNumber": 1, "limit": 20000, "includePartialBar": False}
    require(request == expected, "history request differs from frozen window")
    require(set(response) == {"bars", "success", "errorCode", "errorMessage"} and response["success"] is True and response["errorCode"] == 0 and response["errorMessage"] is None and isinstance(response["bars"], list), "history response invalid")
    begin, end, fetched = instant(window["start_utc"]), instant(window["end_exclusive_utc"]), instant(initiated_at)
    require(begin < end <= fetched and end-begin <= timedelta(days=10), "request closed bounds invalid")
    require(len(response["bars"]) == window["rows"] and len(response["bars"]) < 20000, "history row count invalid")
    rows = []
    for bar in response["bars"]:
        require(isinstance(bar, dict) and set(bar) == {"t", "o", "h", "l", "c", "v"}, "canonical row schema invalid")
        at = instant(bar["t"])
        require(not at.second and not at.microsecond and begin <= at and at+MINUTE <= end and at+MINUTE <= fetched and int(at.timestamp())*10**9 < expiry_ns, "bar timestamp/closed bounds invalid")
        opening, high, low, closing, volume = (number(bar[key]) for key in ("o", "h", "l", "c", "v"))
        require(all(x > 0 and x % Decimal('.25') == 0 for x in (opening, high, low, closing)), "OHLC positivity/tick alignment invalid")
        require(low <= min(opening, closing) <= max(opening, closing) <= high, "OHLC envelope invalid")
        require(volume >= 0 and volume == volume.to_integral_value(), "volume invalid")
        rows.append(ProjectXCandle(at, float(opening), float(high), float(low), float(closing), int(volume), file_sha256, fetched))
    rows.sort(key=lambda row: row.candle_timestamp)
    require(len({r.candle_timestamp for r in rows}) == len(rows), "duplicate minute")
    return rows


def complete_five_minute_bars(minutes):
    """UTC five-minute boundaries equal ET session anchoring in this fixed pool."""
    groups = {}
    seen = set()
    for row in minutes:
        at = row.candle_timestamp
        require(at not in seen, "duplicate minute before aggregation")
        seen.add(at)
        require(row.unit_number == 1 and row.source == SOURCE and row.contract_id == CONTRACT and row.source_raw_symbol == "MNQU6" and row.source_instrument_id == 42004800, "mixed source/delivery or timeframe")
        require(at.tzinfo is not None and not at.second and not at.microsecond, "unaligned minute")
        bucket = at.replace(minute=at.minute-at.minute % 5, second=0, microsecond=0)
        groups.setdefault(bucket, []).append(row)
    bars = []
    for start, group in sorted(groups.items()):
        group.sort(key=lambda row: row.candle_timestamp)
        if [row.candle_timestamp for row in group] != [start+MINUTE*i for i in range(5)]:
            continue
        bars.append(ProjectXCandle(start, group[0].open_price, max(r.high_price for r in group),
            min(r.low_price for r in group), group[-1].close_price, sum(r.volume for r in group),
            fingerprint({"method": "five_complete_observed_minutes", "constituent_file_hashes": sorted({r.source_file_sha256 for r in group})}),
            max((r.fetched_at for r in group if r.fetched_at is not None), default=None), unit_number=5))
    return bars


def make_config(fixture):
    from app.models import BotConfig
    from app.services.topbot import TOPBOT_SETTINGS
    settings = deepcopy(TOPBOT_SETTINGS)
    settings.update(fixture.get_settings("opening_drive"))
    require(all(settings[k] == 1 for k in ("order_size", "max_contracts", "max_open_position")), "one contract required")
    require(settings["lookback_bars"] == 200 and settings["max_daily_loss"] == 250 and settings["cooldown_seconds"] == 300 and settings["max_trades_per_day"] == 3, "original risk settings changed")
    return BotConfig(id=1, user_id="offline-unseen", account_id=1, name="Frozen opening drive unseen evaluation",
        provider="projectx", enabled=False, execution_mode="dry_run", contract_id=CONTRACT, **settings)


def make_engine(minutes, *, slippage, start=START, end=END):
    from app.services import bot_backtesting as replay
    from tools.fixtures import topbot_research as fixture
    from tools.research_topbot import make_engine_class
    require(slippage in SCENARIOS, "unregistered cost scenario")
    require(sha_bytes(Path(fixture.__file__).read_text(encoding="utf-8").encode()) == ORIGINAL_FIXTURE_NORMALIZED_SHA, "original fixture changed")
    signals = complete_five_minute_bars(minutes)
    config = make_config(fixture)
    return make_engine_class(replay, fixture, "opening_drive")(
        config=config, candles=signals, replay_streams={replay._topbot_asset_stream_key("minute", 5): signals},
        execution_candles=[row for row in minutes if start <= row.candle_timestamp and row.nominal_close_time <= end],
        settings=replay.BacktestSettings(start=start, end=end, starting_balance=50000,
            commission_per_contract=.61, slippage_ticks=slippage, tick_size=.25, tick_value=.5),
        entry_delay_minutes=0, roll_exit_candle_resolver=None)


def label_result(result, *, source_fingerprint, candidate):
    """Correct metadata only; retain economic output and generic original defaults."""
    assumptions = result["assumptions"]
    replacements = {"historical_source": SOURCE, "source_fingerprint": source_fingerprint,
        "market_data": "ProjectX documented one-minute OHLCV for fixed MNQU6@2026; only five complete observed minutes form each signal bar; no back adjustment",
        "roll_gap_rule": "fixed dated contract; any unexpected delivery rejected before replay",
        "roll_policy_version": "fixed_MNQU6_2026_no_roll_no_adjustment",
        "strategy_revision": candidate["revision"], "candidate_name": "opening_drive",
        "source_instrument_id_namespace": "databento_dated_definition_crosswalk; bar prices supplied by ProjectX",
        "adapter_revision": REVISION}
    original_defaults = {key: assumptions.get(key) for key in replacements}
    assumptions.update(replacements)
    result["provenance"] = {"source": SOURCE, "pool_source_fingerprint": source_fingerprint,
        "contract_id": CONTRACT, "raw_symbol": "MNQU6", "contract_key": "MNQU6@2026",
        "mapped_databento_definition_instrument_id": 42004800, "prices_from_databento": False,
        "candidate": deepcopy(candidate), "base_engine_version": assumptions["engine_version"],
        "engine_generic_metadata_defaults_replaced": original_defaults}
    return result


def prepare(output_root, pool=POOL):
    from tools.research_topbot import capture_sources, create_run_directory, write_new_json
    from tools.fixtures import topbot_research as fixture
    from app.services import bot_backtesting as replay
    metadata = read_metadata(pool)
    fixture_path = Path(fixture.__file__).resolve()
    require(sha_bytes(fixture_path.read_text(encoding="utf-8").encode()) == ORIGINAL_FIXTURE_NORMALIZED_SHA, "original fixture changed")
    a06_bytes = (A06 / "manifest.json").read_bytes()
    a06 = json.loads(a06_bytes)
    require(a06["hypotheses"]["opening_drive"] == fixture.CANDIDATES["opening_drive"], "original A06 candidate changed")
    lineage_files = {name: item for name, item in a06["code"]["files"].items()
        if name.startswith("backend/app/") or name in {"backend/tools/research_topbot.py", "backend/tools/fixtures/topbot_research.py"}}
    for name, entry in lineage_files.items():
        require(sha_bytes((ROOT / name).read_bytes()) == entry["sha256"], "current engine/runner/rules differ from A06: "+name)
    directory = create_run_directory(output_root, "projectx-unseen-prepared")
    sources = capture_sources(directory, fixture_path)
    extra = {}
    for source in (PROTOCOL, BACKEND / "tests/test_research_projectx_unseen.py"):
        relative = source.relative_to(ROOT)
        payload = source.read_bytes()
        destination = directory / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(payload)
        extra[str(relative).replace('\\','/')] = {"sha256": sha_bytes(payload), "bytes": len(payload)}
    manifest = {"status": "frozen_preparation_awaiting_a07_audit_and_root_authorization", "adapter_revision": REVISION,
        "created_at": datetime.now(UTC).isoformat(), "pool_directory": str(pool.resolve()), "pool_metadata": metadata,
        "candidate": {"name": "opening_drive", "revision": fixture.REVISION, "definition": deepcopy(fixture.CANDIDATES["opening_drive"]),
            "original_fixture_normalized_sha256": ORIGINAL_FIXTURE_NORMALIZED_SHA, "config_before_engine_normalization": replay._config_snapshot(make_config(fixture))},
        "period": {"requested_start": START.isoformat(), "end_exclusive": END.isoformat(), "sessions": 40,
            "warmup_source": "same ProjectX pool only; July10 tail plus July12/13 overnight", "first_decision": READY.isoformat(),
            "expected_first_execution_after_known_warmup_deferral": (READY-MINUTE).isoformat(),
            "warmup_minute_count_at_first_decision": 1000, "warmup_five_minute_count": 200, "fresh_portfolio": True},
        "costs": {"commission_per_side": .61, "slippage_ticks_per_fill": list(SCENARIOS), "tick_size": .25, "tick_value": .5, "starting_balance": 50000},
        "criteria": CRITERIA, "code": sources, "extra_source_files": extra, "protocol_sha256": sha_bytes(PROTOCOL.read_bytes()),
        "a06_lineage": {"run_directory": str(A06), "original_manifest_sha256": sha_bytes(a06_bytes),
            "original_source_bundle_sha256": a06["code"]["combined_sha256"],
            "verified_identical_current_source_files": lineage_files, "candidate_definition_identical": True},
        "raw_price_hash_verification": "deferred until separate authorization; prior collection hashes copied without opening files",
        "execution_authorized": False}
    write_new_json(directory / "manifest.json", manifest)
    with (directory / "a06-manifest.json").open("xb") as stream:
        stream.write(a06_bytes)
    return directory


def validate_authorization(directory, approval):
    """An audit record, not a substitute for parent/user authorization in the task."""
    manifest = read_json(directory / "manifest.json")
    require(approval.get("permission") == "single_reserved_pool_evaluation" and approval.get("a07_audited_passed") is True,
        "A07 audit and separate root authorization required before price access")
    require(approval.get("prepared_manifest_sha256") == sha_bytes((directory / "manifest.json").read_bytes()) and approval.get("candidate") == "opening_drive", "authorization does not bind this frozen candidate")
    evidence = approval.get("a07_evidence_sha256", {})
    require(bool(evidence), "A07 audit evidence required")
    for name, expected in evidence.items():
        path = Path(name)
        require(path.is_file() and sha_bytes(path.read_bytes()) == expected, "A07 evidence changed")
    for name, entry in {**manifest["code"]["files"], **manifest["extra_source_files"]}.items():
        path = (directory / "sources" / name).resolve()
        require(path.is_relative_to(directory / "sources") and sha_bytes(path.read_bytes()) == entry["sha256"], "frozen source changed")
    require(manifest["criteria"] == CRITERIA and manifest["costs"]["slippage_ticks_per_fill"] == list(SCENARIOS), "registered criteria or costs changed")
    return manifest


def load_authorized_minutes(manifest):
    """Caller must pass authorization validation first; never used by preparation."""
    from app.services.trading_day import futures_session_is_open, trading_day_date
    pool = Path(manifest["pool_directory"])
    current = read_metadata(pool)
    require(current == manifest["pool_metadata"], "pool metadata changed since freeze")
    metadata = current["manifest"]
    calls = [call for call in metadata["http_calls"] if call["path"] == "/api/History/retrieveBars"]
    require(len(calls) == len(metadata["windows"]), "window request count mismatch")
    minutes = []
    for window, call in zip(metadata["windows"], calls):
        name = window["file"]
        payload = (pool / name).read_bytes()
        require(sha_bytes(payload) == metadata["files"][name]["sha256"], "quarantined raw file hash changed")
        minutes.extend(parse_window(json.loads(payload), window=window, initiated_at=call["initiated_at"],
            file_sha256=sha_bytes(payload), expiry_ns=metadata["databento_dated_reference"]["definition"]["expiration_ns"]))
    minutes.sort(key=lambda row: row.candle_timestamp)
    require(len(minutes) == 55240 and len({r.candle_timestamp for r in minutes}) == 55240, "pool uniqueness/count invalid")
    require(all(futures_session_is_open(row.candle_timestamp, symbol="MNQ") for row in minutes), "minute outside known calendar")
    counts = Counter(trading_day_date(r.candle_timestamp).isoformat() for r in minutes)
    require(dict(counts) == metadata["coverage"]["rows_by_trading_day_et"], "trading-date coverage changed")
    signals = complete_five_minute_bars(minutes)
    require(len(signals) == len(minutes)//5 and signals[199].nominal_close_time == READY, "expected first-decision warmup incomplete")
    return minutes


def execute(directory, approval_path):
    from tools.research_topbot import write_new_json, summarize_result
    expected_script = directory / "sources/backend/tools/research_projectx_unseen.py"
    require(Path(__file__).resolve() == expected_script.resolve(), "execute only the frozen captured adapter")
    approval = read_json(approval_path)
    manifest = validate_authorization(directory, approval)
    write_new_json(directory / "evaluation-started.json", {"started_at": datetime.now(UTC).isoformat(),
        "approval_sha256": sha_bytes(approval_path.read_bytes()), "approval": approval,
        "prepared_manifest_sha256": sha_bytes((directory / "manifest.json").read_bytes())})
    try:
        minutes = load_authorized_minutes(manifest)
        results = {}
        for slip in SCENARIOS:
            engine = make_engine(minutes, slippage=slip)
            require(engine.execution_start_times[0] == READY-MINUTE and engine.execution_close_times[-1] == END, "unexpected warmup deferral or evaluated bounds")
            require(engine.cash == 50000 and engine.position is None and engine.pending is None and not engine.daily_net_activity, "portfolio did not start fresh")
            result = label_result(engine.run(), source_fingerprint=MANIFEST_SHA, candidate=manifest["candidate"])
            require(engine.delivery_roll_count == 0 and engine.position is None, "unexpected roll or residual position")
            sessions = engine.session_ledger()
            require(len(sessions) == 40, "forty session ledger required")
            for trade in result["trades"]:
                require(trade["quantity"] == 1 and abs(trade["commission"]-1.22) < 1e-8 and abs(trade["net_pnl"]-trade["gross_pnl"]+1.22) < 1e-8, "trade fees/quantity invalid")
                require(trade["exit_reason"] != "forced_end_of_test", "unexpected end-of-test exit; clock audit required")
            exposure = {"definition": "side held at observed minute close", "bar_count": engine.research_bar_count,
                **{side: {"bars": engine.research_exposure[side], "percent": 100*engine.research_exposure[side]/max(1,engine.research_bar_count)} for side in ("long", "short")}}
            summary = summarize_result(result, sessions, exposure, repetitions=2000)
            summary.update(source=SOURCE, source_fingerprint=MANIFEST_SHA, candidate=manifest["candidate"],
                provenance=deepcopy(result["provenance"]), requested_period=manifest["period"], preliminary_only=True)
            for suffix, value in (("replay", result), ("trades", result["trades"]), ("sessions", sessions), ("summary", summary)):
                write_new_json(directory / f"opening_drive__whole_pool__slip-{slip}.{suffix}.json", value)
            results[str(slip)] = {"net_pnl": result["metrics"]["net_pnl"], "trade_count": result["metrics"]["trade_count"], "trades_sha256": fingerprint(result["trades"])}
        if not all(results[str(s)]["trade_count"] for s in (1,2)):
            status = "inconclusive_no_trades"
        elif all(results[str(s)]["net_pnl"] > 0 for s in (1,2)):
            status = "preliminary_support_only"
        else:
            status = "fails_predeclared_preliminary_screen"
        write_new_json(directory / "evaluation-completed.json", {"status": status, "results": results,
            "criteria": CRITERIA, "confirmed_profitability": False, "independent_confirmation_threshold_met": False})
    except Exception as error:
        # Avoid exception text containing raw row values or paths to credentials.
        write_new_json(directory / "evaluation-failed.json", {"status": "invalid_evaluation", "error_type": type(error).__name__, "error": str(error)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "_execute"))
    parser.add_argument("--prepared-dir", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--output-root", type=Path, default=BACKEND / "storage/research/unseen-preparations")
    args = parser.parse_args()
    offline()
    sys.path.insert(0, str(BACKEND))
    if args.action == "prepare":
        directory = prepare(args.output_root)
        print(json.dumps({"prepared_directory": str(directory), "manifest_sha256": sha_bytes((directory / "manifest.json").read_bytes()), "price_files_opened": False, "evaluation_authorized": False}))
        return 0
    require(args.prepared_dir is not None and args.approval is not None, "frozen preparation and separate approval artifact required")
    directory, approval = args.prepared_dir.resolve(), args.approval.resolve()
    validate_authorization(directory, read_json(approval))
    if args.action == "run":
        script = directory / "sources/backend/tools/research_projectx_unseen.py"
        return subprocess.run([sys.executable, str(script), "_execute", "--prepared-dir", str(directory), "--approval", str(approval)], check=False).returncode
    execute(directory, approval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
