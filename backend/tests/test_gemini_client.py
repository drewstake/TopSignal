import json
from urllib import error

import pytest

from app.services.gemini_client import DEFAULT_GEMINI_MODEL, GeminiClient, GeminiClientError


def test_from_env_uses_flash_lite_default(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    client = GeminiClient.from_env()

    assert client.api_key == "test-key"
    assert client.model == DEFAULT_GEMINI_MODEL


def test_from_env_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)

    with pytest.raises(GeminiClientError) as exc_info:
        GeminiClient.from_env()

    assert str(exc_info.value) == "Missing Gemini configuration in environment: GEMINI_API_KEY."


def test_generate_content_posts_to_configured_model(monkeypatch):
    captured = {}

    class StubResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return StubResponse()

    monkeypatch.setattr("app.services.gemini_client.request.urlopen", fake_urlopen)

    client = GeminiClient(api_key="test-key", model="gemini-3.1-flash-lite", timeout_seconds=7)
    data = client.generate_content(
        "Explain AI briefly",
        system_instruction="Be concise.",
        generation_config={"temperature": 0.2},
    )

    assert data["candidates"][0]["content"]["parts"][0]["text"] == "ok"
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.1-flash-lite:generateContent"
    )
    assert captured["timeout"] == 7
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["headers"]["X-goog-api-key"] == "test-key"
    assert captured["payload"] == {
        "contents": [{"role": "user", "parts": [{"text": "Explain AI briefly"}]}],
        "systemInstruction": {"parts": [{"text": "Be concise."}]},
        "generationConfig": {"temperature": 0.2},
    }


def test_generate_text_extracts_candidate_parts(monkeypatch):
    class StubClient(GeminiClient):
        def generate_content(self, *_args, **_kwargs):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "Hello"}, {"text": " world"}]}},
                    {"content": {"parts": [{"text": "!"}]}},
                ]
            }

    client = StubClient(api_key="test-key")

    assert client.generate_text("Say hi") == "Hello world!"


def test_http_error_message_is_actionable(monkeypatch):
    calls = 0

    def fake_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise error.HTTPError(
            url="https://example.test",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=_BytesReader(b'{"error": {"message": "API key not valid."}}'),
        )

    monkeypatch.setattr("app.services.gemini_client.request.urlopen", fake_urlopen)

    client = GeminiClient(api_key="bad-key")
    with pytest.raises(GeminiClientError) as exc_info:
        client.generate_content("Hello")

    assert calls == 1
    assert exc_info.value.status_code == 400
    assert str(exc_info.value) == "Gemini request failed: API key not valid."


def test_retryable_http_error_is_retried(monkeypatch):
    calls = 0

    class StubResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'

    def fake_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error.HTTPError(
                url="https://example.test",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=_BytesReader(
                    b'{"error": {"message": "This model is currently experiencing high demand."}}'
                ),
            )
        return StubResponse()

    monkeypatch.setattr("app.services.gemini_client.request.urlopen", fake_urlopen)

    client = GeminiClient(api_key="test-key", retry_attempts=2, retry_backoff_seconds=0)

    assert client.generate_text("Hello") == "ok"
    assert calls == 2


class _BytesReader:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body
