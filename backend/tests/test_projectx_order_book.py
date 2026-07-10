import asyncio
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.projectx_order_book import (  # noqa: E402
    MarketByPriceBook,
    ProjectXMarketDepthSession,
    ProjectXOrderBookRegistry,
    _SIGNALR_RECORD_SEPARATOR,
)


CONTRACT_NQ = "CON.F.US.ENQ.U26"
CONTRACT_MNQ = "CON.F.US.MNQ.U26"
CONTRACT_MNQ_NEXT = "CON.F.US.MNQ.Z26"


def _depth(
    *,
    timestamp: str,
    depth_type: int,
    price: float = 20000.0,
    volume: int = 10,
    current_volume: int | None = 5,
):
    payload = {
        "timestamp": timestamp,
        "type": depth_type,
        "price": price,
        "volume": volume,
    }
    if current_volume is not None:
        payload["currentVolume"] = current_volume
    return payload


def test_market_by_price_updates_sort_and_remove_absolute_volume_levels():
    book = MarketByPriceBook(CONTRACT_NQ)

    first = book.apply(
        _depth(
            timestamp="2026-07-10T13:00:00Z",
            depth_type=1,
            price=20001.0,
            volume=12,
            current_volume=3,
        )
    )
    book.apply(
        _depth(
            timestamp="2026-07-10T13:00:01Z",
            depth_type=10,
            price=20000.5,
            volume=7,
        )
    )
    book.apply(
        _depth(
            timestamp="2026-07-10T13:00:02Z",
            depth_type=2,
            price=19999.5,
            volume=9,
        )
    )
    book.apply(
        _depth(
            timestamp="2026-07-10T13:00:03Z",
            depth_type=9,
            price=20000.0,
            volume=4,
        )
    )

    assert first == {
        "contract_id": CONTRACT_NQ,
        "sequence": 1,
        "timestamp": "2026-07-10T13:00:00Z",
        "side": "ask",
        "price": 20001,
        "size": 12,
        "volume": 12,
        "current_volume": 3,
    }
    assert book.snapshot()["asks"] == [
        {"price": 20000.5, "size": 7},
        {"price": 20001, "size": 12},
    ]
    assert book.snapshot()["bids"] == [
        {"price": 20000, "size": 4},
        {"price": 19999.5, "size": 9},
    ]

    removed = book.apply(
        _depth(
            timestamp="2026-07-10T13:00:04Z",
            depth_type=1,
            price=20001.0,
            volume=0,
            current_volume=99,
        )
    )
    assert removed["size"] == 0
    assert book.snapshot()["asks"] == [{"price": 20000.5, "size": 7}]


def test_market_by_price_reset_is_authoritative_and_rebuild_accepts_identical_level():
    book = MarketByPriceBook(CONTRACT_NQ)
    original = _depth(
        timestamp="2026-07-10T13:00:10Z",
        depth_type=2,
        price=20000,
        volume=11,
    )
    assert book.apply(original) is not None

    reset = book.apply({"timestamp": "2026-07-10T12:59:00Z", "type": 6})
    assert reset["reset"] is True
    assert reset["bids"] == []
    assert reset["asks"] == []

    # Reset creates a new epoch even if the provider reuses the prior payload.
    rebuilt = book.apply(original)
    assert rebuilt is not None
    assert book.snapshot()["bids"] == [{"price": 20000, "size": 11}]

    # An identical reset remains authoritative after levels have rebuilt.
    repeated_reset = book.apply({"timestamp": "2026-07-10T12:59:00Z", "type": 6})
    assert repeated_reset is not None
    assert repeated_reset["bids"] == []


