from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from ..auth import get_authenticated_user_id
from ..models import PositionLifecycle
from .instruments import resolve_point_value

_EPSILON = 1e-9


@dataclass(frozen=True)
class MarketPriceUpdate:
    contract_id: str
    mark_price: float
    symbol: str | None
    timestamp: datetime


@dataclass(frozen=True)
class PositionUpdate:
    account_id: int
    contract_id: str
    symbol: str | None
    net_qty: float
    avg_price: float
    updated_at: datetime
    realized_pnl_usd: float | None


@dataclass(frozen=True)
class ClosedPositionLifecycle:
    open_key: str
    account_id: int
    contract_id: str
    symbol: str
    opened_at: datetime
    closed_at: datetime
    side: str
    max_qty: float
    avg_entry_at_open: float
    realized_pnl_usd: float | None
    mae_usd: float
    mfe_usd: float
    mae_points: float | None
    mfe_points: float | None
    mae_timestamp: datetime | None
    mfe_timestamp: datetime | None


@dataclass
class _PositionState:
    account_id: int
    contract_id: str
    symbol: str | None
    net_qty: float
    avg_price: float
    updated_at: datetime


@dataclass
class _LifecycleTracker:
    open_key: str
    account_id: int
    contract_id: str
    symbol: str
    side: str
    max_abs_qty: float
    avg_entry_at_open: float
    opened_at: datetime
    mae_usd: float
    mfe_usd: float
    mae_timestamp: datetime | None
    mfe_timestamp: datetime | None


