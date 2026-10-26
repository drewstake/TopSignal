"""Investigation-only reproductions; run exclusively with run_offline_tests.py.

These assertions characterize defects, not desired acceptance behavior.
All provider objects are fakes and every database is in memory.
"""
from pathlib import Path
import sys
from datetime import timedelta
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend" / "tests"))

from test_audit_regressions import api
from test_bot_execution_safety import db_session, open_exchange_session, USER_A
from test_topbot_time_exit import live_entry, lease, due
from app.models import Account
from app.services import bot_service
from app.services.topbot_time_exit import process_time_exit


def test_foreign_origin_can_mutate_anonymous_local_account(api, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DEFAULT_USER_ID", USER_A)
    with api.session_factory() as db:
        db.add(Account(user_id=USER_A, provider="projectx", external_id="91001",
                       name="Disposable local account", is_main=False,
                       account_state="ACTIVE", is_visible=True))
        db.commit()
    response = api.post("/api/accounts/91001/main", headers={
        "origin": "http://untrusted.invalid",
        "content-type": "application/x-www-form-urlencoded",
        "sec-fetch-site": "cross-site",
    })
    assert response.status_code == 200, response.text
    with api.session_factory() as db:
        assert db.query(Account).filter_by(external_id="91001").one().is_main
    print("CONFIRMED: foreign-origin form POST changed disposable account is_main, no token")


def test_confirmed_working_entry_never_reaches_timed_exit(db_session, live_entry):
    result, client = live_entry
    entry = result.order_attempt
    entry.status = "submission_unknown"
    entry.provider_order_id = None
    client.orders = [{"account_id": entry.account_id, "contract_id": entry.contract_id,
                      "order_id": "provider-order-1", "custom_tag": entry.raw_request["customTag"],
                      "status": 1, "raw_payload": {"fillVolume": 0}}]
    client.open_orders = list(client.orders)
    db_session.commit()
    token = lease(db_session)
    for _ in range(4):
        outcome = bot_service.reconcile_unresolved_order_attempts(
            db_session, user_id=entry.user_id, account_id=entry.account_id, client=client,
            now=due(entry) + timedelta(minutes=10))
        assert outcome.unresolved_count == 1
        db_session.commit()  # Worker commits reconciliation before timed-exit processing.
        assert not process_time_exit(db_session, entry_id=entry.id, client=client,
                                     worker_lease_token=token, now=due(entry) + timedelta(minutes=10))
        db_session.commit()
    assert entry.provider_order_id == "provider-order-1"
    assert client.cancel_order_calls == []
    assert client.close_position_calls == []
    assert entry.raw_request["timeExit"]["status"] == "pending"
    print("CONFIRMED: uniquely reconciled working entry remains uncancelled 10 minutes beyond exit deadline")


def test_retained_journal_snapshot_disagrees_with_dashboard_fees():
    from app.services.journal import _compute_trade_stats_snapshot
    from app.services.projectx_trades import _to_metric_sample
    from app.services.projectx_metrics import compute_trade_summary
    row = SimpleNamespace(trade_timestamp=datetime(2026, 10, 23, 15, tzinfo=timezone.utc),
        pnl=100., fees=.36, commissions=.50, fee_scope="per_side", trade_date=None,
        entry_timestamp=None, order_id="fixture", symbol="MNQ", contract_id="CON.F.US.MNQ.Z26",
        side="SELL", size=1., price=20000.)
    journal = _compute_trade_stats_snapshot([row])
    dashboard = compute_trade_summary([_to_metric_sample(row)])
    assert journal["net"] == 99.14
    assert dashboard["net_pnl"] == 98.78
    print("CONFIRMED: same per-side event produces journal net 99.14 versus dashboard net 98.78")
