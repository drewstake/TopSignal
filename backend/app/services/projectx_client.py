from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Iterator
from urllib import error, parse, request


@dataclass
class _TokenCache:
    token: str
    expires_at: datetime


_TOKEN_LOCK = Lock()
_TOKEN_CACHE: _TokenCache | None = None
_TOKEN_SAFETY_WINDOW = timedelta(seconds=60)


class ProjectXClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProjectXClient:
    """Thin HTTP wrapper around documented ProjectX Gateway endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        api_key: str,
        timeout_seconds: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "ProjectXClient":
        base_url = _first_env(
            "PROJECTX_API_BASE_URL",
            "PROJECTX_BASE_URL",
            "PROJECTX_GATEWAY_URL",
            "TOPSTEP_API_BASE_URL",
            "TOPSTEPX_API_BASE_URL",
        )
        username = _first_env(
            "PROJECTX_USERNAME",
            "PROJECTX_USER_NAME",
            "TOPSTEP_USERNAME",
            "TOPSTEPX_USERNAME",
        )
        api_key = _first_env(
            "PROJECTX_API_KEY",
            "TOPSTEP_API_KEY",
            "TOPSTEPX_API_KEY",
            "PX_API_KEY",
        )

        missing: list[str] = []
        if not base_url:
            missing.append("PROJECTX_API_BASE_URL")
        if not username:
            missing.append("PROJECTX_USERNAME")
        if not api_key:
            missing.append("PROJECTX_API_KEY")

        if missing:
            joined = ", ".join(missing)
            raise ProjectXClientError(
                f"Missing ProjectX configuration in environment: {joined}.",
            )

        return cls(base_url=base_url, username=username, api_key=api_key)

    def list_accounts(self) -> list[dict[str, Any]]:
        payload = {"onlyActiveAccounts": True}
        data = self._request("POST", "/api/Account/search", payload=payload, with_auth=True)

        rows = _unwrap_list(data, preferred_keys=["accounts", "data", "items"])
        output: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            # Keep this defensive filter even when onlyActiveAccounts=true.
            if row.get("canTrade") is False:
                continue

            account_id_raw = _first_value(row, ["id", "accountId", "account_id"])
            if account_id_raw is None:
                continue

            account_id = _safe_int(account_id_raw)
            if account_id is None:
                continue

            status_raw = _first_value(row, ["status", "state", "accountStatus"])
            can_trade = row.get("canTrade")
            status = str(status_raw) if status_raw is not None else ("ACTIVE" if can_trade else "UNKNOWN")

            output.append(
                {
                    "id": account_id,
                    "name": str(
                        _first_value(row, ["name", "accountName", "displayName"]) or f"Account {account_id}"
                    ),
                    "balance": _safe_float(
                        _first_value(
                            row,
                            [
                                "balance",
                                "cashBalance",
                                "netLiquidatingValue",
                                "equity",
                                "availableBalance",
                            ],
                        )
                    ),
                    "status": status,
                }
            )

        output.sort(key=lambda account: account["id"])
        return output

    def fetch_trade_history(
        self,
        account_id: int,
        start: datetime,
        end: datetime | None = None,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        start_utc = _as_utc(start)
        end_utc = _as_utc(end) if end is not None else None

        payload: dict[str, Any] = {
            "accountId": int(account_id),
            "startTimestamp": _iso_utc(start_utc),
        }
        if end_utc is not None:
            payload["endTimestamp"] = _iso_utc(end_utc)
        if limit is not None:
            payload["limit"] = max(1, int(limit))
        if offset is not None:
            payload["offset"] = max(0, int(offset))

        data = self._request("POST", "/api/Trade/search", payload=payload, with_auth=True)

        rows = _unwrap_list(data, preferred_keys=["trades", "data", "items"])
        normalized: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            if _is_truthy(_first_value(row, ["voided", "isVoided", "is_voided"])):
                # Voided/canceled executions should not affect local trade history or PnL.
                continue

            timestamp = _parse_datetime(
                _first_value(row, ["creationTimestamp", "timestamp", "createdAt", "updatedAt"])
            )
            if timestamp is None:
                continue

            row_account = _safe_int(_first_value(row, ["accountId", "account_id"]))
            contract_id = _first_value(row, ["contractId", "contract_id", "symbolId", "symbol"])
            symbol = _first_value(row, ["symbol", "symbolId", "contractSymbol", "contractId"])
            order_id = _first_value(row, ["orderId", "order_id"])
            source_trade_id = _first_value(row, ["id", "tradeId", "executionId"])
            pnl_raw = _first_value(row, ["profitAndLoss", "pnl", "realizedPnl"])

            order_id_text = _string_or_none(order_id)
            source_trade_id_text = _string_or_none(source_trade_id)
            if not order_id_text:
                # Keep dedupe stable even if orderId is omitted.
                order_id_text = source_trade_id_text or f"fallback-{int(timestamp.timestamp() * 1000)}"

            contract_id_text = _string_or_none(contract_id) or "UNKNOWN"
            symbol_text = _string_or_none(symbol) or contract_id_text

            normalized.append(
                {
                    "account_id": row_account if row_account is not None else int(account_id),
                    "contract_id": contract_id_text,
                    "symbol": symbol_text,
                    "side": _normalize_side(_first_value(row, ["side", "direction", "positionSide"])),
                    "size": _safe_float(_first_value(row, ["size", "quantity", "qty"])),
                    "price": _safe_float(_first_value(row, ["price", "fillPrice", "averagePrice"])),
                    "timestamp": timestamp,
                    "fees": _safe_float(_first_value(row, ["fees", "commission", "totalFees"])),
                    "pnl": _safe_float(pnl_raw) if pnl_raw is not None else None,
                    "order_id": order_id_text,
                    "source_trade_id": source_trade_id_text,
                    "status": _string_or_none(_first_value(row, ["status", "tradeStatus", "state"])),
                    "raw_payload": row,
                }
            )

        normalized.sort(key=lambda trade: trade["timestamp"])
        return normalized

    def stream_user_trades(
        self,
        account_id: int,
        *,
        start: datetime | None = None,
        poll_interval_seconds: int = 5,
    ) -> Iterator[dict[str, Any]]:
        """
        Poll-based stream interface for user trade events.

        This keeps a stream-like API surface without requiring SignalR client setup.
        """

        poll_seconds = max(1, poll_interval_seconds)
        watermark = _as_utc(start) if start else datetime.now(timezone.utc) - timedelta(minutes=15)
        seen_order_ids_at_watermark: set[str] = set()

        while True:
            events = self.fetch_trade_history(account_id=account_id, start=watermark - timedelta(seconds=1))

            for event in events:
                event_timestamp = event["timestamp"]
                event_order_id = event["order_id"]

                if event_timestamp < watermark:
                    continue
                if event_timestamp == watermark and event_order_id in seen_order_ids_at_watermark:
                    continue

                yield event

                if event_timestamp > watermark:
                    watermark = event_timestamp
                    seen_order_ids_at_watermark = {event_order_id}
                else:
                    seen_order_ids_at_watermark.add(event_order_id)

            time.sleep(poll_seconds)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        with_auth: bool,
    ) -> Any:
        try:
            return self._request_once(method, path, payload=payload, with_auth=with_auth)
        except ProjectXClientError as exc:
            if with_auth and exc.status_code == 401:
                _clear_token_cache()
                return self._request_once(method, path, payload=payload, with_auth=with_auth)
            raise

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        with_auth: bool,
    ) -> Any:
        url = parse.urljoin(f"{self.base_url}/", path.lstrip("/"))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if with_auth:
            headers["Authorization"] = f"Bearer {self._get_access_token()}"

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(url=url, data=body, headers=headers, method=method.upper())

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            detail = _extract_error_message(raw_error) or str(exc.reason)
            raise ProjectXClientError(
                f"ProjectX request failed ({exc.code}): {detail}",
                status_code=exc.code,
            ) from exc
        except error.URLError as exc:
            raise ProjectXClientError(f"ProjectX network error: {exc.reason}") from exc

        if raw.strip() == "":
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectXClientError("ProjectX returned a non-JSON response.") from exc

        if isinstance(parsed, dict) and parsed.get("success") is False:
            message = _extract_error_message(parsed)
            raise ProjectXClientError(f"ProjectX error: {message}")

        return parsed

    def _get_access_token(self) -> str:
        global _TOKEN_CACHE

        now = datetime.now(timezone.utc)
        with _TOKEN_LOCK:
            if _TOKEN_CACHE and (_TOKEN_CACHE.expires_at - _TOKEN_SAFETY_WINDOW) > now:
                return _TOKEN_CACHE.token

        payload = {
            "userName": self.username,
            "apiKey": self.api_key,
        }
        data = self._request_once("POST", "/api/Auth/loginKey", payload=payload, with_auth=False)
        if not isinstance(data, dict):
            raise ProjectXClientError("ProjectX auth response format was invalid.")

        token = _string_or_none(_first_value(data, ["token", "accessToken", "jwt", "jwtToken"]))
        if not token:
            raise ProjectXClientError("ProjectX auth succeeded but no token was returned.")

        expires_at = _parse_token_expiry(data)

        with _TOKEN_LOCK:
            _TOKEN_CACHE = _TokenCache(token=token, expires_at=expires_at)

        return token


def _clear_token_cache() -> None:
    global _TOKEN_CACHE
    with _TOKEN_LOCK:
        _TOKEN_CACHE = None


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _first_value(payload: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _unwrap_list(payload: Any, preferred_keys: list[str]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1_000_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if raw == "":
            return None
        candidate = _normalize_iso_datetime(raw)
        try:
            return _as_utc(datetime.fromisoformat(candidate))
        except ValueError:
            return None
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _normalize_side(raw_side: Any) -> str:
    if isinstance(raw_side, str):
        text = raw_side.strip().upper()
        if text in {"BUY", "LONG", "BID"}:
            return "BUY"
        if text in {"SELL", "SHORT", "ASK"}:
            return "SELL"
        return "UNKNOWN"

    if isinstance(raw_side, (int, float)):
        numeric = int(raw_side)
        if numeric == 0:
            return "BUY"
        if numeric == 1:
            return "SELL"

    return "UNKNOWN"


def _parse_token_expiry(payload: dict[str, Any]) -> datetime:
    now = datetime.now(timezone.utc)
    raw_expiry = _first_value(
        payload,
        [
            "expiration",
            "expiresAt",
            "expires",
            "expiry",
            "expiresIn",
            "expiresInSeconds",
        ],
    )

    if raw_expiry is None:
        return now + timedelta(minutes=20)

    if isinstance(raw_expiry, (int, float)):
        numeric = float(raw_expiry)
        # Epoch milliseconds
        if numeric > 1_000_000_000_000:
            return datetime.fromtimestamp(numeric / 1000.0, tz=timezone.utc)
        # Epoch seconds
        if numeric > 1_000_000_000:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        # Relative seconds
        return now + timedelta(seconds=max(0, int(numeric)))

    parsed = _parse_datetime(raw_expiry)
    if parsed is not None:
        return parsed

    return now + timedelta(minutes=20)


def _extract_error_message(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ["detail", "errorMessage", "message", "title", "error", "errors"]:
            value = raw.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return "; ".join(str(item) for item in value)
            return str(value)
        return "Unknown error"

    if isinstance(raw, str):
        text = raw.strip()
        if text == "":
            return "Unknown error"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return _extract_error_message(parsed)

    return "Unknown error"


def _normalize_iso_datetime(raw: str) -> str:
    """
    Normalize common ProjectX timestamp variants into an ISO string accepted by
    datetime.fromisoformat.
    """

    text = raw.replace("Z", "+00:00")

    # Handles timestamps like `2026-02-05T19:49:57.22185+00:00` where
    # fractional precision may vary and can fail strict parsing.
    match = re.match(
        r"^(?P<prefix>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
        r"(?:\.(?P<fraction>\d+))?"
        r"(?P<offset>[+-]\d{2}:?\d{2})?$",
        text,
    )
    if not match:
        return text

    prefix = match.group("prefix")
    fraction = match.group("fraction") or ""
    offset = match.group("offset") or ""

    normalized = prefix
    if fraction:
        normalized += f".{(fraction + '000000')[:6]}"

    if offset:
        if len(offset) == 5:  # +HHMM
            offset = f"{offset[:3]}:{offset[3:]}"
        normalized += offset

    return normalized