class StreamingPnlTracker:
    """
    Event-driven MAE/MFE tracker.

    This service is intentionally transport-agnostic: hub/websocket clients call
    `ingest_market_event` and `ingest_position_event` with raw payloads.
    """

    def __init__(
        self,
        *,
        point_value_by_symbol: Mapping[str, float] | None = None,
        on_lifecycle_closed: Callable[[ClosedPositionLifecycle], None] | None = None,
    ):
        self._lock = RLock()
        self.price_by_contract_id: dict[str, float] = {}
        self.market_update_by_contract_id: dict[str, MarketPriceUpdate] = {}
        self.position_by_contract_id: dict[str, _PositionState] = {}
        self.tracker_by_contract_id: dict[str, _LifecycleTracker] = {}
        self.symbol_by_contract_id: dict[str, str] = {}
        self._point_value_by_symbol = dict(point_value_by_symbol or {})
        self._on_lifecycle_closed = on_lifecycle_closed or (lambda _lifecycle: None)

    def set_point_value_lookup(self, point_value_by_symbol: Mapping[str, float]) -> None:
        with self._lock:
            self._point_value_by_symbol = dict(point_value_by_symbol)

    def ingest_market_event(self, payload: Mapping[str, Any]) -> bool:
        update = parse_quote_trade(payload)
        if update is None:
            return False

        with self._lock:
            self.price_by_contract_id[update.contract_id] = update.mark_price
            self.market_update_by_contract_id[update.contract_id] = update
            if update.symbol:
                self.symbol_by_contract_id[update.contract_id] = update.symbol
            self._recompute_unrealized(update.contract_id, update.timestamp)
        return True

    def ingest_position_event(self, payload: Mapping[str, Any]) -> bool:
        update = parse_position_update(payload)
        if update is None:
            return False

        with self._lock:
            contract_id = update.contract_id
            previous_state = self.position_by_contract_id.get(contract_id)
            previous_qty = previous_state.net_qty if previous_state is not None else 0.0
            next_qty = update.net_qty
            previous_sign = _sign(previous_qty)
            next_sign = _sign(next_qty)

            if update.symbol:
                self.symbol_by_contract_id[contract_id] = update.symbol

            if previous_sign != 0 and next_sign != 0 and previous_sign != next_sign:
                self._close_lifecycle(contract_id, update.updated_at, update.realized_pnl_usd)
                self._start_lifecycle(update)
            elif previous_sign == 0 and next_sign != 0:
                self._start_lifecycle(update)
            elif previous_sign != 0 and next_sign == 0:
                self._close_lifecycle(contract_id, update.updated_at, update.realized_pnl_usd)

            if next_sign == 0:
                self.position_by_contract_id.pop(contract_id, None)
            else:
                self.position_by_contract_id[contract_id] = _PositionState(
                    account_id=update.account_id,
                    contract_id=contract_id,
                    symbol=update.symbol or self.symbol_by_contract_id.get(contract_id),
                    net_qty=next_qty,
                    avg_price=update.avg_price,
                    updated_at=update.updated_at,
                )
                tracker = self.tracker_by_contract_id.get(contract_id)
                if tracker is not None:
                    tracker.max_abs_qty = max(tracker.max_abs_qty, abs(next_qty))

            self._recompute_unrealized(contract_id, update.updated_at)
        return True

    def get_market_price_update(
        self,
        *,
        contract_id: str | None = None,
        symbol: str | None = None,
    ) -> MarketPriceUpdate | None:
        normalized_contract_id = _as_text(contract_id)
        normalized_symbol = _as_text(symbol)
        normalized_symbol_upper = normalized_symbol.upper() if normalized_symbol else None

        with self._lock:
            if normalized_contract_id:
                update = self.market_update_by_contract_id.get(normalized_contract_id)
                if update is not None:
                    return update

            if normalized_symbol_upper is None:
                return None

            for update in reversed(list(self.market_update_by_contract_id.values())):
                update_symbol = update.symbol or self.symbol_by_contract_id.get(update.contract_id)
                if update_symbol and update_symbol.upper() == normalized_symbol_upper:
                    return update
            return None

    def _start_lifecycle(self, update: PositionUpdate) -> None:
        direction = "LONG" if update.net_qty > 0 else "SHORT"
        symbol = update.symbol or self.symbol_by_contract_id.get(update.contract_id) or update.contract_id
        open_key = f"{update.account_id}:{update.contract_id}:{update.updated_at.isoformat()}"
        tracker = _LifecycleTracker(
            open_key=open_key,
            account_id=update.account_id,
            contract_id=update.contract_id,
            symbol=symbol,
            side=direction,
            max_abs_qty=max(abs(update.net_qty), _EPSILON),
            avg_entry_at_open=update.avg_price,
            opened_at=update.updated_at,
            mae_usd=0.0,
            mfe_usd=0.0,
            mae_timestamp=update.updated_at,
            mfe_timestamp=update.updated_at,
        )
        self.tracker_by_contract_id[update.contract_id] = tracker

    def _close_lifecycle(self, contract_id: str, closed_at: datetime, realized_pnl_usd: float | None) -> None:
        tracker = self.tracker_by_contract_id.pop(contract_id, None)
        if tracker is None:
            return

        point_value = self._resolve_point_value(contract_id, tracker.symbol)
        mae_points: float | None = None
        mfe_points: float | None = None
        if point_value is not None and point_value > _EPSILON and tracker.max_abs_qty > _EPSILON:
            denominator = tracker.max_abs_qty * point_value
            mae_points = tracker.mae_usd / denominator
            mfe_points = tracker.mfe_usd / denominator

        lifecycle = ClosedPositionLifecycle(
            open_key=tracker.open_key,
            account_id=tracker.account_id,
            contract_id=tracker.contract_id,
            symbol=tracker.symbol,
            opened_at=tracker.opened_at,
            closed_at=closed_at,
            side=tracker.side,
            max_qty=tracker.max_abs_qty,
            avg_entry_at_open=tracker.avg_entry_at_open,
            realized_pnl_usd=realized_pnl_usd,
            mae_usd=tracker.mae_usd,
            mfe_usd=tracker.mfe_usd,
            mae_points=mae_points,
            mfe_points=mfe_points,
            mae_timestamp=tracker.mae_timestamp,
            mfe_timestamp=tracker.mfe_timestamp,
        )
        self._on_lifecycle_closed(lifecycle)

    def _recompute_unrealized(self, contract_id: str, timestamp: datetime) -> None:
        position = self.position_by_contract_id.get(contract_id)
        tracker = self.tracker_by_contract_id.get(contract_id)
        mark_price = self.price_by_contract_id.get(contract_id)
        if position is None or tracker is None or mark_price is None:
            return

        qty = abs(position.net_qty)
        if qty <= _EPSILON:
            return

        point_value = self._resolve_point_value(contract_id, position.symbol or tracker.symbol)
        if point_value is None or point_value <= _EPSILON:
            return

        direction = 1.0 if position.net_qty > 0 else -1.0
        unrealized_usd = (mark_price - position.avg_price) * qty * point_value * direction

        if unrealized_usd < tracker.mae_usd:
            tracker.mae_usd = unrealized_usd
            tracker.mae_timestamp = timestamp
        if unrealized_usd > tracker.mfe_usd:
            tracker.mfe_usd = unrealized_usd
            tracker.mfe_timestamp = timestamp

    def _resolve_point_value(self, contract_id: str, symbol: str | None) -> float | None:
        return resolve_point_value(
            symbol=symbol,
            contract_id=contract_id,
            point_value_by_symbol=self._point_value_by_symbol,
        )


