import asyncio
from types import SimpleNamespace

import pytest

import app.main as main


@pytest.mark.parametrize("stop_result", [True, False, RuntimeError("worker failure")])
def test_lifespan_cleans_ancillary_resources_and_preserves_incomplete_worker(monkeypatch, stop_result):
    calls = []

    async def start():
        calls.append("start")

    async def stop():
        calls.append("stop")
        if isinstance(stop_result, Exception):
            raise stop_result
        return stop_result

    async def close_book():
        calls.append("book_closed")

    async def cleanup_loop():
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("cleanup_cancelled")

    worker = SimpleNamespace(start=start, stop=stop)
    monkeypatch.setattr(main, "_bot_worker_runtime", None)
    monkeypatch.setattr(main, "BotWorkerRuntime", lambda **kwargs: worker)
    monkeypatch.setattr(main, "_order_book_registry", SimpleNamespace(close=close_book))
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=lambda: calls.append("db_disposed")))
    monkeypatch.setattr(main, "_trade_import_preview_cleanup_loop", cleanup_loop)
    for name in ("_validate_runtime_security_configuration", "guard_against_local_database_url",
                 "log_runtime_connection_targets", "init_db", "_run_trade_import_preview_cleanup",
                 "_start_streaming_runtime_if_enabled"):
        monkeypatch.setattr(main, name, lambda: None)
    monkeypatch.setattr(main, "_stop_streaming_runtime", lambda: calls.append("stream_closed"))

    async def exercise():
        async with main.app_lifespan(main.app):
            await asyncio.sleep(0)

    if isinstance(stop_result, Exception):
        with pytest.raises(RuntimeError, match="worker failure"):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert "cleanup_cancelled" in calls
    assert "book_closed" in calls
    assert "stream_closed" in calls
    if stop_result is True:
        assert main._bot_worker_runtime is None
        assert "db_disposed" in calls
    else:
        assert main._bot_worker_runtime is worker
        assert "db_disposed" not in calls
        with pytest.raises(RuntimeError, match="previous_bot_worker_shutdown_incomplete"):
            asyncio.run(exercise())
