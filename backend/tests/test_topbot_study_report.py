from datetime import datetime, timedelta, timezone

import pytest

from app.models import BotConfig, BotOrderAttempt
from app.services import databento_cache, topbot_study_jobs as jobs, topbot_study_store as store
from app.services import topbot_study_report as report
from app.services.topbot_study import EXPERIMENT_ID, MODEL_CODE, PROTOCOL
from test_bot_execution_safety import db_session  # noqa: F401  (fixture)

OWNER_A, OWNER_B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
CONTRACT = "CON.F.US.MNQ.Z26"
NOW = datetime.now(timezone.utc)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    monkeypatch.setattr(databento_cache, "default_cache_root", lambda: tmp_path)
    from app.services import topbot_study_worker as worker
    worker.ACTIVE.clear(); worker.RETENTION.clear()
    return tmp_path


def attempt(db, owner, account, *, status="submitted", entry_minutes=None, cutoff_minutes=60, exit_reason=None,
            experiment=EXPERIMENT_ID, mode="fixed", reason=None):
    runner = {"version": 1, "exit_mode": mode, "take_profit_points": 25 if mode == "fixed" else None,
              "initial_stop_points": 25, "activation_points": 25, "trail_points": 25, "stats_status": "awaiting_fill"}
    if entry_minutes is not None:
        runner.update(entry_at=(NOW-timedelta(minutes=entry_minutes)).isoformat(), entry_price=20000., stats_status="open")
        if exit_reason:
            runner.update(stats_status="complete", exit_reason=exit_reason, exit_at=(NOW-timedelta(minutes=entry_minutes-1)).isoformat(),
                          net_pnl_usd=48.28 if exit_reason == "take_profit" else -51.72, hold_seconds=60)
    snapshot = {"experiment_id": experiment, "research_cutoff": (NOW+timedelta(minutes=cutoff_minutes)).isoformat(),
                "rules": {"exit": {"exit_mode": mode}, "entry": {}}, "strategy_version_id": "v", "session": NOW.date().isoformat(),
                "study_model_code": MODEL_CODE, "quantity": 1, "initial_risk_points": 25, "initial_risk_usd": 50,
                "contract_id": CONTRACT}
    row = BotOrderAttempt(user_id=owner, account_id=account, contract_id=CONTRACT, side="BUY", size=1, status=status,
                          execution_mode="live", rejection_reason=reason,
                          raw_request={"timeExit": {"runner": runner}, "strategySnapshot": snapshot,
                                       "entryEvidence": {"pre_submit": {"evidence": {"capture_id": "cd"*16}}}})
    db.add(row)
    db.commit()
    return row


def result(entry_id, session, outcomes, *, valid=True, phase="exploratory"):
    """outcomes: key -> (exit_reason, net) for resolved variants; missing keys are unresolved."""
    variants = []
    for ratio in [str(r) for r in PROTOCOL["ratios"]]+["trailing"]:
        if ratio in outcomes:
            reason, net = outcomes[ratio]
            variants.append({"key": ratio, "ratio": None if ratio == "trailing" else float(ratio), "status": "resolved",
                             "exit_reason": reason, "net_pnl_usd": net, "realized_r": net/50})
        else:
            variants.append({"key": ratio, "status": "missing", "reason": "capture_index_gap"})
    return {"experiment_id": EXPERIMENT_ID, "entry_id": entry_id, "session": session, "entry_at": f"{session}T15:00:00+00:00",
            "paired_valid": valid and len(outcomes) == 10, "variants": variants, "phase": phase, "ordinal": entry_id,
            "strategy_version_id": "v", "strategy_class": "fixed"}


def test_blocked_and_error_attempts_are_not_awaiting_fill():
    blocked = report.display_status({"attempt_status": "blocked", "stats_status": "awaiting_fill"}, "Bot execution state changed")
    assert blocked["kind"] == "blocked" and "Awaiting" not in blocked["label"] and blocked["reason"] == "Bot execution state changed"
    assert report.display_status({"attempt_status": "error", "stats_status": "awaiting_fill"})["kind"] == "submission_error"
    assert report.display_status({"attempt_status": "submitted", "stats_status": "awaiting_fill"})["kind"] == "awaiting_fill"
    assert report.display_status({"attempt_status": "submission_unknown", "stats_status": "awaiting_fill"})["kind"] == "submission_unresolved"
    assert report.display_status({"attempt_status": "submitted", "stats_status": "not_filled"})["kind"] == "not_filled"
    assert report.display_status({"entry_at": "x", "stats_status": "complete", "exit_reason": "take_profit"})["label"] == "Closed · take profit"


