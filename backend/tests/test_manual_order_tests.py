from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Account, BotOrderAttempt
from app.manual_order_schemas import ManualOrderTestIn
from app.services import manual_order_tests as service
from app.services.bot_service import _ProviderMutationBlocked
from app.services.projectx_client import ProjectXClient, ProjectXClientError
from app.services.topbot import prepare_topbot

USER = "00000000-0000-0000-0000-000000000001"
CONTRACT = "CON.F.US.MNQ.Z26"


class Client:
    def __init__(self):
        self.calls = []
        self.simulated = True
        self.can_trade = True
        self.positions = []
        self.orders = []
        self.failure = None
        self.history = []

    def search_contracts(self, **kwargs):
        return [{"id": CONTRACT, "name": "MNQZ6", "active_contract": True, "tick_size": .25, "tick_value": .5}]

    def list_accounts(self, **kwargs):
        return [{"id": 101, "simulated": self.simulated, "can_trade": self.can_trade, "is_visible": True}]

    def search_open_positions(self, **kwargs):
        return self.positions

    def search_open_orders(self, **kwargs):
        return self.orders

    def search_orders(self, **kwargs):
        return self.history

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        return {"order_id": "broker-123", "raw_payload": {"success": True}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'manual.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "true")
    monkeypatch.setattr(service, "running_under_tests", lambda: False)
    with Session(engine) as db:
        db.add(Account(user_id=USER, provider="projectx", external_id="101", name="Practice", can_trade=True,
                       is_visible=True, trade_data_source="projectx", account_state="ACTIVE"))
        db.commit()
        yield db
    engine.dispose()


def payload(**kwargs):
    return ManualOrderTestIn(**({"request_id": str(uuid4()), "side": "BUY", "quantity": 1,
        "stop_loss_ticks": 20, "take_profit_ticks": 40, "confirm_live_order_routing": True} | kwargs))


def submit(db, client, request):
    return service.submit_manual_order_test(db, user_id=USER, account_id=101, payload=request, client=client)


@pytest.mark.parametrize("side,code", [("BUY", 0), ("SELL", 1)])
def test_market_entry_has_attached_brackets_and_durable_idempotency(db, side, code):
    client, request = Client(), payload(side=side, quantity=2)
    original_place = client.place_order
    def place(**kwargs):
        with Session(db.get_bind()) as observer:
            assert observer.query(BotOrderAttempt).one().status == "pending"
        return original_place(**kwargs)
    client.place_order = place
    result = submit(db, client, request)
    assert result["status"] == "submitted"
    assert result["provider_order_id"] == "broker-123"
    assert client.calls[0]["side"] == code
    assert client.calls[0]["order_type"] == 2
    assert client.calls[0]["size"] == 2
    assert client.calls[0]["stop_loss_bracket"] == {"ticks": 20, "type": 4}
    assert client.calls[0]["take_profit_bracket"] == {"ticks": 40, "type": 1}
    db.close()
    assert submit(db, client, request)["attempt_id"] == result["attempt_id"]
    assert len(client.calls) == 1


@pytest.mark.parametrize("side,stop_ticks,target_ticks", [("BUY", -20, 40), ("SELL", 20, -40)])
def test_manual_entry_converts_distances_at_broker_boundary(db, side, stop_ticks, target_ticks):
    client, request = Client(), payload(side=side)
    sent = []
    client.place_order = ProjectXClient.place_order.__get__(client)
    def transport(method, path, *, payload, with_auth):
        sent.append(payload)
        assert payload["stopLossBracket"] == {"ticks": stop_ticks, "type": 4}
        assert payload["takeProfitBracket"] == {"ticks": target_ticks, "type": 1}
        return {"orderId": 123, "success": True}
    client._request = transport
    result = submit(db, client, request)
    assert result["status"] == "submitted"
    assert result["stop_loss_ticks"] == 20
    assert result["take_profit_ticks"] == 40
    assert submit(db, client, request)["attempt_id"] == result["attempt_id"]
    assert len(sent) == 1


@pytest.mark.parametrize("field,value", [("quantity", 0), ("quantity", 11), ("quantity", 1.5),
    ("quantity", True), ("stop_loss_ticks", 0), ("take_profit_ticks", -1),
    ("confirm_live_order_routing", False), ("confirm_live_order_routing", 1)])
def test_input_is_strict(field, value):
    with pytest.raises(ValidationError):
        payload(**{field: value})


@pytest.mark.parametrize("condition", ["server_gate", "worker_gate", "test_env", "funded", "unknown", "not_tradable",
    "position", "working_order", "risk_limit", "active_bot", "other_user", "archived"])
def test_unsafe_entry_never_calls_broker(db, monkeypatch, condition):
    client, request = Client(), payload()
    if condition == "server_gate": monkeypatch.setenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "false")
    if condition == "worker_gate": monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "false")
    if condition == "test_env": monkeypatch.setattr(service, "running_under_tests", lambda: True)
    if condition == "funded": client.simulated = False
    if condition == "unknown": client.simulated = None
    if condition == "not_tradable": client.can_trade = False
    if condition == "position": client.positions = [{"size": 1}]
    if condition == "working_order": client.orders = [{"order_id": "existing"}]
    if condition == "risk_limit": request = payload(quantity=10, stop_loss_ticks=100)
    if condition == "active_bot":
        config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id=CONTRACT)
        config.enabled = True
        db.commit()
    if condition == "other_user":
        db.query(Account).one().user_id = "00000000-0000-0000-0000-000000000002"
        db.commit()
    if condition == "archived":
        from datetime import datetime, timezone
        db.query(Account).one().archived_at = datetime.now(timezone.utc)
        db.commit()
    with pytest.raises((ValueError, LookupError, _ProviderMutationBlocked)):
        submit(db, client, request)
    assert not client.calls