def test_market_by_price_rejects_malformed_duplicates_and_strictly_stale_levels():
    book = MarketByPriceBook(CONTRACT_NQ)
    initial = _depth(
        timestamp="2026-07-10T13:00:10Z",
        depth_type=4,
        price=20000,
        volume=10,
    )
    assert book.apply(initial) is not None
    assert book.apply(dict(initial)) is None
    assert book.apply({**initial, "timestamp": "2026-07-10T13:00:09Z", "volume": 8}) is None
    assert book.apply({**initial, "volume": "not-a-number"}) is None
    assert book.apply({key: value for key, value in initial.items() if key != "volume"}) is None
    assert book.apply({**initial, "currentVolume": -1}) is None

    # Equal timestamps are not ordered by documented metadata, so distinct values
    # remain valid in arrival order.
    equal_timestamp = book.apply({**initial, "volume": 8})
    assert equal_timestamp is not None
    assert book.snapshot()["bids"] == [{"price": 20000, "size": 8}]


def test_market_by_price_ignores_non_resting_dom_types():
    book = MarketByPriceBook(CONTRACT_NQ)
    for depth_type in (0, 5, 7, 8, 11, 999):
        assert (
            book.apply(
                _depth(
                    timestamp=f"2026-07-10T13:00:{depth_type % 60:02d}Z",
                    depth_type=depth_type,
                )
            )
            is None
        )
    assert book.snapshot()["bids"] == []
    assert book.snapshot()["asks"] == []


def test_clear_for_reconnect_always_emits_empty_reset_snapshot_and_new_epoch():
    book = MarketByPriceBook(CONTRACT_NQ)
    first = book.clear_for_reconnect()
    second = book.clear_for_reconnect()

    assert first["reset"] is True
    assert second["reset"] is True
    assert first["sequence"] == 1
    assert second["sequence"] == 2


class _StubClient:
    def get_access_token(self):
        return "server-only-projectx-token"


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.incoming: asyncio.Queue[object] = asyncio.Queue()

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return "{}" + _SIGNALR_RECORD_SEPARATOR

    async def close(self):
        await self.incoming.put(StopAsyncIteration)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is StopAsyncIteration:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeConnectionContext:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_args):
        return False


class _FakeConnector:
    def __init__(self, websocket_factory=_FakeWebSocket):
        self.websockets: list[_FakeWebSocket] = []
        self.urls: list[str] = []
        self.websocket_factory = websocket_factory

    def __call__(self, url, **_kwargs):
        websocket = self.websocket_factory()
        self.websockets.append(websocket)
        self.urls.append(url)
        return _FakeConnectionContext(websocket)


class _FailingUnsubscribeWebSocket(_FakeWebSocket):
    async def send(self, message):
        if "UnsubscribeContractMarketDepth" in str(message):
            raise ConnectionError("socket already failed")
        await super().send(message)


class _FailingDynamicSubscribeWebSocket(_FakeWebSocket):
    async def send(self, message):
        if "SubscribeContractMarketDepth" in str(message) and CONTRACT_MNQ in str(message):
            raise ConnectionError("dynamic subscription failed")
        await super().send(message)


class _HangingDynamicSubscribeWebSocket(_FakeWebSocket):
    async def send(self, message):
        if "SubscribeContractMarketDepth" in str(message) and CONTRACT_MNQ in str(message):
            await asyncio.Event().wait()
        await super().send(message)


class _SlowDynamicSubscribeWebSocket(_FakeWebSocket):
    def __init__(self, send_started: asyncio.Event, release_send: asyncio.Event):
        super().__init__()
        self.send_started = send_started
        self.release_send = release_send

    async def send(self, message):
        if "SubscribeContractMarketDepth" in str(message) and CONTRACT_MNQ in str(message):
            self.send_started.set()
            await self.release_send.wait()
        await super().send(message)


class _IdentityRaceConnector(_FakeConnector):
    def __init__(self, send_started: asyncio.Event, release_send: asyncio.Event):
        super().__init__()
        self.send_started = send_started
        self.release_send = release_send

    def __call__(self, url, **_kwargs):
        if not self.websockets:
            websocket = _SlowDynamicSubscribeWebSocket(
                self.send_started,
                self.release_send,
            )
        else:
            websocket = _FakeWebSocket()
        self.websockets.append(websocket)
        self.urls.append(url)
        return _FakeConnectionContext(websocket)


