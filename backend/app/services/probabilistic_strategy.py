"""Versioned, pure MNQ research models. No database, broker or order adapter.

All forecasts are experimental distributions, never heuristic indicator scores.
The public decision is always NO_TRADE until a separately reviewed promotion.
See docs/topbot-probabilistic-research.md for assumptions and equations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import cached_property
import math
from typing import Any, Literal, Protocol, Sequence
from zoneinfo import ZoneInfo

import numpy as np
from scipy.stats import t as student_t
from .probabilistic_protocol import protocol


INTERFACE_VERSION = "mnq-probabilistic-v3"
MODEL_VERSIONS = ("empirical_pool_v1", "bayesian_cells_v1", "kernel_paths_v1")
DEFAULT_RESEARCH_MODEL = "bayesian_cells_v1"  # Research prototype, not production selection.
SPEC = protocol()
HORIZON = SPEC["horizon_bars"]
FEATURE_BARS = SPEC["feature_bars"]
TICK = 0.25
POINT_VALUE = 2.0
ET = ZoneInfo("America/New_York")
BAR = timedelta(minutes=SPEC["bar_minutes"])
Action = Literal["BUY", "SELL", "NO_TRADE"]


class VolatilityAboveRiskCap(ValueError):
    def __init__(self, sigma: float, stop: float):
        self.sigma, self.stop = sigma, stop
        super().__init__(f"volatility_above_risk_cap: sigma={sigma:.4f}, implied stop={stop:.2f}; 25-point risk cap.")


def utc(value: datetime) -> datetime:
    # SQLAlchemy's stored candle timestamps are UTC, including naive SQLite values.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    contract_id: str
    owner: str
    live: bool = False
    partial: bool = False

    @classmethod
    def from_row(cls, row: Any) -> Candle:
        if str(row.unit) != "minute" or int(row.unit_number) != 5:
            raise ValueError("Research requires observed five-minute candles.")
        if not str(row.contract_id).startswith("CON.F.US.MNQ."):
            raise ValueError("Research is restricted to MNQ delivery contracts.")
        return cls(utc(row.candle_timestamp), float(row.open_price), float(row.high_price),
                   float(row.low_price), float(row.close_price),
                   None if row.volume is None else float(row.volume), str(row.contract_id),
                   str(row.user_id), bool(row.live), bool(row.is_partial))

    def valid(self) -> bool:
        prices = (self.open, self.high, self.low, self.close)
        return (self.timestamp.tzinfo is not None and not self.partial
                and self.timestamp.second == 0 and self.timestamp.microsecond == 0
                and self.timestamp.minute % 5 == 0
                and all(math.isfinite(x) and x > 0 for x in prices)
                and self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high
                and self.volume is not None and math.isfinite(self.volume) and self.volume > 0)


@dataclass(frozen=True)
class Costs:
    commission_per_side: float = SPEC["commission_per_side_usd"]
    spread_ticks: float = SPEC["assumed_spread_ticks"]
    slippage_ticks: float = SPEC["slippage_ticks_per_side"]
    latency_ticks: float = SPEC["latency_ticks_per_side"]

    def __post_init__(self) -> None:
        if any(not math.isfinite(x) or x < 0 for x in (
            self.commission_per_side, self.spread_ticks, self.slippage_ticks, self.latency_ticks
        )):
            raise ValueError("Every cost assumption must be finite and nonnegative.")

    @property
    def adverse_points_per_side(self) -> float:
        return math.ceil(self.spread_ticks / 2 + self.slippage_ticks + self.latency_ticks) * TICK

    def describe(self) -> dict:
        fees = 2 * self.commission_per_side
        spread = self.spread_ticks * TICK * POINT_VALUE
        slippage = 2 * self.slippage_ticks * TICK * POINT_VALUE
        latency = 2 * self.latency_ticks * TICK * POINT_VALUE
        total = fees + 2 * self.adverse_points_per_side * POINT_VALUE
        return {"fees_usd": fees, "spread_usd": spread, "slippage_usd": slippage,
                "latency_usd": latency, "rounding_usd": total - fees - spread - slippage - latency,
                "total_usd": total,
                "basis": "Assumed round trip per contract; spread and latency are not measured fills."}


@dataclass(frozen=True)
class Features:
    values: tuple[float, float, float, float]
    stop_points: float
    volatility: float
    decision_at: datetime
    day: str


def contiguous(rows: Sequence[Candle]) -> bool:
    if not rows or any(not row.valid() for row in rows):
        return False
    identity = (rows[0].owner, rows[0].contract_id, rows[0].live)
    return all((row.owner, row.contract_id, row.live) == identity for row in rows) and all(
        b.timestamp - a.timestamp == BAR for a, b in zip(rows, rows[1:])
    )


def features(rows: Sequence[Candle], *, as_of: datetime, stop_multiplier: float = 1.0,
             all_sessions: bool = False) -> Features:
    """No sorting, interpolation, cross-owner pooling or partial-bar substitution."""
    if not math.isfinite(stop_multiplier) or stop_multiplier <= 0:
        raise ValueError("Invalid stop multiplier.")
    window = rows[-FEATURE_BARS:]
    if len(window) != FEATURE_BARS or not contiguous(window):
        raise ValueError("Need 21 contiguous, complete, valid same-contract candles with observed volume.")
    decision_at = window[-1].timestamp + BAR
    if decision_at > utc(as_of):
        raise ValueError("A feature candle has not closed at decision time.")
    local = decision_at.astimezone(ET)
    if not all_sessions and (local.weekday() >= 5 or not time(9, 35) <= local.time() <= time(15, 30)):
        raise ValueError("Outside the 09:35–15:30 ET research entry window.")
    changes = np.diff([row.close for row in window])
    sigma = float(np.sqrt(np.mean(changes ** 2)))
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError("No estimable price variation.")
    recent = float(np.sqrt(np.mean(changes[-5:] ** 2)))
    if recent <= 0:
        raise ValueError("No recent price variation.")
    volume_base = float(np.mean([row.volume for row in window[:-1]]))
    values = (float(changes[-1] / sigma), float(sum(changes[-3:]) / (sigma * math.sqrt(3))),
              math.log(recent / sigma), math.log(window[-1].volume / volume_base))
    stop = max(SPEC["minimum_candidate_stop_points"], math.ceil(stop_multiplier * sigma * math.sqrt(HORIZON) / TICK) * TICK)
    if stop > SPEC["maximum_candidate_stop_points"]:
        raise VolatilityAboveRiskCap(sigma, stop)
    return Features(values, stop, sigma, decision_at, local.date().isoformat())


@dataclass(frozen=True)
class Sample:
    features: Features
    # Each normalized OHLC is relative to the next observed open; no entry gap prediction.
    path: tuple[tuple[float, float, float, float], ...]
    label_end: datetime


def make_samples(rows: Sequence[Candle], *, stride: int = 1, delay_bars: int = 0,
                 stop_multiplier: float = 1.0, population_by_day: dict | None = None) -> tuple[list[Sample], dict[str, int]]:
    if stride not in (1, 3) or delay_bars not in (0, 1):
        raise ValueError("Unsupported research lattice or latency stress.")
    samples: list[Sample] = []
    rejected: dict[str, int] = {}
    for index in range(FEATURE_BARS - 1, len(rows) - HORIZON - delay_bars):
        now = rows[index].timestamp + BAR
        local = now.astimezone(ET)
        if stride == 3 and (local.hour * 60 + local.minute - 570) % 15:
            continue
        population = None
        if population_by_day is not None and time(9, 35) <= local.time() <= time(15, 30):
            population = population_by_day.setdefault(local.date().isoformat(),
                {"candidate_windows": 0, "accepted": 0, "volatility_cap": 0, "other_rejections": 0})
            population["candidate_windows"] += 1
        try:
            f = features(rows[max(0, index - FEATURE_BARS + 1):index + 1], as_of=now,
                         stop_multiplier=stop_multiplier)
            future = rows[index + 1 + delay_bars:index + 1 + delay_bars + HORIZON]
            if not contiguous(rows[index:index + HORIZON + delay_bars + 1]):
                raise ValueError("Missing or invalid future path; outcome is unknown.")
            end = future[-1].timestamp + BAR
            if end.astimezone(ET).date() != local.date() or end.astimezone(ET).time() > time(15, 45):
                raise ValueError("Horizon exceeds the session exit deadline.")
            reference = future[0].open
            path = tuple(tuple((v - reference) / (f.volatility * math.sqrt(HORIZON)) for v in
                               (row.open, row.high, row.low, row.close)) for row in future)
            samples.append(Sample(f, path, end))
            if population is not None:
                population["accepted"] += 1
        except ValueError as exc:
            reason = "volatility_above_risk_cap" if isinstance(exc, VolatilityAboveRiskCap) else str(exc)
            rejected[reason] = rejected.get(reason, 0) + 1
            if population is not None:
                population["volatility_cap" if isinstance(exc, VolatilityAboveRiskCap) else "other_rejections"] += 1
    return samples, rejected


def price_paths(paths: np.ndarray, *, stop: float, side: int, costs: Costs,
                target_ratio: float = SPEC["target_stop_ratio"], scale: float | None = None,
                trade_through_ticks: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reprice first-passage scenarios with fills and brackets on the same basis.

    Returns net USD, exit kind (0 stop, 1 time, 2 target), and bars exposed.
    Gaps through stops can lose more than the bracket; target gaps get no bonus.
    """
    if side not in (-1, 1) or not math.isfinite(stop) or stop <= 0 or target_ratio <= 0:
        raise ValueError("Invalid side or bracket.")
    if scale is not None and (not math.isfinite(scale) or scale <= 0):
        raise ValueError("Invalid forecast price scale")
    p = np.asarray(paths, dtype=float) * (stop if scale is None else scale)
    if p.ndim != 3 or p.shape[1:] != (HORIZON, 4) or not np.isfinite(p).all():
        raise ValueError("Invalid scenario array.")
    n = len(p)
    # Transform into signed price coordinates: losses are always below entry.
    opening = side * p[:, :, 0]
    favorable = p[:, :, 1] if side == 1 else -p[:, :, 2]
    adverse = p[:, :, 2] if side == 1 else -p[:, :, 1]
    closing = side * p[:, :, 3]
    entry = opening[:, 0] + costs.adverse_points_per_side
    stop_level = entry - stop
    target = entry + math.ceil(stop * target_ratio / TICK) * TICK
    fill = closing[:, -1].copy()
    kind = np.ones(n, dtype=int)
    duration = np.full(n, HORIZON, dtype=int)
    pending = np.ones(n, dtype=bool)
    for bar in range(HORIZON):
        # A known opening gap is ordered before that bar's uncertain high/low.
        gap_stop = pending & (opening[:, bar] <= stop_level)
        gap_target = pending & ~gap_stop & (opening[:, bar] >= target + trade_through_ticks * TICK)
        hit_stop = pending & ~gap_target & (adverse[:, bar] <= stop_level)
        hit_target = pending & ~gap_stop & ~hit_stop & (favorable[:, bar] >= target + trade_through_ticks * TICK)
        stopped = gap_stop | hit_stop
        targeted = gap_target | hit_target
        fill[stopped] = np.minimum(stop_level[stopped], opening[stopped, bar])
        fill[targeted] = target[targeted]
        kind[stopped] = 0
        kind[targeted] = 2
        done = stopped | targeted
        duration[done] = bar + 1
        pending[done] = False
    # Uniform adverse exit haircut is deliberately conservative even on targets.
    net = POINT_VALUE * (fill - costs.adverse_points_per_side - entry) - 2 * costs.commission_per_side
    return net, kind, duration


