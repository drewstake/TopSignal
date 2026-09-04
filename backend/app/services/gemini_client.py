from __future__ import annotations

import json
import math
import os
from time import sleep
from typing import Any, Mapping
from urllib import error, parse, request

from app.production_logging import redact
from .credentialed_http import (
    CredentialedHttpError,
    open_credentialed_request,
    read_bounded,
    validate_credentialed_url,
    validate_timeout,
)


DEFAULT_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GEMINI_TIMEOUT_SECONDS = 30
DEFAULT_GEMINI_RETRY_ATTEMPTS = 3
DEFAULT_GEMINI_RETRY_BACKOFF_SECONDS = 0.75
_RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GeminiClient:
    """Small Gemini API wrapper kept ready for future backend AI features."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        base_url: str = DEFAULT_GEMINI_API_BASE_URL,
        timeout_seconds: int = DEFAULT_GEMINI_TIMEOUT_SECONDS,
        retry_attempts: int = DEFAULT_GEMINI_RETRY_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_GEMINI_RETRY_BACKOFF_SECONDS,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        try:
            validate_credentialed_url(self.base_url)
            self.timeout_seconds = validate_timeout(timeout_seconds)
            if isinstance(retry_attempts, bool) or not isinstance(retry_attempts, int) or not 1 <= retry_attempts <= 5:
                raise ValueError("retry attempts")
            if not math.isfinite(retry_backoff_seconds) or not 0 <= retry_backoff_seconds <= 30:
                raise ValueError("retry backoff")
        except (CredentialedHttpError, TypeError, ValueError):
            raise GeminiClientError("Invalid Gemini transport configuration: require HTTPS, finite timeout <=120s, 1-5 attempts, and backoff <=30s.") from None
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    @classmethod
    def from_env(cls) -> "GeminiClient":
        api_key = _first_env("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")
        if not api_key:
            raise GeminiClientError("Missing Gemini configuration in environment: GEMINI_API_KEY.")

        model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        base_url = os.getenv("GEMINI_API_BASE_URL", DEFAULT_GEMINI_API_BASE_URL).strip() or DEFAULT_GEMINI_API_BASE_URL
        timeout_seconds = _read_int_env("GEMINI_TIMEOUT_SECONDS", DEFAULT_GEMINI_TIMEOUT_SECONDS)
        retry_attempts = _read_int_env("GEMINI_RETRY_ATTEMPTS", DEFAULT_GEMINI_RETRY_ATTEMPTS)
        retry_backoff_seconds = _read_float_env(
            "GEMINI_RETRY_BACKOFF_SECONDS",
            DEFAULT_GEMINI_RETRY_BACKOFF_SECONDS,
        )
        return cls(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    def generate_content(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        generation_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if generation_config:
            payload["generationConfig"] = dict(generation_config)

        return self._request("generateContent", payload)

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        generation_config: Mapping[str, Any] | None = None,
    ) -> str:
        data = self.generate_content(
            prompt,
            system_instruction=system_instruction,
            generation_config=generation_config,
        )
        return _extract_text(data)

    def _request(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        model = parse.quote(self.model, safe="")
        url = f"{self.base_url}/models/{model}:{action}"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": self.api_key,
            },
        )

        raw = ""
        for attempt_index in range(self.retry_attempts):
            try:
                with open_credentialed_request(req, timeout=self.timeout_seconds) as response:
                    raw = read_bounded(response, max_bytes=4 * 1024 * 1024, timeout=self.timeout_seconds).decode("utf-8")
                break
            except TimeoutError:
                raise GeminiClientError("Gemini request timed out.", status_code=504) from None
            except error.HTTPError as exc:
                message = _read_http_error(exc, api_key=self.api_key)
                if _should_retry_http_error(exc.code) and attempt_index < self.retry_attempts - 1:
                    _sleep_before_retry(attempt_index, self.retry_backoff_seconds)
                    continue
                raise GeminiClientError(message, status_code=exc.code) from None
            except error.URLError as exc:
                if isinstance(exc.reason, TimeoutError):
                    raise GeminiClientError("Gemini request timed out.", status_code=504) from None
                raise GeminiClientError("Gemini request failed: transport unavailable.", status_code=502) from None
            except (CredentialedHttpError, UnicodeDecodeError, OSError):
                raise GeminiClientError("Gemini request failed: invalid or unavailable transport response.", status_code=502) from None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GeminiClientError("Gemini returned an invalid JSON response.", status_code=502) from exc

        if not isinstance(parsed, dict):
            raise GeminiClientError("Gemini returned an unexpected response shape.", status_code=502)
        return parsed


def _extract_text(data: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for candidate in data.get("candidates", []):
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("content")
        if not isinstance(content, Mapping):
            continue
        for part in content.get("parts", []):
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def _read_http_error(exc: error.HTTPError, *, api_key: str = "") -> str:
    try:
        raw = read_bounded(exc, max_bytes=16384, timeout=5).decode("utf-8", errors="replace")
    except (CredentialedHttpError, OSError):
        return f"Gemini request failed with HTTP {exc.code}."
    finally:
        exc.close()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return f"Gemini request failed with HTTP {exc.code}."

    message = None
    if isinstance(payload, Mapping):
        error_payload = payload.get("error")
        if isinstance(error_payload, Mapping):
            message = error_payload.get("message")
    if isinstance(message, str) and message.strip():
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        return f"Gemini request failed: {redact(message.strip())[:500]}"
    return f"Gemini request failed with HTTP {exc.code}."


def _should_retry_http_error(status_code: int) -> bool:
    return status_code in _RETRYABLE_HTTP_STATUS_CODES


def _sleep_before_retry(attempt_index: int, backoff_seconds: float) -> None:
    if backoff_seconds <= 0:
        return
    sleep(min(30, backoff_seconds * (2**attempt_index)))


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default