def test_baseline_comparison_obeys_strategy_version_and_date_filters(db_session, cache):
    full = {k: ("initial_stop", -51.72) for k in [str(r) for r in PROTOCOL["ratios"]]+["trailing"]}
    for number, day, version, strategy in ((1, "2026-11-09", "v", "fixed"),
                                         (2, "2026-11-10", "v", "fixed"),
                                         (3, "2026-11-10", "other", "fixed"),
                                         (4, "2026-11-10", "v", "trailing"),
                                         (5, "2026-11-11", "v", "fixed")):
        saved = result(number, day, full)
        saved.update(strategy_version_id=version, strategy_class=strategy)
        store.save(OWNER_A, 101, saved)
    output = report.comparison_report(db_session, OWNER_A, 101, version="v", strategy="fixed",
                                      start="2026-11-10", end="2026-11-10")
    assert output["valid_entries"] == output["tp_baseline"]["cohort"]["valid_paired_entries"] == 1


def test_states_denominators_and_isolation_across_accounts_and_owners(db_session, cache):
    a1 = 101
    observing = attempt(db_session, OWNER_A, a1, entry_minutes=30, cutoff_minutes=90, exit_reason="take_profit")
    queued = attempt(db_session, OWNER_A, a1, entry_minutes=200, cutoff_minutes=-80, exit_reason="initial_stop")
    retrying = attempt(db_session, OWNER_A, a1, entry_minutes=300, cutoff_minutes=-180, exit_reason="take_profit")
    failed = attempt(db_session, OWNER_A, a1, entry_minutes=400, cutoff_minutes=-280, exit_reason="take_profit")
    valid = attempt(db_session, OWNER_A, a1, entry_minutes=500, cutoff_minutes=-380, exit_reason="take_profit")
    excluded = attempt(db_session, OWNER_A, a1, entry_minutes=600, cutoff_minutes=-480, exit_reason="initial_stop")
    blocked = attempt(db_session, OWNER_A, a1, status="blocked", reason="Bot execution state changed before provider routing.")
    unresolved = attempt(db_session, OWNER_A, a1, status="submission_unknown")
    prior = attempt(db_session, OWNER_A, a1, entry_minutes=700, cutoff_minutes=-580, exit_reason="take_profit", experiment="dom-rr-older")
    # Same owner, another account, and another owner with the same account number.
    other_account = attempt(db_session, OWNER_A, 202, entry_minutes=200, cutoff_minutes=-80, exit_reason="take_profit")
    other_owner = attempt(db_session, OWNER_B, a1, entry_minutes=200, cutoff_minutes=-80, exit_reason="take_profit")
    full = {k: ("take_profit", 48.28) for k in [str(r) for r in PROTOCOL["ratios"]]+["trailing"]}
    store.save(OWNER_A, a1, result(valid.id, "2026-11-10", full))
    store.save(OWNER_A, a1, result(excluded.id, "2026-11-10", {"1": ("initial_stop", -51.72)}, valid=False))
    for row, failures in ((retrying, 2), (failed, 6), (other_account, 6), (other_owner, 6)):
        for _ in range(failures):
            jobs.record(str(row.user_id), int(row.account_id), row.id, EXPERIMENT_ID, outcome="no_progress",
                        started_at=NOW.isoformat(), detail={"category": "no_progress"}, failure=True,
                        next_attempt_at=(NOW+timedelta(hours=1)).isoformat())
    db_session.add(BotConfig(user_id=OWNER_A, account_id=a1, name="t", provider="projectx", contract_id=CONTRACT, symbol="MNQ",
                             strategy_type="topbot_adaptive", execution_mode="live", enabled=False,
                             strategy_params={"revision": "dom_contrarian_v1", "exit_mode": "fixed"}))
    db_session.commit()
    status = report.research_status(db_session, OWNER_A, a1)
    ready = status["analysis"]["readiness"]
    assert ready["counts"] == {"observing": 1, "queued": 1, "processing": 0, "retrying": 1, "failed": 1,
                               "completed_valid": 1, "completed_excluded": 1}
    assert ready["registered_filled_entries"] == 6 and ready["pending"] == 4 and ready["deferred"] == 2
    assert ready["processed_coverage"] == {"numerator": 1, "denominator": 2, "rate": .5, "label": ready["processed_coverage"]["label"]}
    assert ready["overall_usable"]["denominator"] == 6
    assert ready["oldest_overdue"]["entry_id"] == failed.id
    assert ready["not_trades"] == {"blocked": 1}
    assert [u["entry_id"] for u in ready["unfilled_submissions"]] == [unresolved.id]
    assert ready["prior_experiment_entries"] == 1
    ids = {e["entry_id"] for e in status["analysis"]["entries"]}
    assert other_account.id not in ids and other_owner.id not in ids
    assert {e["entry_id"]: e["state"] for e in status["analysis"]["entries"]}[queued.id] == "queued"
    windows = status["post_exit_recording"]["open_windows"]
    assert [w["entry_id"] for w in windows] == [observing.id] and windows[0]["position_closed"]
    assert status["backup"]["verified"] is False and "destination" not in status["backup"]
    assert status["position_recording"]["scope"].startswith("Open-position path")
    # The other owner's view of the same account number sees only its own entry.
    other = report.research_status(db_session, OWNER_B, a1)["analysis"]
    assert {e["entry_id"] for e in other["entries"]} == {other_owner.id} and other["readiness"]["deferred"] == 1