class _EarlyCompletionWebSocket(_FakeWebSocket):
    def __init__(self, completion_enqueued: asyncio.Event, release_send: asyncio.Event):
        super().__init__()
        self.completion_enqueued = completion_enqueued
        self.release_send = release_send

    async def send(self, message):
        if "SubscribeContractMarketDepth" in str(message) and CONTRACT_MNQ in str(message):
            invocation = json.loads(str(message).rstrip(_SIGNALR_RECORD_SEPARATOR))
            await self.incoming.put(
                json.dumps(
                    {
                        "type": 3,
                        "invocationId": invocation["invocationId"],
                        "error": "subscription rejected",
                    }
                )
                + _SIGNALR_RECORD_SEPARATOR
            )
            self.completion_enqueued.set()
            await self.release_send.wait()
        await super().send(message)


def _invocations(websocket: _FakeWebSocket, target: str):
    output = []
    for raw in websocket.sent:
        for chunk in raw.split(_SIGNALR_RECORD_SEPARATOR):
            if not chunk:
                continue
            payload = json.loads(chunk)
            if payload.get("target") == target:
                output.append(payload)
    return output


async def _wait_until(predicate, *, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def _drain(queue: asyncio.Queue):
    output = []
    while not queue.empty():
        output.append(queue.get_nowait())
    return output


def test_duplicate_clients_share_one_provider_subscription_and_reference_count_unsubscribe():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        first = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        websocket = connector.websockets[0]
        await _wait_until(
            lambda: len(_invocations(websocket, "SubscribeContractMarketDepth")) == 1
        )

        second = await session.subscribe(CONTRACT_NQ)
        await asyncio.sleep(0)
        assert len(_invocations(websocket, "SubscribeContractMarketDepth")) == 1

        await first.close()
        assert _invocations(websocket, "UnsubscribeContractMarketDepth") == []

        await second.close()
        assert len(_invocations(websocket, "UnsubscribeContractMarketDepth")) == 1
        await session.close()

        assert "server-only-projectx-token" in connector.urls[0]

    asyncio.run(scenario())


def test_failed_provider_unsubscribe_does_not_leak_local_subscription_or_runner():
    async def scenario():
        connector = _FakeConnector(_FailingUnsubscribeWebSocket)
        session = ProjectXMarketDepthSession(client=_StubClient(), connect_factory=connector)
        close_callbacks = []
        subscription = await session.subscribe(
            CONTRACT_NQ,
            on_close=lambda _subscription: close_callbacks.append("closed"),
        )
        await _wait_until(lambda: len(connector.websockets) == 1)
        await _wait_until(
            lambda: len(
                _invocations(connector.websockets[0], "SubscribeContractMarketDepth")
            )
            == 1
        )

        await subscription.close()

        assert await session.is_idle() is True
        assert session._runner_task is None
        assert close_callbacks == ["closed"]
        await session.close()

    asyncio.run(scenario())


def test_failed_unsubscribe_restarts_shared_socket_with_only_active_contracts():
    async def scenario():
        connector = _FakeConnector(_FailingUnsubscribeWebSocket)
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        nq = await session.subscribe(CONTRACT_NQ)
        mnq = await session.subscribe(CONTRACT_MNQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        await _wait_until(
            lambda: {
                invocation["arguments"][0]
                for invocation in _invocations(
                    connector.websockets[0],
                    "SubscribeContractMarketDepth",
                )
            }
            == {CONTRACT_NQ, CONTRACT_MNQ}
        )
        _drain(nq.queue)

        await mnq.close()

        await _wait_until(lambda: len(connector.websockets) >= 2)
        second_websocket = connector.websockets[1]
        await _wait_until(
            lambda: len(
                _invocations(second_websocket, "SubscribeContractMarketDepth")
            )
            == 1
        )
        assert _invocations(second_websocket, "SubscribeContractMarketDepth")[0][
            "arguments"
        ] == [CONTRACT_NQ]
        assert CONTRACT_MNQ not in session._provider_subscribed
        recovery_events = _drain(nq.queue)
        assert any(
            event["event"] == "state" and event["data"]["state"] == "reconnecting"
            for event in recovery_events
        )
        assert any(
            event["event"] == "snapshot" and event["data"]["reset"] is True
            for event in recovery_events
        )

        await nq.close()
        await session.close()

    asyncio.run(scenario())


def test_failed_dynamic_provider_subscribe_rolls_back_local_channel():
    async def scenario():
        connector = _FakeConnector(_FailingDynamicSubscribeWebSocket)
        session = ProjectXMarketDepthSession(client=_StubClient(), connect_factory=connector)
        existing = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        await _wait_until(
            lambda: len(
                _invocations(connector.websockets[0], "SubscribeContractMarketDepth")
            )
            == 1
        )

        try:
            await session.subscribe(CONTRACT_MNQ)
        except ConnectionError as exc:
            assert str(exc) == "dynamic subscription failed"
        else:
            raise AssertionError("expected provider subscription failure")

        assert set(session._channels) == {CONTRACT_NQ}
        await existing.close()
        await session.close()

    asyncio.run(scenario())


def test_provider_send_timeout_rolls_back_and_recovers_remaining_contracts():
    async def scenario():
        connector = _FakeConnector(_HangingDynamicSubscribeWebSocket)
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
            send_timeout_seconds=0.05,
        )
        existing = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        await _wait_until(
            lambda: len(
                _invocations(connector.websockets[0], "SubscribeContractMarketDepth")
            )
            == 1
        )

        try:
            await session.subscribe(CONTRACT_MNQ)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("expected bounded provider send timeout")

        assert set(session._channels) == {CONTRACT_NQ}
        await _wait_until(lambda: len(connector.websockets) >= 2)
        await _wait_until(
            lambda: {
                invocation["arguments"][0]
                for invocation in _invocations(
                    connector.websockets[1],
                    "SubscribeContractMarketDepth",
                )
            }
            == {CONTRACT_NQ}
        )

        await existing.close()
        await session.close()

    asyncio.run(scenario())


def test_old_socket_slow_send_cannot_suppress_new_socket_resubscription():
    async def scenario():
        send_started = asyncio.Event()
        release_send = asyncio.Event()
        connector = _IdentityRaceConnector(send_started, release_send)
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
            send_timeout_seconds=1,
        )
        nq = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        first_websocket = connector.websockets[0]
        await _wait_until(
            lambda: len(
                _invocations(first_websocket, "SubscribeContractMarketDepth")
            )
            == 1
        )

        mnq_task = asyncio.create_task(session.subscribe(CONTRACT_MNQ))
        await send_started.wait()
        await first_websocket.incoming.put(ConnectionError("old connection lost"))
        await asyncio.sleep(0.03)
        # Deactivation waits for the old socket's in-flight send instead of
        # activating a new connection against shared subscription state.
        assert len(connector.websockets) == 1

        release_send.set()
        mnq = await mnq_task
        await _wait_until(lambda: len(connector.websockets) >= 2)
        second_websocket = connector.websockets[1]
        await _wait_until(
            lambda: {
                invocation["arguments"][0]
                for invocation in _invocations(
                    second_websocket,
                    "SubscribeContractMarketDepth",
                )
            }
            == {CONTRACT_NQ, CONTRACT_MNQ}
        )
        assert session._provider_subscribed == {CONTRACT_NQ, CONTRACT_MNQ}

        await nq.close()
        await mnq.close()
        await session.close()

    asyncio.run(scenario())


