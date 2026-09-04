import io
import traceback
from urllib import error

import pytest

from app.services import journal_storage as storage
from app.services.projectx_credentials import ProjectXCredentials


@pytest.mark.parametrize("action", ["save", "load", "delete"])
@pytest.mark.parametrize("transport", [False, True])
def test_storage_errors_do_not_expose_provider_body_or_transport_secrets(monkeypatch, action, transport):
    monkeypatch.setenv("JOURNAL_IMAGE_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "images")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "private-service-key")

    def fail(*args, **kwargs):
        if transport:
            raise error.URLError("private-service-key")
        raise error.HTTPError("https://example.invalid/?token=private-service-key", 502, "private-service-key", {}, io.BytesIO(b"private-service-key"))

    monkeypatch.setattr(storage, "open_credentialed_request", fail)
    with pytest.raises(RuntimeError) as raised:
        if action == "save":
            storage.save_journal_image(object_key="user/image.png", file_bytes=b"image", mime_type="image/png")
        elif action == "load":
            storage.load_journal_image(object_key="user/image.png")
        else:
            storage.delete_journal_image(object_key="user/image.png")
    assert "private-service-key" not in str(raised.value)
    # The exception traceback from the transport is intentionally suppressed.
    rendered = "".join(traceback.format_exception_only(raised.type, raised.value))
    assert "private-service-key" not in rendered
    assert raised.value.__suppress_context__ is True


def test_invalid_storage_backend_never_falls_back_to_another_location(monkeypatch):
    monkeypatch.setenv("JOURNAL_IMAGE_STORAGE_BACKEND", "supabsae")
    with pytest.raises(RuntimeError, match="invalid_journal_image_storage_backend"):
        storage.journal_storage_backend()


def test_provider_credential_repr_does_not_reveal_values():
    credentials = ProjectXCredentials("private-username", "private-api-key")
    assert "private" not in repr(credentials)
    assert credentials.api_key == "private-api-key"


@pytest.mark.parametrize("environment,allowed", [("production", False), ("", False), ("development", True), ("test", True)])
def test_local_storage_http_requires_explicit_development_environment(monkeypatch, environment, allowed):
    monkeypatch.setenv("TOPSIGNAL_ENV", environment)
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    if allowed:
        assert storage._supabase_url() == "http://127.0.0.1:54321"
    else:
        with pytest.raises(RuntimeError, match="requires_tls"):
            storage._supabase_url()


def test_storage_image_download_is_bounded_and_response_closed(monkeypatch):
    monkeypatch.setenv("JOURNAL_IMAGE_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "images")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "private-service-key")
    body = io.BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    monkeypatch.setattr(storage, "open_credentialed_request", lambda *_a, **_k: body)
    with pytest.raises(RuntimeError, match="download_network_error"):
        storage.load_journal_image(object_key="user/image.png")
    assert body.closed
