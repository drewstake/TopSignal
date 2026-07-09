from decimal import Decimal

from app.models import BotDecision
from app.services.bot_service import serialize_bot_trade_levels


def test_serializes_explicit_actionable_trade_levels_without_deriving_values():
    decision = BotDecision(
        action="BUY",
        price=100.0,
        raw_payload={
            "entry_price": 100.25,
            "stop_loss": 98.5,
            "take_profit": 104.75,
            "risk": 1.75,
        },
    )

    assert serialize_bot_trade_levels(decision) == {
        "entry": 100.25,
        "stop": 98.5,
        "target": 104.75,
    }


def test_uses_decision_entry_and_explicit_target_fallbacks_but_never_strategy_math():
    decision = BotDecision(
        action="SELL",
        price=100.0,
        raw_payload={"final_take_profit": 95.0, "risk": 2.0, "reward_r_multiple": 2.5},
    )

    assert serialize_bot_trade_levels(decision) == {
        "entry": 100.0,
        "stop": None,
        "target": 95.0,
    }


def test_rejects_non_actionable_and_non_finite_trade_levels():
    hold = BotDecision(
        action="HOLD",
        price=100.0,
        raw_payload={"entry_price": 100.0, "stop_loss": 98.0, "take_profit": 104.0},
    )
    invalid = BotDecision(
        action="BUY",
        price=float("inf"),
        raw_payload={"stop_loss": float("nan"), "take_profit": float("-inf")},
    )

    assert serialize_bot_trade_levels(hold) is None
    assert serialize_bot_trade_levels(invalid) is None


def test_rejects_boolean_and_malformed_trade_levels_without_coercion():
    invalid = BotDecision(
        action="BUY",
        price=True,
        raw_payload={
            "entry_price": "100.25",
            "stop_loss": False,
            "take_profit": {"price": 104.75},
            "final_take_profit": [104.75],
            "partial_take_profit": 10**10_000,
        },
    )

    assert serialize_bot_trade_levels(invalid) is None


def test_skips_malformed_candidates_but_keeps_valid_numeric_fallbacks():
    decision = BotDecision(
        action="SELL",
        price=Decimal("100.25"),
        raw_payload={
            "entry_price": True,
            "stop_loss": "102.50",
            "take_profit": False,
            "final_take_profit": Decimal("95.75"),
        },
    )

    assert serialize_bot_trade_levels(decision) == {
        "entry": 100.25,
        "stop": None,
        "target": 95.75,
    }
