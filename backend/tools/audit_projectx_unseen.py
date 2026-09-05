"""Audit completed A08 artifacts and export full ledgers; never replays a strategy."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SHA = "fe880f413c5c2cd3db1addc0507cafc2ec5cf8da09cdac0c430d8cef67d3af6f"
POOL_SHA = "25e354280208ae795f402dd88155b3f87c1652b4b976c5b31002fc462dff4576"
UTC = timezone.utc
ET = ZoneInfo("America/New_York")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def at(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert result.tzinfo is not None
    return result.astimezone(UTC)


def close(left, right, tolerance=1e-8):
    return math.isfinite(float(left)) and math.isfinite(float(right)) and abs(float(left)-float(right)) <= tolerance


def write(path, value):
    with path.open("x",encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def csv_file(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("x",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def expected_dates_and_minutes():
    dates=[]
    day=datetime(2026,7,13,tzinfo=ET)
    while day.date() <= datetime(2026,9,4).date():
        if day.weekday()<5:
            dates.append(day.date().isoformat())
        day+=timedelta(days=1)
    expected={datetime(2026,7,10,20,20,tzinfo=UTC)+timedelta(minutes=i) for i in range(40)}
    for date in dates:
        session_open=(datetime.fromisoformat(date).replace(tzinfo=ET)-timedelta(days=1)).replace(hour=18).astimezone(UTC)
        expected.update(session_open+timedelta(minutes=i) for i in range(1380))
    return dates, expected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir",type=Path,required=True)
    parser.add_argument("--output-root",type=Path,default=ROOT/"backend/storage/research/unseen-audits")
    args=parser.parse_args()
    directory=args.prepared_dir.resolve()
    errors=[]
    def check(condition,label):
        if not condition: errors.append(label)
    manifest=read(directory/"manifest.json")
    started=read(directory/"evaluation-started.json")
    finished=read(directory/"evaluation-completed.json")
    receipt=read(directory/"root-evaluation-receipt.json")
    check(not (directory/"evaluation-failed.json").exists(),"evaluation_has_failure")
    check(sha(directory/"manifest.json")==MANIFEST_SHA,"prepared_manifest_hash")
    check(started["prepared_manifest_sha256"]==receipt["prepared_manifest_sha256"]==MANIFEST_SHA,"receipt_manifest_identity")
    check(started["approval"]==receipt and started["approval_sha256"]==sha(directory/"root-evaluation-receipt.json"),"receipt_snapshot_hash")
    check(at(manifest["created_at"])<at(receipt["decided_at"])<at(started["started_at"]),"freeze_receipt_start_chronology")
    check(receipt["permission"]=="single_reserved_pool_evaluation" and receipt["a07_audited_passed"] and receipt["candidate"]=="opening_drive","authorization_scope")
    check(finished["criteria"]==manifest["criteria"] and finished["confirmed_profitability"] is False and finished["independent_confirmation_threshold_met"] is False,"criteria_and_confirmation")
    source_files={**manifest["code"]["files"],**manifest["extra_source_files"]}
    inputs={p:sha(p) for p in directory.glob("*.json")}
    inputs[directory/"working-tree.patch"]=sha(directory/"working-tree.patch")
    for name,entry in source_files.items():
        path=directory/"sources"/name
        inputs[path]=sha(path)
        check(inputs[path]==entry["sha256"],f"frozen_source:{name}")
    check(digest(manifest["code"]["files"])==manifest["code"]["combined_sha256"],"source_bundle_hash")
    a06=read(directory/"a06-manifest.json")
    check(sha(directory/"a06-manifest.json")==manifest["a06_lineage"]["original_manifest_sha256"],"a06_manifest_hash")
    check(manifest["candidate"]["definition"]==a06["hypotheses"]["opening_drive"],"original_center_identity")
    for name,entry in manifest["a06_lineage"]["verified_identical_current_source_files"].items():
        check(source_files[name]["sha256"]==entry["sha256"]==a06["code"]["files"][name]["sha256"],f"a06_lineage:{name}")
    for name,expected in receipt["a07_evidence_sha256"].items():
        path=Path(name)
        inputs[path]=sha(path)
        check(inputs[path]==expected,"a07_evidence_hash")
        evidence=read(path)
        check(evidence["status"]=="all_46_cases_audited" and evidence["case_count"]==46 and all(evidence["neighbor_gates"].values()) and all(evidence["stress_gates"].values()),"a07_passed_gates")
    pool=Path(manifest["pool_directory"])
    collection=manifest["pool_metadata"]["manifest"]
    for name,expected in manifest["pool_metadata"]["metadata_sha256"].items():
        path=pool/name
        inputs[path]=sha(path)
        check(inputs[path]==expected,f"pool_metadata:{name}")
    check(inputs[pool/"manifest.json"]==POOL_SHA,"collection_manifest_hash")
    dates,expected_minutes=expected_dates_and_minutes()
    minutes={}
    raw_row_count=0
    for window in collection["windows"]:
        name=window["file"]
        path=pool/name
        check(Path(name).name==name,"raw_path_scope")
        inputs[path]=sha(path)
        check(inputs[path]==collection["files"][name]["sha256"],f"raw_hash:{name}")
        envelope=read(path)
        request,response=envelope["request"],envelope["response"]
        check(request["contractId"]=="CON.F.US.MNQ.U26" and request["unit"]==2 and request["unitNumber"]==1 and request["includePartialBar"] is False,"raw_request_contract_interval")
        check(request["startTime"]==window["start_utc"] and request["endTime"]==window["end_exclusive_utc"],"raw_request_bounds")
        check(response["success"] is True and response["errorCode"]==0 and len(response["bars"])==window["rows"],"raw_response_count_status")
        for row in response["bars"]:
            raw_row_count+=1
            timestamp=at(row["t"])
            check(set(row)=={"t","o","h","l","c","v"},"raw_row_schema")
            check(timestamp not in minutes,"raw_duplicate_minute")
            check(at(window["start_utc"])<=timestamp and timestamp+timedelta(minutes=1)<=at(window["end_exclusive_utc"]),"raw_closed_request_bounds")
            minutes[timestamp]=row
    check(raw_row_count==55240 and set(minutes)==expected_minutes,"exact_all_55240_expected_minutes")
    check(len(dates)==40,"forty_fixed_sessions")
    signal_closes=[]
    for timestamp in sorted(minutes):
        if timestamp.minute%5==0:
            check(all(timestamp+timedelta(minutes=i) in minutes for i in range(5)),"incomplete_five_minute_bucket")
            signal_closes.append(timestamp+timedelta(minutes=5))
    check(len(signal_closes)==11048 and signal_closes[199]==datetime(2026,7,13,14,tzinfo=UTC),"exact_200_bar_warmup")
    proof=directory/"audit-trade-execution-independent/verification.json"
    phases_path=directory/"audit-trade-execution-independent/exit-phases.json"
    fill_proof,phase_proof=read(proof),read(phases_path)
    inputs[proof],inputs[phases_path]=sha(proof),sha(phases_path)
    check(fill_proof["errors"]==[] and fill_proof["record_count"]==36 and phase_proof["parent_verification_sha256"]==inputs[proof],"independent_fill_proof")
    phase_rows={(r["slippage_ticks"],r["trade_id"]):r for r in phase_proof["records"]}
    import numpy as np
    rows=[]
    cases={}
    exports={}
    for slip in (1,2,4):
        stem=f"opening_drive__whole_pool__slip-{slip}"
        replay,trades,sessions,summary=(read(directory/f"{stem}.{kind}.json") for kind in ("replay","trades","sessions","summary"))
        metrics=replay["metrics"]
        check(trades==replay["trades"] and digest(trades)==summary["trades_sha256"]==finished["results"][str(slip)]["trades_sha256"],f"ledger_hash:{slip}")
        check(digest(sessions)==summary["sessions_sha256"],f"sessions_hash:{slip}")
        for key in ("metrics","range","config_snapshot","assumptions","data_quality","warnings","notes","provenance"):
            check(summary[key]==replay[key],f"summary_full_replay:{slip}:{key}")
        check(summary["candidate"]==manifest["candidate"] and summary["requested_period"]==manifest["period"],f"candidate_period:{slip}")
        assumptions=replay["assumptions"]
        check(assumptions["historical_source"]=="projectx_quarantined_dated_contract" and assumptions["source_fingerprint"]==POOL_SHA and assumptions["strategy_revision"]==manifest["candidate"]["revision"],f"provenance:{slip}")
        check(assumptions["commission_per_contract"]==.61 and assumptions["slippage_ticks"]==slip and assumptions["tick_size"]==.25 and assumptions["tick_value"]==.5 and assumptions["entry_delay_minutes"]==0 and assumptions["execution_stream"]=="observed_1m",f"cost_and_execution:{slip}")
        check(replay["range"]["bar_count"]==54241 and at(replay["range"]["start"])==datetime(2026,7,13,13,59,tzinfo=UTC) and at(replay["range"]["end"])==datetime(2026,9,4,21,tzinfo=UTC),f"executed_bounds:{slip}")
        quality=replay["data_quality"]
        check(quality["warmup_available"]==quality["warmup_required"]==200 and at(quality["first_evaluation"])==datetime(2026,7,13,14,tzinfo=UTC) and quality["gaps"]["missing_bar_count"]==quality["signal_gaps"]["missing_bar_count"]==0,f"warmup_coverage:{slip}")
        first,last=replay["equity_curve"][0],replay["equity_curve"][-1]
        check(first=={"equity":50000.0,"realized_pnl":0.0,"unrealized_pnl":0.0,"timestamp":"2026-07-12T22:00:00+00:00"},f"fresh_start_equity:{slip}")
        daily=defaultdict(float)
        previous_exit=None
        for trade in trades:
            qty=trade["quantity"]
            side=1 if trade["side"]=="long" else -1
            check(qty==1 and close(trade["commission"],1.22),f"trade_quantity_fee:{slip}:{trade['id']}")
            check(close(trade["gross_pnl"],side*(trade["exit_price"]-trade["entry_price"])*2*qty) and close(trade["net_pnl"],trade["gross_pnl"]-trade["commission"]),f"trade_price_accounting:{slip}:{trade['id']}")
            entry,exit=at(trade["entry_timestamp"]),at(trade["exit_timestamp"])
            check(entry<=exit and (previous_exit is None or previous_exit<=entry),f"trade_chronology:{slip}:{trade['id']}")
            check(entry.astimezone(ET).date()==exit.astimezone(ET).date(),f"intraday_flat:{slip}:{trade['id']}")
            check(trade["source_raw_symbol"]=="MNQU6" and trade["source_instrument_id"]==42004800 and trade["exit_reason"]!="forced_end_of_test",f"delivery_clock:{slip}:{trade['id']}")
            previous_exit=exit
            daily[exit.astimezone(ET).date().isoformat()]+=trade["net_pnl"]
        check([row["session"] for row in sessions]==dates,f"all_forty_session_dates:{slip}")
        equity=50000.0
        for session in sessions:
            equity+=daily[session["session"]]
            check(close(session["net_pnl"],daily[session["session"]]) and close(session["ending_equity"],equity),f"session_reconciliation:{slip}:{session['session']}")
        totals={key:math.fsum(float(t[key]) for t in trades) for key in ("net_pnl","gross_pnl","commission")}
        for key,total in totals.items():
            metric="total_commission" if key=="commission" else key
            check(close(total,metrics[metric]),f"metric_total:{slip}:{metric}")
            for period in ("daily_results","monthly_results"):
                check(close(math.fsum(r[key] for r in replay[period]),total),f"period_total:{slip}:{period}:{key}")
        wins=[t["net_pnl"] for t in trades if t["net_pnl"]>0]
        losses=[t["net_pnl"] for t in trades if t["net_pnl"]<0]
        check(len(trades)==metrics["trade_count"]==12 and len(wins)==metrics["winning_trades"] and len(losses)==metrics["losing_trades"],f"trade_counts:{slip}")
        check(close(metrics["profit_factor"],sum(wins)/-sum(losses)) and close(metrics["expectancy"],totals["net_pnl"]/len(trades)),f"pf_expectancy:{slip}")
        check(close(metrics["average_win"],sum(wins)/len(wins)) and close(metrics["average_loss"],sum(losses)/len(losses)),f"mean_win_loss:{slip}")
        for side in ("long","short"):
            subset=[t for t in trades if t["side"]==side]
            check(len(subset)==metrics[side]["trade_count"] and close(sum(t["net_pnl"] for t in subset),metrics[side]["net_pnl"]),f"side_metrics:{slip}:{side}")
        check(close(last["equity"],50000+totals["net_pnl"]) and close(last["realized_pnl"],totals["net_pnl"]) and last["unrealized_pnl"]==0,f"final_equity:{slip}")
        check(close(max(r["drawdown_dollars"] for r in replay["drawdown_series"]),metrics["max_drawdown_dollars"]),f"saved_max_drawdown:{slip}")
        # Mark the fixed recorded positions, never rerun entry/exit decisions.
        # Independent source audit supplies actual exit bar/phase so a clock
        # fill at the next open is not booked at the prior minute's close.
        ledger_marks={at(first["timestamp"]):50000.0}
        peak=50000.0
        maximum_dd=maximum_pct=0.0
        closing_exposure=Counter()
        any_exposure=0
        trade_times=[(t,at(t["entry_timestamp"]),at(phase_rows[(slip,t["id"])]["exit_bar_start"])) for t in trades]
        for timestamp in sorted(minutes):
            if timestamp<datetime(2026,7,13,13,59,tzinfo=UTC): continue
            mark=50000.0
            exposed=False
            for trade,entry,exit_bar in trade_times:
                if entry<=timestamp<=exit_bar: exposed=True
                if timestamp<entry: continue
                if timestamp>=exit_bar:
                    mark+=trade["net_pnl"]
                else:
                    sign=1 if trade["side"]=="long" else -1
                    mark+=sign*(minutes[timestamp]["c"]-trade["entry_price"])*2-.61
                    closing_exposure[trade["side"]]+=1
            any_exposure+=exposed
            event=timestamp+timedelta(minutes=1)
            ledger_marks[event]=mark
            peak=max(peak,mark)
            maximum_dd=max(maximum_dd,peak-mark)
            maximum_pct=max(maximum_pct,(peak-mark)/peak*100)
        check(close(maximum_dd,metrics["max_drawdown_dollars"]) and close(maximum_pct,metrics["max_drawdown_percent"]),f"independent_minute_mark_drawdown:{slip}")
        check(all(close(point["equity"],ledger_marks[at(point["timestamp"])]) for point in replay["equity_curve"]),f"all_sampled_equity_points_from_fixed_ledgers:{slip}")
        check(close(any_exposure/54241*100,metrics["exposure_percent"]),f"independent_exposure:{slip}")
        check(all(closing_exposure[side]==summary["exposure_by_side"][side]["bars"] for side in ("long","short")),f"independent_closing_exposure:{slip}")
        values=np.asarray([row["net_pnl"] for row in sessions],dtype=float)
        uncertainty=summary["uncertainty"]
        check(uncertainty["session_count"]==40 and uncertainty["seed"]==20260905 and close(uncertainty["mean_session_pnl"],values.mean()),f"bootstrap_input:{slip}")
        for estimate in uncertainty["estimates"]:
            block=estimate["block_sessions"]
            check(block in (5,20) and estimate["repetitions"]==2000,f"bootstrap_parameters:{slip}:{block}")
            rng=np.random.default_rng(20260905+block)
            indices=(rng.integers(0,40,size=(2000,math.ceil(40/block)))[:,:,None]+np.arange(block))%40
            means=values[indices.reshape(2000,-1)[:,:40]].mean(axis=1)
            limits=np.quantile(means,[.025,.975])
            check(np.allclose(limits,estimate["mean_session_pnl_95_percent_interval"],atol=1e-9,rtol=0) and np.allclose(limits*40,estimate["same_length_net_pnl_95_percent_interval"],atol=1e-8,rtol=0) and close(float((means>0).mean()),estimate["resampled_fraction_positive"]),f"bootstrap_reproduction:{slip}:{block}")
        descending=sorted((t["net_pnl"] for t in trades),reverse=True)
        concentration=summary["concentration"]
        expected_concentration={"best_trade":descending[0],"worst_trade":descending[-1],
            "net_excluding_best_trade":sum(descending)-descending[0],"net_excluding_best_5_trades":sum(descending)-sum(descending[:5]),
            "net_excluding_best_1_percent_trades":sum(descending)-descending[0],"top_1_percent_trade_count":1,
            "top_1_percent_positive_profit_share":max(wins)/sum(wins),
            "net_excluding_best_5_sessions":float(values.sum())-sum(sorted(values,reverse=True)[:5]),
            "best_calendar_year_net_pnl":float(values.sum()),"net_excluding_best_calendar_year":0.0}
        check(all(close(value,concentration[key]) for key,value in expected_concentration.items()),f"concentration:{slip}")
        check(finished["results"][str(slip)]["net_pnl"]==metrics["net_pnl"],f"terminal_metrics:{slip}")
        cases[slip]={"replay":replay,"summary":summary,"trades":trades,"sessions":sessions}
        exports[slip]={"trades":trades,"sessions":sessions,"sampled-equity":replay["equity_curve"],"sampled-drawdown":replay["drawdown_series"]}
        rows.append({"slippage_ticks":slip,**{key:metrics[key] for key in ("trade_count","gross_pnl","total_commission","net_pnl","expectancy","profit_factor","max_drawdown_dollars","max_drawdown_percent","win_rate","exposure_percent")},"long_net_pnl":metrics["long"]["net_pnl"],"short_net_pnl":metrics["short"]["net_pnl"],"sessions":40,"zero_pnl_sessions":int((values==0).sum()),"trades_sha256":digest(trades)})
    for slip in (2,4):
        for key in ("candidate","requested_period","config_snapshot","range"):
            check(cases[1]["summary"][key]==cases[slip]["summary"][key],f"case_control:{slip}:{key}")
        check({k:v for k,v in cases[1]["replay"]["assumptions"].items() if k!="slippage_ticks"}=={k:v for k,v in cases[slip]["replay"]["assumptions"].items() if k!="slippage_ticks"},f"case_non_slippage_assumptions:{slip}")
    check(finished["status"]=="fails_predeclared_preliminary_screen" and all(cases[slip]["replay"]["metrics"]["net_pnl"]<=0 for slip in (1,2)),"predeclared_failure_status")
    after={p:sha(p) for p in inputs}
    check(inputs==after,"input_bytes_changed_during_audit")
    output=args.output_root.resolve()/(datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")+"-a08-audit-"+uuid4().hex[:12])
    output.mkdir(parents=True,exist_ok=False)
    for slip,groups in exports.items():
        for kind,records in groups.items(): csv_file(output/f"opening_drive__whole_pool__slip-{slip}.{kind}.csv",records)
    csv_file(output/"cases.csv",rows)
    report={"status":"passed" if not errors else "failed","prepared_directory":str(directory),"errors":errors,
        "manifest_sha256":MANIFEST_SHA,"pool_manifest_sha256":POOL_SHA,"source_files_verified":len(source_files),
        "a06_lineage_files_verified":len(manifest["a06_lineage"]["verified_identical_current_source_files"]),
        "source_bundle_sha256":manifest["code"]["combined_sha256"],"cases":rows,"raw_minutes_checked":raw_row_count,
        "complete_sessions":40,"minute_execution_count":54241,"known_initial_warmup_deferral_minutes":959,
        "trade_rows_checked":sum(row["trade_count"] for row in rows),"distinct_underlying_market_sample":1,
        "bootstrap_reproduced":not any(e.startswith("bootstrap") for e in errors),"all_inputs_unchanged":inputs==after,
        "input_sha256_before":{str(p):value for p,value in inputs.items()},"input_sha256_after":{str(p):value for p,value in after.items()},
        "no_strategy_replay_or_new_provider_calls":True,"entire_pool_is_now_exposed":True,"strategy_reselection":False,
        "per_fill_independent_proof_path":str(proof),"per_fill_independent_proof_available":proof.is_file(),
        "drawdown_audit_scope":"Independent raw-minute-close marks of the fixed recorded trades reproduce every saved equity sample and maximum drawdown; no intraminute/tick-extremum claim",
        "export_script_sha256":sha(Path(__file__))}
    if proof.is_file():
        report["per_fill_independent_proof_sha256"]=sha(proof)
        write(output/"independent-fill-verification.json",read(proof))
    write(output/"audit.json",report)
    with (output/Path(__file__).name).open("xb") as stream: stream.write(Path(__file__).read_bytes())
    lines=["# A08 frozen opening-drive evaluation audit","",f"Audit status: {report['status']}. All 40 sessions are now exposed.","",
        "| Slippage ticks/fill | Trades | Gross | Fees | Net | PF | Expectancy | Max drawdown |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['slippage_ticks']} | {row['trade_count']} | {row['gross_pnl']:.2f} | {row['total_commission']:.2f} | {row['net_pnl']:.2f} | {row['profit_factor']:.4f} | {row['expectancy']:.2f} | {row['max_drawdown_dollars']:.2f} |")
    lines += ["","The original candidate failed its predeclared preliminary screen. The cases are three costs on one market sample, not 36 independent trades. Twelve trades and forty sessions cannot meet the unchanged 200-trade/six-month confirmation requirement. No retuning or new replay was performed.","",
        "Full trade and session CSVs preserve every field. Equity and drawdown CSVs are explicitly sampled charts; original complete JSON replays remain in the preparation directory.",""]
    with (output/"report.md").open("x",encoding="utf-8") as stream: stream.write("\n".join(lines))
    print(json.dumps({"output_directory":str(output),"status":report["status"],"errors":errors,"cases":3,"trade_rows":36,"raw_rows":raw_row_count,"input_files":len(inputs),"per_fill_proof_available":proof.is_file()},indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
