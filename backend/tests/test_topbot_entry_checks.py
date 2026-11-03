from datetime import datetime, timezone

from app.services.bot_risk import RiskBlock
from app.services.topbot_entry_checks import snapshot


def check(blocks=(), authoritative=False):
    return snapshot(blocks, checked_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
                    episode_at="episode", authoritative=authoritative)


def test_preliminary_checks_never_claim_broker_passes():
    result = check()
    assert result["phase"] == "preliminary"
    assert all(row["status"] == "unknown" for row in result["checks"])


def test_final_check_preserves_blockers_and_reports_independent_passes():
    blocks = [RiskBlock("proposed_stop_risk_exceeds_daily_loss_budget", "Insufficient budget"),
              RiskBlock("worker_lease_lost", "Worker lease lost")]
    result = check(blocks, authoritative=True)
    rows = {row["id"]: row for row in result["checks"]}
    assert rows["loss"]["status"] == "blocked"
    assert rows["loss"]["detail"] == "Insufficient budget"
    assert rows["cooldown"]["status"] == "passed"
    assert rows["worker_lease_lost"]["status"] == "blocked"
    assert result["episode_at"] == "episode"
    assert len(blocks) == 2


def test_unavailable_pnl_and_unresolved_orders_cannot_show_passes():
    result = check([RiskBlock("account_unrealized_pnl_unavailable", "No P&L"),
                    RiskBlock("unresolved_order_submission", "Pending order")], authoritative=True)
    rows = {row["id"]: row for row in result["checks"]}
    assert rows["loss"]["status"] == "blocked"
    assert rows["position"]["status"] == "blocked"


def test_preflight_failure_retains_unknowns_and_the_failure():
    result = check([RiskBlock("live_preflight_unavailable", "Broker unavailable")])
    assert not any(row["status"] == "passed" for row in result["checks"])
    assert result["checks"][-1]["detail"] == "Broker unavailable"
