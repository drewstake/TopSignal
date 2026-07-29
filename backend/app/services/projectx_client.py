from __future__ import annotations

import hashlib
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
_TOKEN_CACHE_BY_KEY: dict[str, _TokenCache] = {}
_TOKEN_SAFETY_WINDOW = timedelta(seconds=60)
_PROJECTX_ORDER_TYPES = {1, 2, 4, 5, 6, 7}
_PROJECTX_ORDER_SIDES = {0, 1}
_PROJECTX_POSITION_LONG = 1
_PROJECTX_POSITION_SHORT = 2
_MAX_PROJECTX_ORDER_SIZE = 10_000
_PROJECTX_INTRADAY_UNIT_SECONDS = {1: 1, 2: 60, 3: 60 * 60}
_PARTIAL_BAR_KEYS = ("isPartial", "is_partial", "partial")
PROJECTX_ERROR_AUTH_FAILED = "projectx_auth_failed"
PROJECTX_ERROR_CONFIGURATION = "projectx_configuration_error"
PROJECTX_ERROR_NETWORK = "projectx_network_error"
PROJECTX_ERROR_PROVIDER_RESPONSE = "projectx_provider_error"
_PROJECTX_ERROR_REASON_CODES = {
    PROJECTX_ERROR_AUTH_FAILED,
    PROJECTX_ERROR_CONFIGURATION,
    PROJECTX_ERROR_NETWORK,
    PROJECTX_ERROR_PROVIDER_RESPONSE,
}


class ProjectXClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        submission_outcome_unknown: bool = False,
        reason_code: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.submission_outcome_unknown = bool(submission_outcome_unknown)
        self.reason_code = reason_code


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
                reason_code=PROJECTX_ERROR_CONFIGURATION,
            )

        return cls(base_url=base_url, username=username, api_key=api_key)

    def list_accounts(self, *, only_active_accounts: bool = True) -> list[dict[str, Any]]:
        payload = {"onlyActiveAccounts": bool(only_active_accounts)}
        data = self._request("POST", "/api/Account/search", payload=payload, with_auth=True)

        rows = _require_list(data, key="accounts", endpoint="Account/search")
        output: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProjectXClientError("ProjectX Account/search returned an invalid account row.")

            account_id_raw = _first_value(row, ["id", "accountId", "account_id"])
            if account_id_raw is None:
                raise ProjectXClientError("ProjectX Account/search returned an account without an ID.")

            account_id = _safe_int(account_id_raw)
            if account_id is None:
                raise ProjectXClientError("ProjectX Account/search returned an invalid account ID.")

            can_trade = _safe_bool(_first_value(row, ["canTrade", "can_trade"]))
            if can_trade is None:
                raise ProjectXClientError("ProjectX Account/search omitted authoritative account tradability.")
            is_visible = _safe_bool(_first_value(row, ["isVisible", "is_visible"]))
            if is_visible is None:
                raise ProjectXClientError("ProjectX Account/search omitted authoritative account visibility.")
            status = _account_status_from_flags(can_trade=can_trade, is_visible=is_visible)
            balance = _safe_float(
                _first_value(
                    row,
                    ["balance", "cashBalance", "netLiquidatingValue", "equity", "availableBalance"],
                ),
                default=None,
            )
            if balance is not None and not _is_finite_number(balance):
                balance = None

            # Keep this defensive filter for active-only requests.
            if only_active_accounts and status != "ACTIVE":
                continue

            output.append(
                {
                    "id": account_id,
                    "name": str(
                        _first_value(row, ["name", "accountName", "displayName"]) or f"Account {account_id}"
                    ),
                    "balance": balance,
                    "status": status,
                    "can_trade": can_trade,
                    "is_visible": is_visible,
                }
            )

        output.sort(key=lambda account: account["id"])
        return output

    def search_contracts(self, *, search_text: str, live: bool = False) -> list[dict[str, Any]]:
        payload = {
            "searchText": str(search_text).strip(),
            "live": bool(live),
        }
        data = self._request("POST", "/api/Contract/search", payload=payload, with_auth=True)
        return _normalize_contract_rows(data)

    def list_available_contracts(self, *, live: bool = False) -> list[dict[str, Any]]:
        payload = {"live": bool(live)}
        data = self._request("POST", "/api/Contract/available", payload=payload, with_auth=True)
        return _normalize_contract_rows(data)

    def retrieve_bars(
        self,
        *,
        contract_id: str,
        live: bool,
        start: datetime,
        end: datetime,
        unit: int,
        unit_number: int,
        limit: int,
        include_partial_bar: bool = False,
    ) -> list[dict[str, Any]]:
        payload = {
            "contractId": str(contract_id),
            "live": bool(live),
            "startTime": _iso_utc(start),
            "endTime": _iso_utc(end),
            "unit": int(unit),
            "unitNumber": max(1, int(unit_number)),
            "limit": max(1, min(int(limit), 20_000)),
            "includePartialBar": bool(include_partial_bar),
        }
        data = self._request("POST", "/api/History/retrieveBars", payload=payload, with_auth=True)
        rows = _unwrap_list(data, preferred_keys=["bars", "data", "items"])
        output: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            timestamp = _parse_datetime(_first_value(row, ["t", "timestamp", "time"]))
            if timestamp is None:
                continue
            has_partial_marker = any(key in row for key in _PARTIAL_BAR_KEYS)
            is_partial = _is_truthy(_first_value(row, list(_PARTIAL_BAR_KEYS)))
            interval_seconds = _PROJECTX_INTRADAY_UNIT_SECONDS.get(int(unit))
            if not has_partial_marker and interval_seconds is not None:
                is_partial = timestamp + timedelta(
                    seconds=interval_seconds * max(1, int(unit_number))
                ) > _as_utc(end)
            if is_partial and not include_partial_bar:
                continue

            output.append(
                {
                    "timestamp": timestamp,
                    "open": _safe_float(_first_value(row, ["o", "open"])),
                    "high": _safe_float(_first_value(row, ["h", "high"])),
                    "low": _safe_float(_first_value(row, ["l", "low"])),
                    "close": _safe_float(_first_value(row, ["c", "close"])),
                    "volume": _safe_float(_first_value(row, ["v", "volume"])),
                    "is_partial": is_partial,
                    "raw_payload": row,
                }
            )

        output.sort(key=lambda bar: bar["timestamp"])
        return output

    def place_order(
        self,
        *,
        account_id: int,
        contract_id: str,
        order_type: int,
        side: int,
        size: int,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_price: float | None = None,
        custom_tag: str | None = None,
        stop_loss_bracket: dict[str, Any] | None = None,
        take_profit_bracket: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validated_order_type = _validate_projectx_order_type(order_type)
        validated_side = _validate_projectx_order_side(side)
        validated_size = _validate_projectx_order_size(size)
        payload: dict[str, Any] = {
            "accountId": int(account_id),
            "contractId": str(contract_id),
            "type": validated_order_type,
            "side": validated_side,
            "size": validated_size,
        }
        if limit_price is not None:
            payload["limitPrice"] = float(limit_price)
        if stop_price is not None:
            payload["stopPrice"] = float(stop_price)
        if trail_price is not None:
            payload["trailPrice"] = float(trail_price)
        if custom_tag is not None:
            payload["customTag"] = str(custom_tag)
        if stop_loss_bracket is not None:
            payload["stopLossBracket"] = stop_loss_bracket
        if take_profit_bracket is not None:
            payload["takeProfitBracket"] = take_profit_bracket

        data = self._request("POST", "/api/Order/place", payload=payload, with_auth=True)
        order_id = _string_or_none(_first_value(data, ["orderId", "id"])) if isinstance(data, dict) else None
        return {
            "order_id": order_id,
            "raw_payload": data,
            "request_payload": payload,
        }

    def search_open_positions(self, *, account_id: int) -> list[dict[str, Any]]:
        """Return the provider's current open positions for an account.

        An absent or malformed ``positions`` collection is an error rather than
        an empty account. Live risk checks must never turn an unknown provider
        response into a flat position.
        """

        payload = {"accountId": int(account_id)}
        data = self._request("POST", "/api/Position/searchOpen", payload=payload, with_auth=True)
        rows = _require_list(data, key="positions", endpoint="Position/searchOpen")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProjectXClientError("ProjectX Position/searchOpen returned an invalid position row.")

            row_account_id = _safe_int(_first_value(row, ["accountId", "account_id"]))
            contract_id = _string_or_none(_first_value(row, ["contractId", "contract_id"]))
            position_type = _safe_int(_first_value(row, ["type", "positionType", "position_type"]))
            size = _safe_float(_first_value(row, ["size", "quantity", "qty"]), default=None)
            if row_account_id is None or contract_id is None or position_type not in {
                _PROJECTX_POSITION_LONG,
                _PROJECTX_POSITION_SHORT,
            }:
                raise ProjectXClientError("ProjectX Position/searchOpen returned an incomplete position row.")
            if size is None or not _is_finite_number(size) or size <= 0:
                raise ProjectXClientError("ProjectX Position/searchOpen returned an invalid position size.")

            normalized.append(
                {
                    "id": _string_or_none(_first_value(row, ["id", "positionId", "position_id"])),
                    "account_id": row_account_id,
                    "contract_id": contract_id,
                    "type": position_type,
                    "size": float(size),
                    "signed_size": float(size) if position_type == _PROJECTX_POSITION_LONG else -float(size),
                    "average_price": _safe_float(
                        _first_value(row, ["averagePrice", "average_price"]),
                        default=None,
                    ),
                    "creation_timestamp": _parse_datetime(
                        _first_value(row, ["creationTimestamp", "createdAt", "timestamp"])
                    ),
                    "raw_payload": row,
                }
            )

        return normalized

    def search_orders(
        self,
        *,
        account_id: int,
        start: datetime,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Search provider orders for deterministic-tag reconciliation."""

        payload: dict[str, Any] = {
            "accountId": int(account_id),
            "startTimestamp": _iso_utc(_as_utc(start)),
        }
        if end is not None:
            payload["endTimestamp"] = _iso_utc(_as_utc(end))
        data = self._request("POST", "/api/Order/search", payload=payload, with_auth=True)
        rows = _require_list(data, key="orders", endpoint="Order/search")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProjectXClientError("ProjectX Order/search returned an invalid order row.")
            order_id = _string_or_none(_first_value(row, ["id", "orderId", "order_id"]))
            row_account_id = _safe_int(_first_value(row, ["accountId", "account_id"]))
            if order_id is None or row_account_id is None:
                raise ProjectXClientError("ProjectX Order/search returned an incomplete order row.")
            if row_account_id != int(account_id):
                raise ProjectXClientError("ProjectX Order/search returned an order for the wrong account.")
            normalized.append(
                {
                    "order_id": order_id,
                    "account_id": row_account_id,
                    "contract_id": _string_or_none(_first_value(row, ["contractId", "contract_id"])),
                    "status": _safe_int(_first_value(row, ["status", "orderStatus", "order_status"])),
                    "custom_tag": _string_or_none(_first_value(row, ["customTag", "custom_tag"])),
                    "creation_timestamp": _parse_datetime(
                        _first_value(row, ["creationTimestamp", "createdAt", "timestamp"])
                    ),
                    "update_timestamp": _parse_datetime(
                        _first_value(row, ["updateTimestamp", "updatedAt"])
                    ),
                    "raw_payload": row,
                }
            )
        return normalized

    def search_open_orders(self, *, account_id: int) -> list[dict[str, Any]]:
        """Return working provider orders so live exposure cannot race fills."""

        payload = {"accountId": int(account_id)}
        data = self._request("POST", "/api/Order/searchOpen", payload=payload, with_auth=True)
        rows = _require_list(data, key="orders", endpoint="Order/searchOpen")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProjectXClientError("ProjectX Order/searchOpen returned an invalid order row.")
            order_id = _string_or_none(_first_value(row, ["id", "orderId", "order_id"]))
            row_account_id = _safe_int(_first_value(row, ["accountId", "account_id"]))
            contract_id = _string_or_none(_first_value(row, ["contractId", "contract_id"]))
            status = _safe_int(_first_value(row, ["status", "orderStatus", "order_status"]))
            side = _safe_int(_first_value(row, ["side", "orderSide", "order_side"]))
            size = _safe_float(_first_value(row, ["size", "quantity", "qty"]), default=None)
            if (
                order_id is None
                or row_account_id is None
                or contract_id is None
                or status is None
                or side not in _PROJECTX_ORDER_SIDES
                or size is None
                or not _is_finite_number(size)
                or size <= 0
            ):
                raise ProjectXClientError("ProjectX Order/searchOpen returned an incomplete order row.")
            normalized.append(
                {
                    "order_id": order_id,
                    "account_id": row_account_id,
                    "contract_id": contract_id,
                    "status": status,
                    "side": side,
                    "size": float(size),
                    "signed_size": float(size) if side == 0 else -float(size),
                    "custom_tag": _string_or_none(_first_value(row, ["customTag", "custom_tag"])),
                    "raw_payload": row,
                }
            )
        return normalized

    def fetch_trade_history(
        self,
        account_id: int,
        start: datetime,
        end: datetime | None = None,
        *,
        limit: int | None = None,
        offset: int | None = None,
        require_valid_collection: bool = False,
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

        rows = (
            _require_list(data, key="trades", endpoint="Trade/search")
            if require_valid_collection
            else _unwrap_list(data, preferred_keys=["trades", "data", "items"])
        )
        normalized: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                if require_valid_collection:
                    raise ProjectXClientError("ProjectX Trade/search returned an invalid trade row.")
                continue

            if require_valid_collection:
                account_value = _safe_int(_first_value(row, ["accountId", "account_id"]))
                fees_value = _safe_float(
                    _first_value(row, ["fees", "commission", "totalFees"]),
                    default=None,
                )
                voided_keys = {"voided", "isVoided", "is_voided"}
                if account_value is None or fees_value is None or not _is_finite_number(fees_value):
                    raise ProjectXClientError("ProjectX Trade/search returned incomplete daily P&L data.")
                if not any(key in row for key in voided_keys):
                    raise ProjectXClientError("ProjectX Trade/search omitted the execution void status.")
                pnl_value = _first_value(row, ["profitAndLoss", "pnl", "realizedPnl"])
                if pnl_value is not None and not _is_finite_number(pnl_value):
                    raise ProjectXClientError("ProjectX Trade/search returned invalid daily P&L data.")

            timestamp = _parse_datetime(
                _first_value(row, ["creationTimestamp", "timestamp", "createdAt", "updatedAt"])
            )
            if timestamp is None:
                if require_valid_collection:
                    raise ProjectXClientError("ProjectX Trade/search returned a trade without a valid timestamp.")
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
                    "voided": _is_truthy(_first_value(row, ["voided", "isVoided", "is_voided"])),
                    "raw_payload": row,
                }
            )

        normalized.sort(key=lambda trade: trade["timestamp"])
        return normalized

    def fetch_last_trade_timestamp(self, account_id: int, *, lookback_days: int = 3650) -> datetime | None:
        """
        Return the latest known trade timestamp for an account using provider data.

        This uses a bounded lookback window to avoid unbounded provider scans.
        """
        effective_lookback_days = max(1, int(lookback_days))
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=effective_lookback_days)

        rows = self.fetch_trade_history(
            account_id=account_id,
            start=start_utc,
            end=end_utc,
            limit=1,
        )
        timestamps = [
            _as_utc(timestamp)
            for timestamp in (
                row.get("timestamp")
                for row in rows
                if isinstance(row, dict) and not bool(row.get("voided"))
            )
            if isinstance(timestamp, datetime)
        ]
        if not timestamps:
            return None

        return max(timestamps)

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
                _clear_token_cache(self._token_cache_key())
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
                submission_outcome_unknown=(_is_order_submission_path(path) and 500 <= int(exc.code) <= 599),
                reason_code=_http_error_reason_code(path=path, status_code=int(exc.code)),
            ) from exc
        except TimeoutError as exc:
            raise ProjectXClientError(
                "ProjectX request timed out. Check the ProjectX connection and try again.",
                status_code=504,
                submission_outcome_unknown=_is_order_submission_path(path),
                reason_code=PROJECTX_ERROR_NETWORK,
            ) from exc
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProjectXClientError(
                    "ProjectX request timed out. Check the ProjectX connection and try again.",
                    status_code=504,
                    submission_outcome_unknown=_is_order_submission_path(path),
                    reason_code=PROJECTX_ERROR_NETWORK,
                ) from exc
            raise ProjectXClientError(
                f"ProjectX network error: {exc.reason}",
                status_code=502,
                submission_outcome_unknown=_is_order_submission_path(path),
                reason_code=PROJECTX_ERROR_NETWORK,
            ) from exc

        if raw.strip() == "":
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectXClientError(
                "ProjectX returned a non-JSON response.",
                status_code=502,
                submission_outcome_unknown=_is_order_submission_path(path),
                reason_code=PROJECTX_ERROR_PROVIDER_RESPONSE,
            ) from exc

        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise ProjectXClientError(
                _format_provider_error(path=path, payload=parsed),
                status_code=502,
                reason_code=(
                    PROJECTX_ERROR_AUTH_FAILED
                    if _is_authentication_path(path)
                    else PROJECTX_ERROR_PROVIDER_RESPONSE
                ),
            )

        return parsed

    def get_access_token(self) -> str:
        return self._get_access_token()

    def _get_access_token(self) -> str:
        cache_key = self._token_cache_key()
        now = datetime.now(timezone.utc)
        with _TOKEN_LOCK:
            cache_entry = _TOKEN_CACHE_BY_KEY.get(cache_key)
            if cache_entry and (cache_entry.expires_at - _TOKEN_SAFETY_WINDOW) > now:
                return cache_entry.token

        payload = {
            "userName": self.username,
            "apiKey": self.api_key,
        }
        data = self._request_once("POST", "/api/Auth/loginKey", payload=payload, with_auth=False)
        if not isinstance(data, dict):
            raise ProjectXClientError(
                "ProjectX auth response format was invalid.",
                status_code=502,
                reason_code=PROJECTX_ERROR_AUTH_FAILED,
            )

        token = _string_or_none(_first_value(data, ["token", "accessToken", "jwt", "jwtToken"]))
        if not token:
            raise ProjectXClientError(
                "ProjectX auth succeeded but no token was returned.",
                status_code=502,
                reason_code=PROJECTX_ERROR_AUTH_FAILED,
            )

        expires_at = _parse_token_expiry(data)

        with _TOKEN_LOCK:
            _TOKEN_CACHE_BY_KEY[cache_key] = _TokenCache(token=token, expires_at=expires_at)

        return token

    def _token_cache_key(self) -> str:
        # Do not retain a username or API key in the process-global cache index.
        # The framed digest still gives each credential tuple an isolated token.
        material = json.dumps(
            [self.base_url, self.username, self.api_key],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


def projectx_error_reason_code(exc: ProjectXClientError) -> str:
    """Return a stable, non-secret error category for logs and API metadata."""

    if exc.reason_code in _PROJECTX_ERROR_REASON_CODES:
        return exc.reason_code
    if exc.status_code in {401, 403}:
        return PROJECTX_ERROR_AUTH_FAILED
    if exc.status_code is not None and int(exc.status_code) >= 500:
        return PROJECTX_ERROR_NETWORK
    return PROJECTX_ERROR_PROVIDER_RESPONSE


def _clear_token_cache(cache_key: str | None = None) -> None:
    with _TOKEN_LOCK:
        if cache_key is None:
            _TOKEN_CACHE_BY_KEY.clear()
            return
        _TOKEN_CACHE_BY_KEY.pop(cache_key, None)


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


def _require_list(payload: Any, *, key: str, endpoint: str) -> list[Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ProjectXClientError(f"ProjectX {endpoint} returned an invalid response collection.")
    return payload[key]


def _is_order_submission_path(path: str) -> bool:
    return path.rstrip("/").lower() == "/api/order/place"


def _is_authentication_path(path: str) -> bool:
    return path.rstrip("/").lower() == "/api/auth/loginkey"


def _http_error_reason_code(*, path: str, status_code: int) -> str:
    if status_code in {401, 403}:
        return PROJECTX_ERROR_AUTH_FAILED
    if _is_authentication_path(path) and 400 <= status_code < 500:
        return PROJECTX_ERROR_AUTH_FAILED
    if status_code >= 500:
        return PROJECTX_ERROR_NETWORK
    return PROJECTX_ERROR_PROVIDER_RESPONSE


def _normalize_contract_rows(payload: Any) -> list[dict[str, Any]]:
    rows = _unwrap_list(payload, preferred_keys=["contracts", "data", "items"])
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        contract_id = _string_or_none(_first_value(row, ["id", "contractId", "contract_id"]))
        if contract_id is None:
            continue

        name = _string_or_none(_first_value(row, ["name", "symbol", "contractSymbol"])) or contract_id
        output.append(
            {
                "id": contract_id,
                "name": name,
                "description": _string_or_none(_first_value(row, ["description", "desc"])),
                "tick_size": _safe_float(_first_value(row, ["tickSize", "tick_size"]), default=None),
                "tick_value": _safe_float(_first_value(row, ["tickValue", "tick_value"]), default=None),
                "active_contract": _safe_bool(_first_value(row, ["activeContract", "active_contract"])),
                "symbol_id": _string_or_none(_first_value(row, ["symbolId", "symbol_id"])),
                "raw_payload": row,
            }
        )

    output.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))
    return output


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
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


def _validate_projectx_order_type(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectXClientError("Unsupported ProjectX order type.") from exc
    if parsed not in _PROJECTX_ORDER_TYPES:
        raise ProjectXClientError("Unsupported ProjectX order type.")
    return parsed


def _validate_projectx_order_side(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectXClientError("Unsupported ProjectX order side.") from exc
    if parsed not in _PROJECTX_ORDER_SIDES:
        raise ProjectXClientError("Unsupported ProjectX order side.")
    return parsed


def _validate_projectx_order_size(value: Any) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProjectXClientError("ProjectX order size must be a positive whole number.") from exc
    if (
        not _is_finite_number(parsed)
        or parsed <= 0
        or parsed > _MAX_PROJECTX_ORDER_SIZE
        or abs(parsed - round(parsed)) > 1e-9
    ):
        raise ProjectXClientError("ProjectX order size must be a positive whole number.")
    return int(round(parsed))


def _is_finite_number(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return numeric == numeric and numeric not in {float("inf"), float("-inf")}


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _account_status_from_flags(*, can_trade: bool | None, is_visible: bool | None) -> str:
    if is_visible is False:
        return "HIDDEN"
    if can_trade is False:
        return "LOCKED_OUT"
    return "ACTIVE"


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
    message = _extract_nested_error_message(raw)
    return message or "Unknown error"


def _format_provider_error(*, path: str, payload: dict[str, Any]) -> str:
    message = _extract_error_message(payload)
    normalized_path = path.split("?", 1)[0].rstrip("/").lower()

    if normalized_path == "/api/auth/loginkey":
        return _format_login_key_error(message=message, payload=payload)

    return f"ProjectX error: {message}"


def _format_login_key_error(*, message: str, payload: dict[str, Any]) -> str:
    prefix = "ProjectX authentication failed"
    error_code = _extract_error_code(payload)
    unhelpful_message = (
        message == "Unknown error" or (error_code is not None and message == f"Error code {error_code}")
    )

    if not unhelpful_message and message:
        return f"{prefix}: {message}"

    if error_code == "3":
        return (
            f"{prefix}. Verify your TopstepX username and API key, and confirm "
            "ProjectX API access is active and your account is linked. "
            "(error code 3)"
        )

    suffix = f" (error code {error_code})" if error_code else ""
    return f"{prefix}. Verify your TopstepX username and API key.{suffix}"


def _extract_error_message_from_mapping(payload: dict[str, Any]) -> str | None:
    validation_message = _format_error_collection(payload.get("errors"))
    if validation_message:
        return validation_message

    for key in ["detail", "errorMessage", "message", "title", "error", "reason", "error_description"]:
        if key not in payload:
            continue
        message = _extract_nested_error_message(payload.get(key))
        if message:
            return message

    for key in ["responseStatus", "response_status", "innerError", "inner_error"]:
        if key not in payload:
            continue
        message = _extract_nested_error_message(payload.get(key))
        if message:
            return message

    error_code = _extract_error_code(payload)
    if error_code:
        return f"Error code {error_code}"

    return None


def _extract_error_code(payload: dict[str, Any]) -> str | None:
    for key in ["errorCode", "error_code", "code", "statusCode", "status_code"]:
        value = payload.get(key)
        if value is None:
            continue
        text = _string_or_none(value)
        if text:
            return text
    return None


def _extract_nested_error_message(raw: Any) -> str | None:
    if isinstance(raw, dict):
        direct_message = _extract_error_message_from_mapping(raw)
        if direct_message:
            return direct_message

        for key, value in raw.items():
            if key in {
                "detail",
                "errorMessage",
                "message",
                "title",
                "error",
                "errors",
                "reason",
                "error_description",
                "responseStatus",
                "response_status",
                "innerError",
                "inner_error",
                "errorCode",
                "error_code",
                "code",
                "statusCode",
                "status_code",
            }:
                continue
            message = _extract_nested_error_message(value)
            if message:
                return message
        return None

    if isinstance(raw, list):
        parts = [_extract_nested_error_message(item) for item in raw]
        return _join_error_messages(parts)

    if isinstance(raw, str):
        text = raw.strip()
        if text == "":
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return _extract_nested_error_message(parsed)

    return None


def _format_error_collection(raw: Any) -> str | None:
    if isinstance(raw, dict):
        parts: list[str | None] = []
        for key, value in raw.items():
            label = _string_or_none(key)
            message = _extract_nested_error_message(value)
            if not message:
                continue
            if label and label not in {"__all__", "non_field_errors"}:
                parts.append(f"{label}: {message}")
            else:
                parts.append(message)
        return _join_error_messages(parts)

    if isinstance(raw, list):
        return _join_error_messages(_extract_nested_error_message(item) for item in raw)

    return _extract_nested_error_message(raw)


def _join_error_messages(parts: list[str | None] | Iterator[str | None]) -> str | None:
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part is None:
            continue
        text = part.strip()
        if text == "" or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        return None
    return "; ".join(cleaned)


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