def test_unknown_outcome_never_retries_and_blocks_new_request(db):
    client, request = Client(), payload()
    client.failure = ProjectXClientError("timed out", submission_outcome_unknown=True)
    assert submit(db, client, request)["status"] == "submission_unknown"
    assert submit(db, client, request)["status"] == "submission_unknown"
    with pytest.raises(ValueError, match="previous order"):
        submit(db, client, payload())
    assert len(client.calls) == 1


def test_reused_key_cannot_change_order_settings(db):
    client, request = Client(), payload()
    submit(db, client, request)
    with pytest.raises(ValueError, match="different order settings"):
        submit(db, client, payload(request_id=str(request.request_id), side="SELL"))
    assert len(client.calls) == 1


def test_provider_rejection_is_reported_without_retry_or_dropping_brackets(db):
    client = Client()
    client.failure = ProjectXClientError("Enable Auto OCO Brackets")
    result = submit(db, client, payload())
    assert result["status"] == "error"
    assert "Auto OCO" in result["message"]
    assert len(client.calls) == 1


def test_status_reads_cannot_place_orders(db):
    client = Client()
    result = service.get_manual_order_test_state(db, user_id=USER, account_id=101, client=client)
    assert result["contract"]["id"] == CONTRACT
    assert result["recent_attempts"] == []
    assert not client.calls


@pytest.mark.parametrize("exit_type,exit_volume,parent_id,expected", [
    (1, 2, "broker-123", "take_profit_hit"),
    (4, 2, "broker-123", "stop_loss_hit"),
    (1, 1, "broker-123", "partially_closed"),
    (1, 2, "unrelated", "flat_exit_unconfirmed"),
])
def test_status_reconciles_only_linked_bracket_fills(db, exit_type, exit_volume, parent_id, expected):
    client = Client()
    submit(db, client, payload(side="SELL", quantity=2))
    client.history = [
        {"order_id": "broker-123", "account_id": 101, "contract_id": CONTRACT, "status": 2,
         "raw_payload": {"side": 1, "fillVolume": 2, "filledPrice": 24000}},
        {"order_id": "exit", "account_id": 101, "contract_id": CONTRACT, "status": 2,
         "raw_payload": {"side": 0, "fillVolume": exit_volume, "filledPrice": 23990,
                         "type": exit_type, "parentOrderId": parent_id}},
    ]
    state = service.get_manual_order_test_state(db, user_id=USER, account_id=101, client=client)
    result = state["recent_attempts"][0]
    assert result["execution_status"] == expected
    assert result["entry_fill_price"] == 24000
    if expected in {"take_profit_hit", "stop_loss_hit"}:
        assert result["exit_fill_price"] == 23990
    else:
        assert "exit_fill_price" not in result
    assert result["status"] == "submitted"  # Submission audit is preserved.
    assert len(client.calls) == 1  # Status reads never submit a new order.


