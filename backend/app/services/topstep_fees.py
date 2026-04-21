from __future__ import annotations

from datetime import datetime, timezone

from .instruments import symbol_candidates

_TOPSTEP_COMMISSION_EFFECTIVE_AT = datetime(2026, 4, 12, tzinfo=timezone.utc)
_TOPSTEP_MICRO_COMMISSION_PER_SIDE = 0.25
_TOPSTEP_NON_MICRO_COMMISSION_PER_SIDE = 0.50
_TOPSTEP_MICRO_SYMBOLS = frozenset(
    {
        "MES",
        "MNQ",
        "M2K",
        "MYM",
        "MBT",
        "MET",
        "MCL",
        "MNG",
        "MGC",
        "SIL",
        "M6A",
        "M6B",
        "M6E",
        "MHG",
    }
)


def effective_topstep_trade_fee(
    *,
    trade_timestamp: datetime | None,
    pnl: float | None,
    fees: float | None,
    symbol: str | None = None,
    contract_id: str | None = None,
    size: float | None = None,
    raw_fee_is_per_side: bool,
) -> float:
    fee_amount = float(fees) if fees is not None else 0.0
    if pnl is not None and raw_fee_is_per_side:
        fee_amount *= 2
    if pnl is None:
        return fee_amount
    return fee_amount + _topstep_commission_round_turn(
        trade_timestamp=trade_timestamp,
        symbol=symbol,
        contract_id=contract_id,
        size=size,
    )


def _topstep_commission_round_turn(
    *,
    trade_timestamp: datetime | None,
    symbol: str | None,
    contract_id: str | None,
    size: float | None,
) -> float:
    normalized_timestamp = _as_utc(trade_timestamp)
    if normalized_timestamp is None or normalized_timestamp < _TOPSTEP_COMMISSION_EFFECTIVE_AT:
        return 0.0
    contract_count = abs(float(size)) if size is not None else 0.0
    if contract_count <= 0:
        contract_count = 1.0
    per_side = _TOPSTEP_MICRO_COMMISSION_PER_SIDE if _is_micro_contract(symbol=symbol, contract_id=contract_id) else _TOPSTEP_NON_MICRO_COMMISSION_PER_SIDE
    return per_side * 2 * contract_count


def _is_micro_contract(*, symbol: str | None, contract_id: str | None) -> bool:
    for candidate in symbol_candidates(symbol=symbol, contract_id=contract_id):
        if candidate in _TOPSTEP_MICRO_SYMBOLS:
            return True
        if candidate.startswith("M"):
            return True
    return False


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
