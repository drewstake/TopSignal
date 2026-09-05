from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools import research_projectx_unseen as adapter

UTC = timezone.utc


def envelope(count=5):
    start = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    window = {"file": "history-synthetic.json", "start_utc": start.isoformat(),
        "end_exclusive_utc": (start+timedelta(minutes=count)).isoformat(), "rows": count}
    request = {"contractId": adapter.CONTRACT, "live": False, "startTime": window["start_utc"],
        "endTime": window["end_exclusive_utc"], "unit": 2, "unitNumber": 1,
        "limit": 20000, "includePartialBar": False}
    bars = [{"t": (start+timedelta(minutes=i)).isoformat(), "o": 100, "h": 101,
        "l": 99, "c": 100.25, "v": i+1} for i in range(count)]
    return {"request": request, "response": {"success": True, "errorCode": 0, "errorMessage": None, "bars": bars}}, window


def parse(value, window):
    return adapter.parse_window(value, window=window, initiated_at="2026-09-05T00:00:00Z",
        file_sha256="synthetic-file-sha", expiry_ns=1789738200000000000)


def synthetic_first_session():
    """Same timestamp layout as metadata; all prices are invented constants."""
    starts = [datetime(2026,7,10,20,20,tzinfo=UTC)+timedelta(minutes=i) for i in range(40)]
    starts += [adapter.START+timedelta(minutes=i) for i in range(18*60)]
    rows = []
    opening_start = datetime(2026,7,13,13,30,tzinfo=UTC)
    for at in starts:
        if opening_start <= at < adapter.READY:
            n = int((at-opening_start).total_seconds()/60)
            opening, closing = 10000+n, 10001+n
        elif at >= adapter.READY:
            opening = closing = 10030
        else:
            opening = closing = 10000
        rows.append(adapter.ProjectXCandle(at, opening, max(opening,closing)+.25,
            min(opening,closing)-.25, closing, 10, "synthetic-only", None))
    return rows


def test_canonical_parser_has_explicit_projectx_source_and_namespaced_crosswalk():
    value, window = envelope()
    value["response"]["bars"].reverse()
    minutes = parse(value, window)
    assert [row.candle_timestamp for row in minutes] == sorted(row.candle_timestamp for row in minutes)
    assert all(row.source == adapter.SOURCE and row.source != "databento" for row in minutes)
    assert minutes[0].source_instrument_id == 42004800
    assert minutes[0].source_instrument_id_namespace == "databento_dated_definition_crosswalk"
    assert minutes[0].contract_id == adapter.CONTRACT
    assert not hasattr(minutes, "_topsignal_verified_replay")
    bars = adapter.complete_five_minute_bars(minutes)
    assert len(bars) == 1 and bars[0].unit_number == 5
    assert bars[0].nominal_close_time == minutes[-1].nominal_close_time
    assert (bars[0].open_price, bars[0].high_price, bars[0].low_price, bars[0].close_price, bars[0].volume) == (100,101,99,100.25,15)


@pytest.mark.parametrize("key,value", [("o",float('nan')), ("h",float('inf')), ("c",100.1),
    ("o",False), ("h",99), ("l",0), ("v",-1), ("v",.5), ("v",True),
    ("t","2026-07-13T13:30:01Z"), ("t","2026-07-13T13:35:00Z"),
    ("t","2026-07-13T13:30:00")])
def test_invalid_canonical_rows_fail_without_repair(key, value):
    data, window = envelope()
    data["response"]["bars"][0][key] = value
    with pytest.raises(ValueError):
        parse(data, window)


@pytest.mark.parametrize("mutation", ["contract", "partial", "duplicate", "extra", "failed"])
def test_wrong_contract_partial_duplicate_and_schema_rejected(mutation):
    data, window = envelope()
    if mutation == "contract": data["request"]["contractId"] = "CON.F.US.MNQ.Z26"
    if mutation == "partial": data["request"]["includePartialBar"] = True
    if mutation == "duplicate": data["response"]["bars"][1]["t"] = data["response"]["bars"][0]["t"]
    if mutation == "extra": data["response"]["bars"][0]["d"] = 0
    if mutation == "failed": data["response"]["success"] = False
    with pytest.raises(ValueError): parse(data, window)


def test_resampling_does_not_bridge_missing_minutes_or_delivery_changes():
    value, window = envelope()
    rows = parse(value, window)
    assert adapter.complete_five_minute_bars(rows[:2]+rows[3:]) == []
    with pytest.raises(ValueError, match="duplicate"):
        adapter.complete_five_minute_bars(rows+[rows[0]])
    with pytest.raises(ValueError, match="mixed"):
        adapter.complete_five_minute_bars([replace(rows[0], source_raw_symbol="MNQZ6"), *rows[1:]])


