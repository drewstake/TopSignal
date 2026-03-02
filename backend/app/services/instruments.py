from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import InstrumentMetadata

POINTS_BASIS_AUTO = "auto"
POINTS_BASIS_SYMBOLS = ("MNQ", "MES", "MGC", "SIL")
_POINTS_BASIS_SYMBOL_SET = set(POINTS_BASIS_SYMBOLS)

_CONTRACT_SUFFIX_PATTERN = re.compile(r"^([A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,4}$", re.IGNORECASE)
_ALNUM_TOKEN_PATTERN = re.compile(r"[^A-Z0-9]+")


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    tick_size: float
    tick_value: float

    @property
    def point_value(self) -> float | None:
        if self.tick_size <= 0:
            return None
        return self.tick_value / self.tick_size


DEFAULT_INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    "MNQ": InstrumentSpec(symbol="MNQ", tick_size=0.25, tick_value=0.50),
    "MES": InstrumentSpec(symbol="MES", tick_size=0.25, tick_value=1.25),
    "MGC": InstrumentSpec(symbol="MGC", tick_size=0.10, tick_value=1.00),
    "SIL": InstrumentSpec(symbol="SIL", tick_size=0.005, tick_value=5.00),
}


def normalize_points_basis(raw_value: str | None) -> str:
    if raw_value is None:
        return POINTS_BASIS_AUTO

    text = raw_value.strip()
    if text == "":
        return POINTS_BASIS_AUTO

    if text.lower() == POINTS_BASIS_AUTO:
        return POINTS_BASIS_AUTO

    upper = text.upper()
    if upper in _POINTS_BASIS_SYMBOL_SET:
        return upper

    allowed = ",".join((POINTS_BASIS_AUTO, *POINTS_BASIS_SYMBOLS))
    raise ValueError(f"pointsBasis must be one of {allowed}")


def ensure_default_instrument_metadata(db: Session) -> None:
    existing_rows = db.query(InstrumentMetadata.symbol).all()
    existing_symbols = {str(row.symbol).strip().upper() for row in existing_rows if row.symbol}

    for symbol, spec in DEFAULT_INSTRUMENT_SPECS.items():
        if symbol in existing_symbols:
            continue
        db.add(
            InstrumentMetadata(
                symbol=symbol,
                tick_size=spec.tick_size,
                tick_value=spec.tick_value,
            )
        )


def load_instrument_specs(db: Session) -> dict[str, InstrumentSpec]:
    specs = dict(DEFAULT_INSTRUMENT_SPECS)
    bind = db.get_bind()
    if bind is None:
        return specs

    try:
        inspector = inspect(bind)
        if "instrument_metadata" not in set(inspector.get_table_names()):
            return specs
        rows = db.query(InstrumentMetadata).all()
    except SQLAlchemyError:
        return specs

    for row in rows:
        symbol = normalize_symbol_key(row.symbol)
        if symbol is None:
            continue
        tick_size = float(row.tick_size) if row.tick_size is not None else 0.0
        tick_value = float(row.tick_value) if row.tick_value is not None else 0.0
        if tick_size <= 0 or tick_value <= 0:
            continue
        specs[symbol] = InstrumentSpec(symbol=symbol, tick_size=tick_size, tick_value=tick_value)
    return specs


def build_point_value_lookup(specs: Mapping[str, InstrumentSpec]) -> dict[str, float]:
    output: dict[str, float] = {}
    for symbol, spec in specs.items():
        point_value = spec.point_value
        if point_value is None or point_value <= 0:
            continue
        output[symbol] = point_value
    return output


def resolve_point_value(
    *,
    symbol: str | None,
    contract_id: str | None,
    point_value_by_symbol: Mapping[str, float],
) -> float | None:
    for candidate in symbol_candidates(symbol=symbol, contract_id=contract_id):
        point_value = point_value_by_symbol.get(candidate)
        if point_value is not None and point_value > 0:
            return point_value
    return None


def symbol_candidates(*, symbol: str | None, contract_id: str | None) -> list[str]:
    candidates: list[str] = []
    for raw in [symbol, contract_id]:
        normalized = normalize_symbol_key(raw)
        if normalized is None:
            continue
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def normalize_symbol_key(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None

    text = str(raw_value).strip().upper()
    if text == "":
        return None

    if text in _POINTS_BASIS_SYMBOL_SET:
        return text

    if "." in text:
        dotted_tokens = [token.strip().upper() for token in text.split(".") if token.strip()]
        for token in dotted_tokens:
            if token in _POINTS_BASIS_SYMBOL_SET:
                return token
        if len(dotted_tokens) >= 2:
            middle_candidate = dotted_tokens[-2]
            if middle_candidate and middle_candidate not in {"US", "F", "CON"}:
                suffix_match = _CONTRACT_SUFFIX_PATTERN.match(middle_candidate)
                if suffix_match:
                    return suffix_match.group(1).upper()
                return middle_candidate

    cleaned = _ALNUM_TOKEN_PATTERN.sub("", text)
    if cleaned in _POINTS_BASIS_SYMBOL_SET:
        return cleaned

    suffix_match = _CONTRACT_SUFFIX_PATTERN.match(cleaned)
    if suffix_match:
        return suffix_match.group(1).upper()

    return cleaned or None