def weighted_crps(values: np.ndarray, weights: np.ndarray, observed: float) -> float:
    order = np.argsort(values, kind="stable")
    y, w = values[order], weights[order]
    before = np.cumsum(w) - w
    # E|Y-y| - 1/2 E|Y-Y'|, evaluated in O(n log n).
    return float(np.sum(w * np.abs(y - observed)) - np.sum(w * y * (2 * before + w - 1)))


class StrategyModel(Protocol):
    version: str

    def forecast(self, f: Features, costs: Costs) -> dict: ...


@dataclass(frozen=True)
class PathModel:
    version: str
    samples: tuple[Sample, ...]
    pool_mix: float = 0.0

    def __post_init__(self) -> None:
        if self.version not in MODEL_VERSIONS or not 0 <= self.pool_mix <= 1:
            raise ValueError("Unknown model revision or calibration mixture.")
        if not 2 <= len(self.samples) <= 4096:
            raise ValueError("Model requires 2–4096 finite, completed training scenarios.")
        previous = None
        for sample in self.samples:
            f = sample.features
            if (not all(math.isfinite(v) for v in (*f.values, f.stop_points, f.volatility))
                    or not 4 <= f.stop_points <= 25 or f.volatility <= 0
                    or f.decision_at.tzinfo is None or sample.label_end.tzinfo is None
                    or sample.label_end != f.decision_at + HORIZON * BAR
                    or f.day != f.decision_at.astimezone(ET).date().isoformat()
                    or (previous is not None and f.decision_at <= previous)):
                raise ValueError("Invalid or unordered training metadata.")
            previous = f.decision_at
        paths = self.paths
        if paths.shape != (len(self.samples), HORIZON, 4) or not np.isfinite(paths).all():
            raise ValueError("Invalid training paths.")
        if (not np.allclose(paths[:, 0, 0], 0) or np.any(paths[:, :, 2] > np.minimum(paths[:, :, 0], paths[:, :, 3]))
                or np.any(paths[:, :, 1] < np.maximum(paths[:, :, 0], paths[:, :, 3]))):
            raise ValueError("Malformed OHLC path.")

    @cached_property
    def paths(self) -> np.ndarray:
        values = np.asarray([s.path for s in self.samples], dtype=float)
        values.setflags(write=False)
        return values

    @cached_property
    def trained_through(self) -> datetime:
        return max(s.label_end for s in self.samples)

    @cached_property
    def _feature_values(self) -> np.ndarray:
        values = np.asarray([s.features.values for s in self.samples])
        values.setflags(write=False)
        return values

    @cached_property
    def _day_codes(self) -> np.ndarray:
        # np.unique preserves the same sorted day order used by the HAC lags.
        return np.unique([s.features.day for s in self.samples], return_inverse=True)[1]

    @cached_property
    def _feature_scale(self) -> np.ndarray:
        return np.maximum(np.std(self._feature_values, axis=0), 0.1)

    def weights(self, f: Features) -> np.ndarray:
        if self.trained_through >= f.decision_at:
            raise ValueError("Training label cutoff must be strictly before this decision.")
        x = self._feature_values
        query = np.asarray(f.values)
        if not np.isfinite(query).all() or np.max(np.abs(x)) > 50 or np.max(np.abs(query)) > 50:
            raise ValueError("Features exceed the research model's numerical domain.")
        n = len(x)
        if self.version == "empirical_pool_v1":
            weights = np.full(n, 1 / n)
        elif self.version == "bayesian_cells_v1":
            same = ((x[:, 1] >= 0) == (query[1] >= 0)) & ((x[:, 2] >= 0) == (query[2] >= 0))
            weights = (same.astype(float) + 20 / n) / (np.sum(same) + 20)
        else:
            scale = self._feature_scale  # Train only; never future normalization.
            distance = np.sum(((x - query) / scale) ** 2, axis=1)
            kernel = np.exp(-0.5 * np.minimum(distance, 100))
            weights = 0.95 * kernel / np.sum(kernel) + 0.05 / n
        mixed = (1 - self.pool_mix) * weights + self.pool_mix / n
        if not np.isfinite(mixed).all() or np.any(mixed < 0):
            raise ValueError("Invalid forecast weights.")
        return mixed / mixed.sum()

    def forecast(self, f: Features, costs: Costs = Costs()) -> dict:
        weights = self.weights(f)
        day_codes = self._day_codes
        day_weight = np.bincount(day_codes, weights=weights)
        effective_days = float(1 / np.sum(day_weight ** 2))
        outcomes = {}
        for action, side in (("BUY", 1), ("SELL", -1)):
            payoffs, kinds, _ = price_paths(self.paths, stop=f.stop_points, scale=f.volatility * math.sqrt(HORIZON), side=side, costs=costs)
            mean = float(np.dot(weights, payoffs))
            contributions = np.bincount(day_codes, weights=weights * (payoffs - mean))
            variance = float(np.dot(contributions, contributions))
            hac = variance + 2 * sum((1 - lag / 5) * float(np.dot(contributions[lag:], contributions[:-lag]))
                                    for lag in range(1, min(5, len(contributions))))
            # Small-cluster correction; HAC is never allowed to reduce uncertainty.
            correction = effective_days / max(1e-9, effective_days - 1)
            se = math.sqrt(max(variance, hac, 0) * correction)
            critical = float(student_t.ppf(1 - SPEC["forecast_familywise_alpha"] / SPEC["forecast_tests_per_refit"],
                                          max(1, effective_days - 1)))
            penalty = critical * se + SPEC["cost_uncertainty_usd"]
            if not all(math.isfinite(value) for value in (mean, se, penalty)):
                raise ValueError("Forecast uncertainty is not finite.")
            def probability(mask):
                return float(np.clip(np.sum(weights[mask]), 0, 1))
            outcomes[action] = {
                "probability_net_positive": probability(payoffs > 0),
                "probability_net_nonpositive": probability(payoffs <= 0),
                "probability_stop": probability(kinds == 0),
                "probability_time_exit": probability(kinds == 1),
                "probability_target": probability(kinds == 2),
                "expected_net_usd": mean, "standard_error_usd": se,
                "uncertainty_penalty_usd": penalty, "lower_utility_usd": mean - penalty,
                "effective_days": effective_days,
                "critical_value": critical,
                "expected_net_r": mean / (POINT_VALUE * f.stop_points),
            }
        best = max(outcomes, key=lambda key: outcomes[key]["lower_utility_usd"])
        supported = len(self.samples) >= SPEC["minimum_training_examples"] and effective_days >= SPEC["minimum_effective_days_per_forecast"]
        proposal = best if supported and outcomes[best]["lower_utility_usd"] > SPEC["minimum_net_edge_usd"] else "NO_TRADE"
        reasons = ["Unvalidated research model: no order routing or default promotion."]
        if not supported:
            reasons.append("Insufficient independent evidence: require 300 training paths and 20 effective days.")
        if outcomes[best]["lower_utility_usd"] <= 1:
            reasons.append("Neither action clears costs, model uncertainty and the $1 minimum net edge.")
        return {"interface_version": INTERFACE_VERSION, "model_version": self.version,
                "validation_status": "unvalidated", "action": "NO_TRADE", "research_action": proposal,
                "routing_allowed": False, "horizon_minutes": 15, "probability_basis": "uncalibrated_model_estimate",
                "model_trained_through": self.trained_through.isoformat(),
                "stop_points": f.stop_points, "target_points": math.ceil(f.stop_points * 1.5 / TICK) * TICK,
                "quantity": 1, "costs": costs.describe(), "forecasts": outcomes,
                "no_trade_expected_net_usd": 0.0, "minimum_net_edge_usd": 1.0,
                "uncertainty_method": "Day-cluster/HAC SE with Kish effective-day correction; Student t and eight-test Bonferroni correction plus cost buffer.",
                "training_paths": len(self.samples), "reasons": reasons}

    def to_dict(self) -> dict:
        return {"interface_version": INTERFACE_VERSION, "version": self.version, "pool_mix": self.pool_mix,
                "samples": [{"features": list(s.features.values), "stop": s.features.stop_points,
                             "volatility": s.features.volatility, "decision_at": s.features.decision_at.isoformat(),
                             "day": s.features.day, "path": s.path, "label_end": s.label_end.isoformat()}
                            for s in self.samples]}

    @classmethod
    def from_dict(cls, payload: dict) -> PathModel:
        if payload.get("interface_version") != INTERFACE_VERSION:
            raise ValueError("Unsupported model artifact interface.")
        raw = payload["samples"]
        if not isinstance(raw, list) or not 2 <= len(raw) <= 4096:
            raise ValueError("Invalid model sample count.")
        samples = []
        for row in raw:
            values = tuple(float(x) for x in row["features"])
            if len(values) != 4:
                raise ValueError("Invalid model feature count.")
            f = Features(values, float(row["stop"]), float(row["volatility"]),
                         datetime.fromisoformat(row["decision_at"]), row["day"])
            samples.append(Sample(f, tuple(tuple(float(v) for v in bar) for bar in row["path"]),
                                  datetime.fromisoformat(row["label_end"])))
        return cls(payload["version"], tuple(samples), float(payload["pool_mix"]))