def test_predeclared_same_source_warmup_has_exactly_200_bars_at_first_decision():
    bars = adapter.complete_five_minute_bars(synthetic_first_session())
    assert sum(bar.nominal_close_time <= adapter.READY for bar in bars) == 200
    assert bars[199].nominal_close_time == adapter.READY
    assert sum(bar.nominal_close_time < adapter.READY for bar in bars) == 199
    assert sum(bar.nominal_close_time <= adapter.START for bar in bars) == 8


@pytest.mark.parametrize("slip", [1,2,4])
def test_synthetic_engine_first_decision_clock_fees_and_fresh_state(slip):
    minutes = synthetic_first_session()
    end = datetime(2026,7,13,16,tzinfo=UTC)
    # Extend fixed-price invented minutes through the predetermined clock exit.
    last = minutes[-1]
    minutes.extend(replace(last,candle_timestamp=at) for at in (
        end+timedelta(minutes=i) for i in range(5*60)))
    end = datetime(2026,7,13,21,tzinfo=UTC)
    engine = adapter.make_engine(minutes, slippage=slip, end=end)
    fresh = adapter.make_engine(minutes, slippage=slip, end=end)
    assert engine.execution_start_times[0] == adapter.READY-timedelta(minutes=1)
    assert engine.cash == fresh.cash == 50000
    assert engine.position is engine.pending is fresh.position is fresh.pending is None
    assert not engine.daily_net_activity and not fresh.daily_net_activity
    result = engine.run()
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert adapter.instant(trade["signal_timestamp"]) == adapter.READY-timedelta(minutes=5)
    assert adapter.instant(trade["entry_timestamp"]) == adapter.READY
    assert adapter.instant(trade["exit_timestamp"]) == datetime(2026,7,13,19,55,tzinfo=UTC)
    assert trade["exit_reason"] == "scheduled_session_flatten"
    assert trade["commission"] == 1.22 and trade["quantity"] == 1
    assert trade["net_pnl"] == pytest.approx(trade["gross_pnl"]-1.22)
    assert trade["gross_pnl"] == pytest.approx(-slip)
    assert engine.delivery_roll_count == 0 and engine.position is None
    assert fresh.cash == 50000 and fresh.trades == [] and fresh.session_ledger() == []


def test_future_aggregate_prices_cannot_change_first_entry():
    minutes = synthetic_first_session()
    end = datetime(2026,7,13,14,10,tzinfo=UTC)
    cutoff = adapter.READY+timedelta(minutes=1)
    changed = [replace(row, high_price=10100, low_price=9900, close_price=10050) if row.candle_timestamp >= cutoff else row for row in minutes]
    original = adapter.make_engine(minutes, slippage=1, end=end).run()["trades"][0]
    perturbed = adapter.make_engine(changed, slippage=1, end=end).run()["trades"][0]
    assert {k: original[k] for k in ("side","entry_timestamp","entry_price","signal_timestamp")} == {k: perturbed[k] for k in ("side","entry_timestamp","entry_price","signal_timestamp")}


def test_source_metadata_override_never_changes_economic_result():
    engine = adapter.make_engine(synthetic_first_session(), slippage=1, end=datetime(2026,7,13,14,10,tzinfo=UTC))
    result = engine.run()
    economic = json.dumps({k:result[k] for k in ("metrics","trades","equity_curve")}, sort_keys=True)
    adapter.label_result(result, source_fingerprint="test-pool-hash", candidate={"revision":"frozen-opening-drive","name":"opening_drive"})
    assert result["assumptions"]["historical_source"] == adapter.SOURCE
    assert result["assumptions"]["strategy_revision"] == "frozen-opening-drive"
    assert result["assumptions"]["source_fingerprint"] == "test-pool-hash"
    assert result["provenance"]["prices_from_databento"] is False
    assert json.dumps({k:result[k] for k in ("metrics","trades","equity_curve")}, sort_keys=True) == economic


def test_metadata_reader_opens_only_three_explicit_metadata_files(monkeypatch):
    # Intercept filesystem access entirely: do not read any actual pool file.
    names = []
    def refuse(path):
        names.append(path.name)
        raise RuntimeError("synthetic read stop")
    monkeypatch.setattr(Path,"read_bytes",refuse)
    with pytest.raises(RuntimeError, match="synthetic read stop"):
        adapter.read_metadata(Path("synthetic-pool"))
    assert names == ["manifest.json"]