def test_early_subscription_error_completion_cannot_be_overwritten_after_send():
    async def scenario():
        completion_enqueued = asyncio.Event()
        release_send = asyncio.Event()
        websocket = _EarlyCompletionWebSocket(completion_enqueued, release_send)
        connector = _FakeConnector(lambda: websocket)
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            send_timeout_seconds=1,
        )
        nq = await session.subscribe(CONTRACT_NQ)
        await _wait_until(
            lambda: len(_invocations(websocket, "SubscribeContractMarketDepth")) == 1
        )

        mnq_task = asyncio.create_task(session.subscribe(CONTRACT_MNQ))
        await completion_enqueued.wait()
        release_send.set()
        mnq = await mnq_task
        await _wait_until(
            lambda: any(
                event.get("event") == "state"
                and event.get("data", {}).get("state") == "unavailable"
                for event in tuple(mnq.queue._queue)
            )
        )
        assert CONTRACT_MNQ not in session._provider_subscribed

        await nq.close()
        await mnq.close()
        await session.close()

    asyncio.run(scenario())


def test_successful_resubscribe_completion_recovers_existing_clients_to_connected():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(client=_StubClient(), connect_factory=connector)
        existing = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        websocket = connector.websockets[0]
        await _wait_until(
            lambda: len(_invocations(websocket, "SubscribeContractMarketDepth")) == 1
        )
        first_invocation = _invocations(websocket, "SubscribeContractMarketDepth")[0]
        await websocket.incoming.put(
            json.dumps(
                {
                    "type": 3,
                    "invocationId": first_invocation["invocationId"],
                    "error": "temporarily rejected",
                }
            )
            + _SIGNALR_RECORD_SEPARATOR
        )
        await _wait_until(
            lambda: any(
                event["event"] == "state" and event["data"]["state"] == "unavailable"
                for event in tuple(existing.queue._queue)
            )
        )
        _drain(existing.queue)

        duplicate = await session.subscribe(CONTRACT_NQ)
        await _wait_until(
            lambda: len(_invocations(websocket, "SubscribeContractMarketDepth")) == 2
        )
        second_invocation = _invocations(websocket, "SubscribeContractMarketDepth")[1]
        await websocket.incoming.put(
            json.dumps(
                {"type": 3, "invocationId": second_invocation["invocationId"]}
            )
            + _SIGNALR_RECORD_SEPARATOR
        )
        await _wait_until(
            lambda: any(
                event["event"] == "state" and event["data"]["state"] == "connected"
                for event in tuple(existing.queue._queue)
            )
        )

        await existing.close()
        await duplicate.close()
        await session.close()

    asyncio.run(scenario())


