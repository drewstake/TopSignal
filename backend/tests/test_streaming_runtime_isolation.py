import logging
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
import app.services.projectx_streaming_runtime as runtime_module
from app.services.projectx_streaming_runtime import create_streaming_runtime


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