def test_valid_synthetic_metadata_reads_all_and_only_allowlisted_files(monkeypatch):
    contract = {"response": {"contract": {"id":adapter.CONTRACT,"name":"MNQU6","tickSize":.25,"tickValue":.5}}}
    qa = {"checked_rows":55240,"errors":{"synthetic":0},"bar_source_is_databento":False}
    days = {"2026-07-10":40}
    at = datetime(2026,7,13,tzinfo=UTC)
    while at.date() <= datetime(2026,9,4).date():
        if at.weekday()<5: days[at.date().isoformat()] = 1380
        at += timedelta(days=1)
    contract_bytes = json.dumps(contract).encode()
    manifest = {"contract_id":adapter.CONTRACT,"contract_metadata_verified":True,
        "files":{"contract-lookup.json":{"sha256":adapter.sha_bytes(contract_bytes)},"history-synthetic.json":{"sha256":"unread-price-sha"}},
        "databento_dated_reference":{"definition":{"raw_symbol":"MNQU6","contract_key":"MNQU6@2026","instrument_id":42004800}},
        "coverage":{"total_rows":55240,"duplicate_timestamps_across_windows":0,"rows_by_trading_day_et":days,"first_utc":"2026-07-10T20:20:00Z"},
        "end_exclusive_utc":adapter.END.isoformat(),
        "windows":[{"file":"history-synthetic.json","missing_regular_session_minutes":0,"out_of_regular_session_minutes":0,"duplicate_timestamps":0}]}
    blobs = {"manifest.json":json.dumps(manifest).encode(),"structural-qa.json":json.dumps(qa).encode(),"contract-lookup.json":contract_bytes}
    monkeypatch.setattr(adapter,"MANIFEST_SHA",adapter.sha_bytes(blobs["manifest.json"]))
    monkeypatch.setattr(adapter,"QA_SHA",adapter.sha_bytes(blobs["structural-qa.json"]))
    names = []
    def guarded_read(path):
        names.append(path.name)
        assert path.name in blobs, "A price file was opened during metadata preparation"
        return blobs[path.name]
    monkeypatch.setattr(Path,"read_bytes",guarded_read)
    result = adapter.read_metadata(Path("synthetic-pool"))
    assert names == list(blobs)
    assert result["history_price_files_opened_during_preparation"] is False
    assert result["history_file_hashes_from_prior_manifest_only"]["history-synthetic.json"]["sha256"] == "unread-price-sha"


def test_authorization_binds_preparation_and_source_hashes(tmp_path):
    code = tmp_path/'sources/backend/tools/synthetic.py'
    code.parent.mkdir(parents=True)
    code.write_bytes(b'unchanged source')
    evidence = tmp_path/'synthetic-a07-audit.json'
    evidence.write_text('{"passed":true}',encoding='utf-8')
    manifest = {"code":{"files":{"backend/tools/synthetic.py":{"sha256":adapter.sha_bytes(code.read_bytes())}}},
        "extra_source_files":{},"criteria":adapter.CRITERIA,"costs":{"slippage_ticks_per_fill":list(adapter.SCENARIOS)}}
    manifest_path = tmp_path/'manifest.json'
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    approval = {"permission":"single_reserved_pool_evaluation","a07_audited_passed":True,
        "prepared_manifest_sha256":adapter.sha_bytes(manifest_path.read_bytes()),"candidate":"opening_drive",
        "a07_evidence_sha256":{str(evidence):adapter.sha_bytes(evidence.read_bytes())}}
    assert adapter.validate_authorization(tmp_path,approval) == manifest
    code.write_bytes(b'changed source')
    with pytest.raises(ValueError,match="frozen source changed"):
        adapter.validate_authorization(tmp_path,approval)
    approval['prepared_manifest_sha256'] = 'different-preparation'
    with pytest.raises(ValueError,match="does not bind"):
        adapter.validate_authorization(tmp_path,approval)


def test_missing_authorization_rejected_before_source_or_price_access(tmp_path, monkeypatch):
    (tmp_path/"manifest.json").write_text('{}', encoding='utf-8')
    names = []
    def reject(_path):
        names.append(_path)
        raise AssertionError("No evidence/source/price reads before permission")
    monkeypatch.setattr(Path,"read_bytes",reject)
    with pytest.raises(ValueError, match="separate root authorization"):
        adapter.validate_authorization(tmp_path,{})
    assert names == []


def test_preliminary_criteria_do_not_claim_confirmation_or_allow_reselection():
    assert adapter.CRITERIA["confirmation_minimum_trades"] == 200
    assert adapter.CRITERIA["confirmation_minimum_calendar_months"] == 6
    assert adapter.CRITERIA["complete_sessions_expected"] == 40
    assert adapter.CRITERIA["no_retuning_or_date_subselection_after_opening_pool"]
    assert adapter.SCENARIOS == (1,2,4)