def test_delayed_old_subscribe_error_cannot_override_newer_resubscribe():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(client=_StubClient(), connect_factory=connector)
        keepalive = await session.subscribe(CONTRACT_MNQ)
        original = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        websocket = connector.websockets[0]
        await _wait_until(
            lambda: len(
                [
                    invocation
                    for invocation in _invocations(
                        websocket,
                        "SubscribeContractMarketDepth",
                    )
                    if invocation["arguments"] == [CONTRACT_NQ]
                ]
            )
            == 1
        )
        original_subscribe = [
            invocation
            for invocation in _invocations(websocket, "SubscribeContractMarketDepth")
            if invocation["arguments"] == [CONTRACT_NQ]
        ][0]

        await original.close()
        replacement = await session.subscribe(CONTRACT_NQ)
        await _wait_until(
            lambda: len(
                [
                    invocation
                    for invocation in _invocations(
                        websocket,
                        "SubscribeContractMarketDepth",
                    )
                    if invocation["arguments"] == [CONTRACT_NQ]
                ]
            )
            == 2
        )
        replacement_subscribe = [
            invocation
            for invocation in _invocations(websocket, "SubscribeContractMarketDepth")
            if invocation["arguments"] == [CONTRACT_NQ]
        ][1]
        _drain(replacement.queue)

        await websocket.incoming.put(
            json.dumps(
                {
                    "type": 3,
                    "invocationId": original_subscribe["invocationId"],
                    "error": "delayed old rejection",
                }
            )
            + _SIGNALR_RECORD_SEPARATOR
        )
        await websocket.incoming.put(
            json.dumps(
                {"type": 3, "invocationId": replacement_subscribe["invocationId"]}
            )
            + _SIGNALR_RECORD_SEPARATOR
        )
        await _wait_until(
            lambda: replacement_subscribe["invocationId"]
            not in session._pending_invocations
        )

        assert CONTRACT_NQ in session._provider_subscribed
        assert not any(
            event["event"] == "state" and event["data"]["state"] == "unavailable"
            for event in _drain(replacement.queue)
        )
        unsubscribe_count_before = len(
            [
                invocation
                for invocation in _invocations(
                    websocket,
                    "UnsubscribeContractMarketDepth",
                )
                if invocation["arguments"] == [CONTRACT_NQ]
            ]
        )
        await replacement.close()
        unsubscribe_count_after = len(
            [
                invocation
                for invocation in _invocations(
                    websocket,
                    "UnsubscribeContractMarketDepth",
                )
                if invocation["arguments"] == [CONTRACT_NQ]
            ]
        )
        assert unsubscribe_count_after == unsubscribe_count_before + 1

        await keepalive.close()
        await session.close()

    asyncio.run(scenario())