def test_exit_settings_distinguish_future_entries_from_open_trade_protection(db_session, cache):
    db_session.add(BotConfig(user_id=OWNER_A, account_id=5, name="t", provider="projectx", contract_id=CONTRACT, symbol="MNQ",
                             strategy_type="topbot_adaptive", execution_mode="live", enabled=True,
                             strategy_params={"revision": "dom_contrarian_v1", "exit_mode": "fixed"}))
    db_session.commit()
    items = [{"entry_id": 1, "entry_at": NOW.isoformat(), "stats_status": "open", "exit_mode": "trailing",
              "take_profit_points": None, "initial_stop_points": 25, "confirmed_stop": 19975.}]
    settings = report.exit_settings(db_session, OWNER_A, 5, items)
    assert settings["future_entries"]["exit_mode"] == "fixed"
    assert settings["future_entries"]["take_profit_points"] == 25 and settings["future_entries"]["initial_stop_points"] == 25
    assert settings["open_trades"][0]["exit_mode"] == "trailing" and settings["open_trades"][0]["confirmed_stop"] == 19975.
    assert report.exit_settings(db_session, OWNER_A, 6, [])["future_entries"] is None


def test_tp_baseline_pairs_same_entries_includes_losers_and_keeps_partials_separate():
    keys = [str(r) for r in PROTOCOL["ratios"]]
    rows = []
    entry = 0
    for day in range(6):
        for n in range(6):
            entry += 1
            if n < 2:   # Winner at 25; a larger target later stops out.
                out = {"1": ("take_profit", 48.28), **{k: ("initial_stop", -51.72) for k in keys[1:]}, "trailing": ("trailing_stop", 10.)}
            elif n < 3:  # Runs far enough for every target.
                out = {k: ("take_profit", 25*float(k)*2-1.72) for k in keys} | {"trailing": ("trailing_stop", 60.)}
            else:        # Loser: identical for all fixed targets.
                out = {k: ("initial_stop", -51.72) for k in keys} | {"trailing": ("initial_stop", -51.72)}
            rows.append(result(entry, f"2026-11-{10+day:02d}", out))
    rows.append(result(999, "2026-11-16", {"1": ("take_profit", 48.28), "1.25": ("take_profit", 60.78)}, valid=False))
    rows.append(result(1000, "2026-11-16", {k: ("take_profit", 1.) for k in keys} | {"trailing": ("take_profit", 1.)}, phase="holdout"))
    out = report.tp_baseline(rows)
    assert out["cohort"]["valid_paired_entries"] == 36 and out["cohort"]["includes_losing_trades"]
    two = next(s for s in out["series"] if s["key"] == "2")
    diff = two["versus_baseline"]
    # Per 6 entries: two lose 100 each, one gains 50 ((100-1.72)-(50-1.72)), three are identical.
    assert diff["mean_difference_usd"] == pytest.approx((-100*2+50)/6)
    assert (diff["entries_better"], diff["entries_worse"], diff["entries_same"]) == (6, 12, 18)
    assert diff["baseline_target_became_stop"] == 12
    assert diff["verdict"] in {"lower_in_sample", "no_clear_difference"}
    assert out["series"][0]["key"] == "1" and "versus_baseline" not in out["series"][0]
    assert out["trailing_reference"]["label"].startswith("Trailing reference")
    assert [p["entry_id"] for p in out["partial_observations"]] == [999]
    assert out["partial_observations"][0]["unresolved"]["2"] == "capture_index_gap"
    small = report.tp_baseline(rows[:12])
    assert all(s["versus_baseline"]["verdict"] == "insufficient_data" for s in small["series"][1:])
