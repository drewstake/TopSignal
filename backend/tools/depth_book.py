"""Standalone copy of the validated display-book kernel for read-only research.

Parity with projectx_order_book is enforced by the research tests. No network imports.
"""

from __future__ import annotations

from collections import deque

from dataclasses import dataclass

from datetime import datetime, timezone

from decimal import Decimal, InvalidOperation

import math

from typing import Any, Mapping

_ASK_TYPES = frozenset({1, 3, 10})

_BID_TYPES = frozenset({2, 4, 9})

_RESET_TYPE = 6

_RECENT_FINGERPRINT_LIMIT = 4096

@dataclass(frozen=True)
class _Level:
    price: Decimal
    size: Decimal
    volume: Decimal
    current_volume: Decimal | None
    timestamp: datetime

class MarketByPriceBook:
    """Independent, incrementally maintained ProjectX book for one contract ID."""

    def __init__(self, contract_id: str):
        self.contract_id = _normalize_contract_id(contract_id)
        self._bids: dict[Decimal, _Level] = {}
        self._asks: dict[Decimal, _Level] = {}
        self._sequence = 0
        self._last_timestamp: datetime | None = None
        self._last_reset_timestamp: datetime | None = None
        self._level_timestamps: dict[tuple[str, Decimal], datetime] = {}
        self._explicit_depth_levels: set[tuple[str, Decimal]] = set()
        self._best_quote_prices: dict[str, Decimal] = {}
        self._best_quote_timestamps: dict[str, datetime] = {}
        self._recent_fingerprints: deque[tuple[Any, ...]] = deque()
        self._recent_fingerprint_set: set[tuple[Any, ...]] = set()

    @property
    def sequence(self) -> int:
        return self._sequence

    def apply(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Apply one GatewayDepth entry and return a UI event when state changed."""

        depth_type = _parse_dom_type(payload.get("type"))
        timestamp = _parse_timestamp(payload.get("timestamp"))
        if depth_type is None or timestamp is None:
            return None

        if depth_type == _RESET_TYPE:
            fingerprint = (timestamp, depth_type, None, None, None)
            if self._is_duplicate(fingerprint) and not self._bids and not self._asks:
                return None
            # Reset is an authoritative epoch boundary. Forget pre-reset payload
            # fingerprints so an identical rebuilt level is not mistaken for a
            # duplicate, even when the provider reuses timestamps and values.
            self._recent_fingerprints.clear()
            self._recent_fingerprint_set.clear()
            self._remember_fingerprint(fingerprint)
            self._bids.clear()
            self._asks.clear()
            self._level_timestamps.clear()
            self._explicit_depth_levels.clear()
            self._best_quote_prices.clear()
            self._best_quote_timestamps.clear()
            self._last_reset_timestamp = timestamp
            self._last_timestamp = timestamp
            self._sequence += 1
            return self.snapshot(reset=True)

        side = _side_for_dom_type(depth_type)
        if side is None:
            # Trade, session high/low, fill, and unknown events are not resting levels.
            return None

        price = _parse_decimal(payload.get("price"))
        volume = _parse_nonnegative_decimal(payload.get("volume"))
        if price is None or volume is None:
            return None

        current_volume = _parse_optional_nonnegative_decimal(payload.get("currentVolume"))
        if "currentVolume" in payload and payload.get("currentVolume") is not None and current_volume is None:
            return None

        fingerprint = (timestamp, depth_type, price, volume, current_volume)
        if self._is_duplicate(fingerprint):
            return None

        level_key = (side, price)
        previous_timestamp = self._level_timestamps.get(level_key)
        if self._last_reset_timestamp is not None and timestamp < self._last_reset_timestamp:
            return None
        if previous_timestamp is not None and timestamp < previous_timestamp:
            return None

        is_best_quote = depth_type not in {1, 2}
        best_timestamp = self._best_quote_timestamps.get(side)
        if best_timestamp is not None and timestamp < best_timestamp:
            return None
        levels = self._bids if side == "bid" else self._asks
        if is_best_quote:
            # A best quote proves only its current price. A prior quote is not
            # a resting depth level unless an explicit Ask/Bid also supplied it.
            # The new best price also invalidates any supposedly better levels.
            obsolete = [
                level_price for level_price in levels
                if (side == "bid" and level_price > price)
                or (side == "ask" and level_price < price)
            ] if volume > 0 else []
            if any(levels[level_price].timestamp > timestamp for level_price in obsolete):
                return None
            prior_best = self._best_quote_prices.get(side)
            if prior_best is not None and (side, prior_best) not in self._explicit_depth_levels:
                obsolete.append(prior_best)
            for level_price in obsolete:
                levels.pop(level_price, None)
                self._explicit_depth_levels.discard((side, level_price))
                # The side-wide best timestamp rejects late updates for removed
                # prices, so quote-only feeds need no unbounded tombstone map.
                self._level_timestamps.pop((side, level_price), None)
            self._best_quote_timestamps[side] = timestamp
            if volume > 0:
                self._best_quote_prices[side] = price
            else:
                self._best_quote_prices.pop(side, None)
        elif volume > 0:
            self._explicit_depth_levels.add(level_key)
        else:
            self._explicit_depth_levels.discard(level_key)

        self._remember_fingerprint(fingerprint)
        self._level_timestamps[level_key] = timestamp
        if self._last_timestamp is None or timestamp > self._last_timestamp:
            self._last_timestamp = timestamp

        previous = levels.get(price)
        if volume == 0:
            self._explicit_depth_levels.discard(level_key)
            if previous is None and not is_best_quote:
                return None
            levels.pop(price, None)
        else:
            replacement = _Level(
                price=price,
                size=volume,
                volume=volume,
                current_volume=current_volume,
                timestamp=timestamp,
            )
            if previous == replacement:
                return None
            levels[price] = replacement

        self._sequence += 1
        if is_best_quote:
            # A full snapshot removes obsolete best prices in every client.
            return self.snapshot()
        return {
            "contract_id": self.contract_id,
            "sequence": self._sequence,
            "timestamp": _iso_utc(timestamp),
            "side": side,
            "price": _json_number(price),
            # ProjectX documents `volume` as total volume at the price level.
            # It is aggregate size, not an order or trader count.
            "size": _json_number(volume),
            "volume": _json_number(volume),
            "current_volume": _json_number(current_volume) if current_volume is not None else None,
        }

    def clear_for_reconnect(self) -> dict[str, Any] | None:
        """Drop potentially stale state without creating a provider timestamp watermark."""

        self._bids.clear()
        self._asks.clear()
        self._level_timestamps.clear()
        self._explicit_depth_levels.clear()
        self._best_quote_prices.clear()
        self._best_quote_timestamps.clear()
        self._recent_fingerprints.clear()
        self._recent_fingerprint_set.clear()
        self._last_timestamp = None
        self._last_reset_timestamp = None
        self._sequence += 1
        return self.snapshot(reset=True)

    def snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "sequence": self._sequence,
            "timestamp": _iso_utc(self._last_timestamp) if self._last_timestamp is not None else None,
            "bids": [
                {"price": _json_number(level.price), "size": _json_number(level.size)}
                for level in sorted(self._bids.values(), key=lambda item: item.price, reverse=True)
            ],
            "asks": [
                {"price": _json_number(level.price), "size": _json_number(level.size)}
                for level in sorted(self._asks.values(), key=lambda item: item.price)
            ],
            "reset": bool(reset),
        }

    def capture_bbo(self) -> dict[str, Any]:
        # A two-sided spread is usable only when both sides were observed
        # recently relative to the latest provider update. No depth is inferred.
        if not self._bids or not self._asks or self._last_timestamp is None:
            return {}
        bid = self._bids[max(self._bids)]
        ask = self._asks[min(self._asks)]
        if max((self._last_timestamp - bid.timestamp).total_seconds(),
               (self._last_timestamp - ask.timestamp).total_seconds()) > 2:
            return {}
        return {"bids": [{"price": _json_number(bid.price)}], "asks": [{"price": _json_number(ask.price)}]}

    def _is_duplicate(self, fingerprint: tuple[Any, ...]) -> bool:
        return fingerprint in self._recent_fingerprint_set

    def _remember_fingerprint(self, fingerprint: tuple[Any, ...]) -> None:
        if fingerprint in self._recent_fingerprint_set:
            return
        self._recent_fingerprints.append(fingerprint)
        self._recent_fingerprint_set.add(fingerprint)
        while len(self._recent_fingerprints) > _RECENT_FINGERPRINT_LIMIT:
            expired = self._recent_fingerprints.popleft()
            self._recent_fingerprint_set.discard(expired)

def _side_for_dom_type(depth_type: int) -> str | None:
    if depth_type in _BID_TYPES:
        return "bid"
    if depth_type in _ASK_TYPES:
        return "ask"
    return None

def _parse_dom_type(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    try:
        if Decimal(str(value)) != Decimal(parsed):
            return None
    except (InvalidOperation, ValueError):
        return None
    return parsed

def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None

def _parse_nonnegative_decimal(value: Any) -> Decimal | None:
    parsed = _parse_decimal(value)
    if parsed is None or parsed < 0:
        return None
    return parsed

def _parse_optional_nonnegative_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _parse_nonnegative_decimal(value)

def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None

def _normalize_contract_id(contract_id: str) -> str:
    normalized = str(contract_id or "").strip()
    if not normalized:
        raise ValueError("contract_id must not be empty")
    if len(normalized) > 120:
        raise ValueError("contract_id is too long")
    return normalized