def test_registry_subscription_lease_prevents_idle_prune_race():
    async def scenario():
        connector = _FakeConnector()
        entered = asyncio.Event()
        release = asyncio.Event()

        class DelayedSession(ProjectXMarketDepthSession):
            async def subscribe(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return await super().subscribe(*args, **kwargs)

        session = DelayedSession(client=_StubClient(), connect_factory=connector)
        other_session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=_FakeConnector(),
        )
        registry = ProjectXOrderBookRegistry(session_factory=lambda **_kwargs: session)
        registry._sessions["user-1"] = session
        registry._sessions["user-2"] = other_session

        subscribe_task = asyncio.create_task(
            registry.subscribe(
                user_id="user-1",
                client=_StubClient(),
                contract_id=CONTRACT_NQ,
            )
        )
        await entered.wait()

        # A slow same-user lease must not block an unrelated user's subscribe.
        other_subscription = await asyncio.wait_for(
            registry.subscribe(
                user_id="user-2",
                client=_StubClient(),
                contract_id=CONTRACT_MNQ,
            ),
            timeout=0.25,
        )

        class ClosingSubscription:
            pass

        closing = ClosingSubscription()
        closing.session = session
        prune_task = asyncio.create_task(registry._prune_subscription(closing))
        await asyncio.sleep(0)
        assert prune_task.done() is False

        release.set()
        subscription = await subscribe_task
        await prune_task
        assert registry._sessions["user-1"] is session

        await subscription.close()
        await other_subscription.close()
        assert "user-1" not in registry._sessions
        assert "user-2" not in registry._sessions
        await registry.close()

    asyncio.run(scenario())


def test_registry_removes_new_idle_session_after_subscribe_failure():
    async def scenario():
        class FailingSession(ProjectXMarketDepthSession):
            async def subscribe(self, *_args, **_kwargs):
                raise ConnectionError("subscribe failed before registration")

        registry = ProjectXOrderBookRegistry(
            session_factory=lambda **_kwargs: FailingSession(
                client=_StubClient(),
                connect_factory=_FakeConnector(),
            )
        )
        try:
            await registry.subscribe(
                user_id="failed-user",
                client=_StubClient(),
                contract_id=CONTRACT_NQ,
            )
        except ConnectionError:
            pass
        else:
            raise AssertionError("expected subscribe failure")

        assert "failed-user" not in registry._sessions
        assert "failed-user" not in registry._user_slots
        await registry.close()

    asyncio.run(scenario())