@pytest.mark.parametrize("status,volume,expected", [(1, 0, "working"), (3, 0, "cancelled"),
    (4, 0, "expired"), (5, 0, "rejected"), (1, 1, "partially_filled"), (2, 2, "filled")])
def test_status_tracks_entry_without_inventing_exit(db, status, volume, expected):
    client = Client()
    submit(db, client, payload(quantity=2))
    client.positions = [{"account_id": 101, "contract_id": CONTRACT}]
    client.history = [{"order_id": "broker-123", "account_id": 101, "contract_id": CONTRACT,
        "status": status, "raw_payload": {"side": 0, "fillVolume": volume, "filledPrice": 24000}}]
    state = service.get_manual_order_test_state(db, user_id=USER, account_id=101, client=client)
    assert state["recent_attempts"][0]["execution_status"] == expected


def test_status_ignores_exit_from_different_account_or_contract(db):
    client = Client()
    submit(db, client, payload())
    client.history = [{"order_id": "broker-123", "account_id": 101, "contract_id": CONTRACT,
        "status": 2, "raw_payload": {"side": 0, "fillVolume": 1, "filledPrice": 24000}}]
    for account, contract in [(102, CONTRACT), (101, "other")]:
        client.history.append({"order_id": "other", "account_id": account, "contract_id": contract,
            "status": 2, "raw_payload": {"side": 1, "fillVolume": 1, "filledPrice": 24010,
                "type": 1, "parentOrderId": "broker-123"}})
    state = service.get_manual_order_test_state(db, user_id=USER, account_id=101, client=client)
    assert state["recent_attempts"][0]["execution_status"] == "flat_exit_unconfirmed"


def test_uncertain_response_recovers_by_exact_broker_tag_without_resubmitting(db):
    client, request = Client(), payload()
    client.failure = ProjectXClientError("lost response", submission_outcome_unknown=True)
    submit(db, client, request)
    client.history = [{"order_id": "accepted-456", "account_id": 101, "contract_id": CONTRACT,
        "custom_tag": f"ts-manual-{request.request_id}", "status": 2, "raw_payload": {"side": 0, "size": 1}}]
    recovered = submit(db, client, request)
    assert recovered["status"] == "submitted"
    assert recovered["provider_order_id"] == "accepted-456"
    assert len(client.calls) == 1


def test_concurrent_identical_clicks_have_one_broker_submission(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, request = Client(), payload()
    barrier = Barrier(2)
    client.search_open_orders = lambda **kwargs: (barrier.wait(timeout=5), [])[1]
    engine = db.get_bind()
    def run():
        with Session(engine) as session:
            return submit(session, client, request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0]["attempt_id"] == results[1]["attempt_id"]
    assert len(client.calls) == 1


def test_crash_after_broker_acceptance_leaves_pending_claim_and_never_resubmits(db, monkeypatch):
    client, request = Client(), payload()
    original = db.commit
    def fail_result_commit():
        if any(isinstance(row, BotOrderAttempt) and row.status == "submitted" for row in db.dirty):
            raise RuntimeError("simulated database outage")
        original()
    monkeypatch.setattr(db, "commit", fail_result_commit)
    with pytest.raises(RuntimeError, match="database outage"):
        submit(db, client, request)
    db.rollback()
    monkeypatch.setattr(db, "commit", original)
    assert submit(db, client, request)["status"] == "pending"
    assert len(client.calls) == 1