def parse_position_update(payload: Mapping[str, Any]) -> PositionUpdate | None:
    for candidate in _iter_payload_candidates(payload):
        contract_id = _as_text(
            _first_value(
                candidate,
                [
                    "contractId",
                    "contract_id",
                    "symbolId",
                    "instrumentId",
                    "contract",
                ],
            )
        )
        if not contract_id:
            continue

        account_id = _safe_int(_first_value(candidate, ["accountId", "account_id", "userAccountId"])) or 0
        symbol = _as_text(_first_value(candidate, ["symbol", "contractSymbol", "rootSymbol"]))

        net_qty = _safe_float(
            _first_value(
                candidate,
                [
                    "netQty",
                    "net_qty",
                    "netPosition",
                    "positionQty",
                    "position",
                    "qty",
                    "quantity",
                    "currentQty",
                ],
            ),
            default=None,
        )
        if net_qty is None:
            long_qty = _safe_float(_first_value(candidate, ["longQty", "long_qty"]), default=0.0)
            short_qty = _safe_float(_first_value(candidate, ["shortQty", "short_qty"]), default=0.0)
            if abs(long_qty) > _EPSILON or abs(short_qty) > _EPSILON:
                net_qty = long_qty - short_qty

        if net_qty is None:
            side = _as_text(_first_value(candidate, ["side", "direction"]))
            qty = _safe_float(_first_value(candidate, ["size", "qty", "quantity"]), default=None)
            if side and qty is not None:
                side_sign = 1.0 if side.upper() in {"BUY", "LONG"} else -1.0 if side.upper() in {"SELL", "SHORT"} else 0.0
                if side_sign != 0.0:
                    net_qty = qty * side_sign

        if net_qty is None:
            continue

        avg_price = _safe_float(
            _first_value(
                candidate,
                [
                    "avgPrice",
                    "averagePrice",
                    "avgEntryPrice",
                    "average_entry_price",
                    "entryPrice",
                ],
            ),
            default=0.0,
        )
        updated_at = _parse_timestamp(
            _first_value(candidate, ["timestamp", "updatedAt", "updateTime", "time", "tradeTimestamp"])
        ) or _utc_now()
        realized_pnl = _safe_float(
            _first_value(candidate, ["realizedPnl", "realized_pnl", "pnl", "profitAndLoss"]),
            default=None,
        )

        return PositionUpdate(
            account_id=account_id,
            contract_id=contract_id,
            symbol=symbol,
            net_qty=net_qty,
            avg_price=avg_price,
            updated_at=updated_at,
            realized_pnl_usd=realized_pnl,
        )

    return None