def test_signalr_frames_keep_contract_books_and_subscribers_separate_and_snapshot_current_state():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(client=_StubClient(), connect_factory=connector)
        nq = await session.subscribe(CONTRACT_NQ)
        mnq = await session.subscribe(CONTRACT_MNQ)
        mnq_next = await session.subscribe(CONTRACT_MNQ_NEXT)
        await _wait_until(lambda: len(connector.websockets) == 1)
        _drain(nq.queue)
        _drain(mnq.queue)
        _drain(mnq_next.queue)

        await session.process_signalr_frame(
            {
                "type": 1,
                "target": "GatewayDepth",
                "arguments": [
                    CONTRACT_NQ,
                    [
                        None,
                        _depth(
                            timestamp="2026-07-10T13:00:00Z",
                            depth_type=2,
                            price=20000,
                            volume=6,
                        ),
                    ],
                ],
            }
        )

        nq_events = _drain(nq.queue)
        assert [event for event in nq_events if event["event"] == "update"][-1]["data"][
            "contract_id"
        ] == CONTRACT_NQ
        assert [event for event in _drain(mnq.queue) if event["event"] == "update"] == []

        await session.process_signalr_frame(
            {
                "type": 1,
                "target": "GatewayDepth",
                "arguments": [
                    CONTRACT_MNQ,
                    _depth(
                        timestamp="2026-07-10T13:00:01Z",
                        depth_type=1,
                        price=20001,
                        volume=3,
                    ),
                ],
            }
        )
        assert any(event["event"] == "update" for event in _drain(mnq.queue))
        assert [event for event in _drain(mnq_next.queue) if event["event"] == "update"] == []

        late_joiner = await session.subscribe(CONTRACT_NQ)
        snapshot = [
            event["data"] for event in late_joiner.initial_events if event["event"] == "snapshot"
        ][0]
        assert snapshot["bids"] == [{"price": 20000, "size": 6}]
        assert snapshot["contract_id"] == CONTRACT_NQ

        await nq.close()
        await mnq.close()
        await mnq_next.close()
        await late_joiner.close()
        await session.close()

    asyncio.run(scenario())


def test_gateway_reset_is_sent_as_snapshot():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(client=_StubClient(), connect_factory=connector)
        subscription = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        _drain(subscription.queue)

        await session.process_signalr_frame(
            {
                "type": 1,
                "target": "GatewayDepth",
                "arguments": [CONTRACT_NQ, {"timestamp": "2026-07-10T13:01:00Z", "type": 6}],
            }
        )
        events = _drain(subscription.queue)
        snapshots = [event["data"] for event in events if event["event"] == "snapshot"]
        assert snapshots[-1]["reset"] is True
        assert snapshots[-1]["bids"] == []
        assert snapshots[-1]["asks"] == []

        await subscription.close()
        await session.close()

    asyncio.run(scenario())


def test_active_contracts_resubscribe_once_after_reconnect_and_emit_reset_snapshot():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        subscription = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        first = connector.websockets[0]
        await _wait_until(
            lambda: len(_invocations(first, "SubscribeContractMarketDepth")) == 1
        )

        await session.process_signalr_frame(
            {
                "type": 1,
                "target": "GatewayDepth",
                "arguments": [
                    CONTRACT_NQ,
                    _depth(
                        timestamp="2026-07-10T13:00:00Z",
                        depth_type=2,
                        price=20000,
                        volume=6,
                    ),
                ],
            }
        )
        _drain(subscription.queue)
        await first.incoming.put(ConnectionError("network lost"))

        await _wait_until(lambda: len(connector.websockets) >= 2)
        second = connector.websockets[1]
        await _wait_until(
            lambda: len(_invocations(second, "SubscribeContractMarketDepth")) == 1
        )
        assert len(_invocations(first, "SubscribeContractMarketDepth")) == 1
        assert len(_invocations(second, "SubscribeContractMarketDepth")) == 1

        events = _drain(subscription.queue)
        assert any(
            event["event"] == "state" and event["data"]["state"] == "reconnecting"
            for event in events
        )
        assert any(
            event["event"] == "snapshot"
            and event["data"]["reset"] is True
            and event["data"]["bids"] == []
            for event in events
        )

        await subscription.close()
        await session.close()

    asyncio.run(scenario())


