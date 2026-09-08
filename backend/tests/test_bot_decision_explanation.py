"""Explanation must follow the actual execution result, not a market score."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_bot_execution_safety import (USER_A, RecordingClient, _add_account_and_config,
    _patch_actionable_signal, db_session, open_exchange_session)
from app.services import bot_service
from app.services.bot_decision_explanation import build_bot_decision_explanation
from app.bot_schemas import BotEvaluationOut
from app.market_observation_models import MarketObservation


@pytest.mark.parametrize("action,enabled,status", [("HOLD", True, "held"), ("BUY", True, "dry_run_attempt"), ("SELL", False, "risk_blocked")])
def test_explanation_matches_real_router_outcome_and_dry_run(db_session, monkeypatch, open_exchange_session, action, enabled, status):
    account, config = _add_account_and_config(db_session, enabled=enabled)
    _patch_actionable_signal(monkeypatch, action=action)
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=client, dry_run=True)
    explanation = result.analysis["bot_decision"]
    assert result.status == status == explanation["status"]
    assert result.decision.action == explanation["action"]
    assert result.decision.reason == explanation["strategy_reason"]
    assert explanation["execution_mode"] == "dry_run"
    assert client.place_order_calls == []
    checks = {check["id"]: check for check in explanation["checks"]}
    assert checks["risk"]["status"] == ("not_evaluated" if action == "HOLD" else "passed" if enabled else "failed")
    if not enabled:
        assert "Bot is disabled" in explanation["summary"]
    if action == "HOLD":
        assert "not evaluated for permission" in checks["risk"]["detail"]
    assert "predict" not in explanation["summary"].lower()


def test_duplicate_explanation_reports_final_mutated_action(db_session, monkeypatch, open_exchange_session):
    account, config = _add_account_and_config(db_session)
    _patch_actionable_signal(monkeypatch)
    client = RecordingClient()
    bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=client, dry_run=True)
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=client, dry_run=True)
    assert result.status == result.analysis["bot_decision"]["status"] == "duplicate_skipped"
    assert result.analysis["bot_decision"]["action"] == result.decision.action
    assert "duplicate" in result.analysis["bot_decision"]["summary"]
    assert client.place_order_calls == []


def test_actual_topbot_warmup_hold_is_explained_without_claiming_risk_permission(db_session, monkeypatch, open_exchange_session):
    from app.services.topbot_strategy import evaluate
    account, config = _add_account_and_config(db_session)
    config.strategy_type = "topbot_adaptive"
    _patch_actionable_signal(monkeypatch)
    fetch = bot_service.fetch_candles_and_evaluate_strategy
    def actual_strategy(*args, **kwargs):
        candles, _signal = fetch(*args, **kwargs)
        return candles, evaluate(candles)
    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", actual_strategy)
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=RecordingClient(), dry_run=True)
    assert result.status == "held"
    assert "200 closed candles" in result.analysis["bot_decision"]["summary"]
    assert result.analysis["bot_decision"]["strategy"]["revision"]
    assert next(check for check in result.analysis["bot_decision"]["checks"] if check["id"] == "risk")["status"] == "not_evaluated"


def test_api_roundtrip_preserves_closed_candle_observation_cutoff_contract_and_final_decision(db_session, monkeypatch, open_exchange_session):
    account, config = _add_account_and_config(db_session)
    canonical = bot_service.build_bot_market_analysis
    stamp = _patch_actionable_signal(monkeypatch, action="HOLD")
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", canonical)
    cutoff = stamp+timedelta(minutes=5)
    for price, contract, when in [(100, config.contract_id, cutoff-timedelta(seconds=1)),
        (200, config.contract_id, cutoff+timedelta(seconds=1)), (300, "CON.F.US.MNQ.Z26", cutoff)]:
        db_session.add(MarketObservation(user_id=USER_A, contract_id=contract, source="projectx_gateway_depth", event_type="quote",
            provider_timestamp=when, received_at=when, fingerprint=str(uuid4()), bid=price, ask=price+.25, details={}))
    db_session.flush()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=RecordingClient(), dry_run=True)
    response = BotEvaluationOut.model_validate(bot_service.serialize_evaluation(result)).model_dump(mode="json")
    analysis = response["analysis"]
    assert analysis["bot_decision"]["status"] == response["status"] == "held"
    assert analysis["bot_decision"]["action"] == response["decision"]["action"]
    assert analysis["collected_context"]["as_of"] == cutoff.isoformat()
    assert analysis["collected_context"]["order_book"]["bid"] == 100
    assert analysis["collected_context"]["contract_id"] == response["decision"]["contract_id"]
    assert "Level 1 quote" in analysis["context_coverage"]["limited"]
    assert analysis["explanation"]["context_evidence"]


def candle(opened):
    return SimpleNamespace(candle_timestamp=opened, unit="minute", unit_number=5, is_partial=False,
        open_price=100, high_price=102, low_price=99, close_price=101, volume=10)


def test_risk_freshness_waits_for_next_scheduled_close_and_keeps_configured_grace():
    opened = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)
    row = candle(opened)
    assert bot_service._candle_delivery_delay_seconds(row, symbol="MNQ", now=opened+timedelta(minutes=8)) == 0
    assert bot_service._candle_delivery_delay_seconds(row, symbol="MNQ", now=opened+timedelta(minutes=11, seconds=31)) == 91


def test_risk_freshness_pauses_over_weekend_and_scheduled_holiday_closure():
    friday = datetime(2026, 9, 4, 20, 55, tzinfo=timezone.utc)
    assert bot_service._candle_delivery_delay_seconds(candle(friday), symbol="MNQ", now=datetime(2026, 9, 5, 20, tzinfo=timezone.utc)) == 0
    holiday = datetime(2026, 9, 7, 16, 55, tzinfo=timezone.utc)
    assert bot_service._candle_delivery_delay_seconds(candle(holiday), symbol="MNQ", now=datetime(2026, 9, 7, 20, tzinfo=timezone.utc)) == 0


def test_future_or_mismatched_signal_timestamp_is_not_actionable():
    now = datetime.now(timezone.utc)
    old = candle(now-timedelta(minutes=20))
    latest = candle(now-timedelta(minutes=6))
    signal = SimpleNamespace(candle_timestamp=old.candle_timestamp)
    assert bot_service._actionable_candle_timestamp(signal=signal, candles=[old, latest], latest_candle=latest) is None
    future = candle(now)
    signal.candle_timestamp = future.candle_timestamp
    assert bot_service._actionable_candle_timestamp(signal=signal, candles=[latest, future], latest_candle=future) is None


def test_session_rejection_and_submission_error_explanations_do_not_claim_permission():
    config = SimpleNamespace(strategy_type="sma_cross", trading_start_time="09:30", trading_end_time="15:45",
        max_contracts=1, max_open_position=1, max_daily_loss=250, max_trades_per_day=10, max_data_staleness_seconds=90)
    signal = SimpleNamespace(action="BUY", reason="SMA crossed up", raw_payload={})
    decision = SimpleNamespace(action="BUY", candle_timestamp=None, contract_id="MNQ")
    risk = SimpleNamespace(code="outside_session", message="Current time is outside the bot trading session.")
    result = build_bot_decision_explanation(config=config, signal=signal, decision=decision, status="risk_blocked", risk_events=[risk], dry_run=True, analysis={})
    assert next(check for check in result["checks"] if check["id"] == "session")["status"] == "failed"
    result = build_bot_decision_explanation(config=config, signal=signal, decision=decision, status="error", risk_events=[], dry_run=False, analysis={},
        order_attempt=SimpleNamespace(rejection_reason="Submission unknown"))
    assert "not confirmed as submitted" in result["summary"]
    assert "Submission unknown" in result["summary"]


def test_optional_explanation_failure_preserves_routing_result_and_api_contract(db_session, monkeypatch, open_exchange_session):
    account, config = _add_account_and_config(db_session)
    canonical = bot_service.build_bot_market_analysis
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", canonical)
    def broken(*args, **kwargs):
        raise ValueError("optional explanation failed")
    monkeypatch.setattr("app.services.bot_decision_explanation.build_bot_decision_explanation", broken)
    monkeypatch.setattr("app.services.market_context_bundle.integrate_collected_context", broken)
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=client, dry_run=True)
    response = BotEvaluationOut.model_validate(bot_service.serialize_evaluation(result)).model_dump(mode="json")
    assert response["status"] == response["analysis"]["bot_decision"]["status"] == "dry_run_attempt"
    assert "Detailed explanation unavailable" in response["analysis"]["bot_decision"]["basis"]
    assert result.order_attempt.status == "dry_run"
    assert client.place_order_calls == []


def test_topbot_warmup_does_not_stitch_different_contracts():
    from app.services.topbot_strategy import evaluate
    started = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(200):
        row = candle(started+timedelta(minutes=5*index))
        row.contract_id = "CON.F.US.MNQ.U26" if index < 199 else "CON.F.US.MNQ.Z26"
        row.symbol = "MNQ"
        rows.append(row)
    assert len(bot_service._closed_candles(rows)) == 1
    result = evaluate(rows)
    assert result.action == "HOLD"
    assert "200 closed candles" in result.reason


def test_analysis_uses_actual_special_strategy_timeframe_and_same_stream_as_strategy():
    config = SimpleNamespace(timeframe_unit="minute", timeframe_unit_number=5, fast_period=2, slow_period=3,
        max_data_staleness_seconds=90, contract_id="MNQ", symbol="MNQ")
    started = datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(12):
        row = candle(started+timedelta(hours=index))
        row.unit, row.unit_number, row.contract_id, row.symbol = "hour", 1, "MNQ", "MNQ"
        rows.append(row)
    analysis = bot_service.build_bot_market_analysis(candles=rows, config=config, signal=SimpleNamespace(action="HOLD"))
    assert analysis["provenance"]["timeframe"]["label"] == "1H"
    assert analysis["provenance"]["closed_candle_count"] == len(bot_service._closed_candles(rows)) == 12
    assert analysis["provenance"]["latest_candle_end_timestamp"] == (rows[-1].candle_timestamp+timedelta(hours=1)).isoformat()