def parse_quote_trade(payload: Mapping[str, Any]) -> MarketPriceUpdate | None:
    for candidate in _iter_payload_candidates(payload):
        contract_id = _as_text(
            _first_value(
                candidate,
                [
                    "contractId",
                    "contract_id",
                    "symbolId",
                    "instrumentId",
                    "contract",
                ],
            )
        )
        if not contract_id:
            continue

        symbol = _as_text(_first_value(candidate, ["symbol", "contractSymbol", "rootSymbol"]))
        bid = _safe_float(_first_value(candidate, ["bid", "bidPrice", "bestBid", "bestBidPrice"]), default=None)
        ask = _safe_float(_first_value(candidate, ["ask", "askPrice", "bestAsk", "bestAskPrice"]), default=None)
        last = _safe_float(
            _first_value(candidate, ["markPrice", "last", "lastPrice", "tradePrice", "price"]),
            default=None,
        )

        mark_price: float | None = None
        if bid is not None and ask is not None:
            mark_price = (bid + ask) / 2.0
        elif last is not None:
            mark_price = last

        if mark_price is None:
            continue

        timestamp = _parse_timestamp(
            _first_value(candidate, ["timestamp", "updatedAt", "updateTime", "time", "tradeTimestamp"])
        ) or _utc_now()
        return MarketPriceUpdate(
            contract_id=contract_id,
            mark_price=mark_price,
            symbol=symbol,
            timestamp=timestamp,
        )

    return None


def save_position_lifecycle_mae_mfe(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    contract_id: str,
    symbol: str | None = None,
    opened_at: datetime,
    closed_at: datetime,
    mae_usd: float,
    mfe_usd: float,
    realized_pnl_usd: float | None,
    side: str,
    max_qty: float,
    avg_entry_at_open: float | None = None,
    mae_points: float | None = None,
    mfe_points: float | None = None,
    mae_timestamp: datetime | None = None,
    mfe_timestamp: datetime | None = None,
) -> PositionLifecycle:
    resolved_user_id = user_id or get_authenticated_user_id()
    row = PositionLifecycle(
        user_id=resolved_user_id,
        account_id=account_id,
        contract_id=contract_id,
        symbol=symbol or contract_id,
        opened_at=opened_at,
        closed_at=closed_at,
        side=side,
        max_qty=max_qty,
        avg_entry_at_open=avg_entry_at_open,
        realized_pnl_usd=realized_pnl_usd,
        mae_usd=mae_usd,
        mfe_usd=mfe_usd,
        mae_points=mae_points,
        mfe_points=mfe_points,
        mae_timestamp=mae_timestamp,
        mfe_timestamp=mfe_timestamp,
    )
    db.add(row)
    db.flush()
    return row


def _iter_payload_candidates(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    queue: list[Mapping[str, Any]] = [payload]
    candidates: list[Mapping[str, Any]] = []
    seen_ids: set[int] = set()

    while queue:
        current = queue.pop(0)
        identity = id(current)
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        candidates.append(current)

        for key in ["data", "payload", "quote", "trade", "position", "result"]:
            nested = current.get(key)
            if isinstance(nested, Mapping):
                queue.append(nested)

        arguments = current.get("arguments")
        if isinstance(arguments, list):
            for argument in arguments:
                if isinstance(argument, Mapping):
                    queue.append(argument)

    return candidates


def _first_value(payload: Mapping[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _safe_float(value: Any, *, default: float | None) -> float | None:
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


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _parse_timestamp(value: Any) -> datetime | None:
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
        text = value.strip()
        if text == "":
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return _as_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sign(value: float) -> int:
    if value > _EPSILON:
        return 1
    if value < -_EPSILON:
        return -1
    return 0