def test_failed_unsubscribe_completion_reconnects_to_current_contract_intent():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        nq = await session.subscribe(CONTRACT_NQ)
        mnq = await session.subscribe(CONTRACT_MNQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        first = connector.websockets[0]
        await _wait_until(
            lambda: len(_invocations(first, "SubscribeContractMarketDepth")) == 2
        )

        await mnq.close()
        unsubscribe = _invocations(first, "UnsubscribeContractMarketDepth")[-1]
        await first.incoming.put(
            json.dumps(
                {
                    "type": 3,
                    "invocationId": unsubscribe["invocationId"],
                    "error": "unsubscribe rejected",
                }
            )
            + _SIGNALR_RECORD_SEPARATOR
        )

        await _wait_until(lambda: len(connector.websockets) >= 2)
        second = connector.websockets[1]
        await _wait_until(
            lambda: len(_invocations(second, "SubscribeContractMarketDepth")) == 1
        )
        assert _invocations(second, "SubscribeContractMarketDepth")[0]["arguments"] == [
            CONTRACT_NQ
        ]

        await nq.close()
        await session.close()

    asyncio.run(scenario())


def test_late_old_generation_depth_is_ignored_after_reconnect_reset():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        subscription = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        first_generation = session._connection_generation
        await connector.websockets[0].incoming.put(ConnectionError("connection lost"))
        await _wait_until(lambda: len(connector.websockets) >= 2)
        await _wait_until(lambda: session._connection_generation > first_generation)
        _drain(subscription.queue)
        sequence_before = session._channels[CONTRACT_NQ].book.sequence

        await session.process_signalr_frame(
            {
                "type": 1,
                "target": "GatewayDepth",
                "arguments": [
                    CONTRACT_NQ,
                    _depth(
                        timestamp="2026-07-10T13:20:00Z",
                        depth_type=2,
                        price=19999,
                        volume=99,
                    ),
                ],
            },
            connection_generation=first_generation,
        )
        assert session._channels[CONTRACT_NQ].book.sequence == sequence_before
        assert session._channels[CONTRACT_NQ].book.snapshot()["bids"] == []
        assert [event for event in _drain(subscription.queue) if event["event"] == "update"] == []

        await subscription.close()
        await session.close()

    asyncio.run(scenario())


def test_normal_websocket_end_reconnects_and_resubscribes():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            reconnect_base_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        subscription = await session.subscribe(CONTRACT_NQ)
        await _wait_until(lambda: len(connector.websockets) == 1)
        await connector.websockets[0].incoming.put(StopAsyncIteration)
        await _wait_until(lambda: len(connector.websockets) >= 2)
        await _wait_until(
            lambda: len(
                _invocations(connector.websockets[1], "SubscribeContractMarketDepth")
            )
            == 1
        )
        assert any(
            event["event"] == "state" and event["data"]["state"] == "reconnecting"
            for event in _drain(subscription.queue)
        )

        await subscription.close()
        await session.close()

    asyncio.run(scenario())


def test_queue_overflow_coalesces_to_snapshot_and_retains_state():
    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketDepthSession(
            client=_StubClient(),
            connect_factory=connector,
            subscriber_queue_size=8,
        )
        subscription = await session.subscribe(CONTRACT_NQ)
        for index in range(8):
            subscription.queue.put_nowait({"event": "update", "data": {"sequence": index}})

        await session._broadcast_contract_state(CONTRACT_NQ, "reconnecting")
        events = _drain(subscription.queue)
        assert [event["event"] for event in events] == ["snapshot", "state"]
        assert events[-1]["data"]["state"] == "reconnecting"

        for index in range(8):
            subscription.queue.put_nowait({"event": "update", "data": {"sequence": index}})
        await session.process_signalr_frame(
            {
                "type": 1,
                "target": "GatewayDepth",
                "arguments": [CONTRACT_NQ, {"timestamp": "2026-07-10T13:10:00Z", "type": 6}],
            }
        )
        reset_events = _drain(subscription.queue)
        assert len(reset_events) == 1
        assert reset_events[0]["event"] == "snapshot"
        assert reset_events[0]["data"]["reset"] is True

        await subscription.close()
        await session.close()

    asyncio.run(scenario())
