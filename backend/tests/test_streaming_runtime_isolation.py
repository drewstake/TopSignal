import logging
import os
import threading

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
import app.services.projectx_streaming_runtime as runtime_module
from app.services.projectx_streaming_runtime import StreamingRuntime, create_streaming_runtime


def test_process_global_streaming_flag_fails_closed(monkeypatch, caplog):
    monkeypatch.setenv("PROJECTX_STREAMING_ENABLED", "true")
    monkeypatch.setattr(main_module, "_streaming_runtime", None)
    monkeypatch.setattr(
        runtime_module,
        "create_streaming_runtime",
        lambda *args, **kwargs: pytest.fail(
            f"process-global runtime must not be created: args={args!r} kwargs={kwargs!r}"
        ),
    )
    caplog.set_level(logging.WARNING, logger=main_module.logger.name)

    main_module._start_streaming_runtime_if_enabled()

    assert main_module._streaming_runtime is None
    assert "projectx_process_global_streaming_disabled" in caplog.text


def test_streaming_runtime_factory_cannot_be_called_without_tenant_scope():
    with pytest.raises(TypeError):
        create_streaming_runtime()  # type: ignore[call-arg]


def test_timed_out_stream_stop_keeps_handles_until_the_thread_can_be_reaped(caplog):
    class ControlledThread:
        alive = True

        def join(self, timeout=None):
            del timeout

        def is_alive(self):
            return self.alive

    class Loop:
        def call_soon_threadsafe(self, callback):
            callback()

    thread = ControlledThread()
    loop = Loop()
    stop_event = threading.Event()
    runtime = StreamingRuntime(tracker=object(), runner=object())  # type: ignore[arg-type]
    runtime.thread = thread  # type: ignore[assignment]
    runtime.loop = loop  # type: ignore[assignment]
    runtime.stop_event = stop_event
    caplog.set_level(logging.ERROR, logger=runtime_module.logger.name)

    assert runtime.stop(timeout_seconds=0.01) is False
    assert stop_event.is_set()
    assert runtime.thread is thread
    assert runtime.loop is loop
    assert runtime.stop_event is stop_event
    assert "projectx_streaming_runtime_stop_timed_out" in caplog.text

    thread.alive = False
    assert runtime.stop(timeout_seconds=0.01) is True
    assert runtime.thread is None
    assert runtime.loop is None
    assert runtime.stop_event is None
