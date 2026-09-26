"""Interpretable paired-baseline research models; never an order-routing model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Protocol, Sequence

import numpy as np

from .depth_research import L1_NAMES, L2_NAMES, SHAPE_NAMES, FLOW_NAMES, timestamp
from .probabilistic_strategy import Features

VERSION = "depth_direct_payoff_v2"
CANDLE_NAMES = ("return_1", "return_3", "relative_volatility", "relative_volume")
FEATURE_SETS = {"candles": CANDLE_NAMES, "level1": CANDLE_NAMES + L1_NAMES,
                "level2": CANDLE_NAMES + L1_NAMES + L2_NAMES,
                "level2_no_shape": CANDLE_NAMES + L1_NAMES + FLOW_NAMES,
                "level2_no_flow": CANDLE_NAMES + L1_NAMES + SHAPE_NAMES}


@dataclass(frozen=True)
class Example:
    at: datetime
    label_end: datetime
    contract_id: str
    candle: Features
    depth: dict[str, float]
    net: tuple[float, float]  # BUY, SELL; 15-minute sampled executable-quote proxy.
    duration: tuple[float, float]
    execution_cost: tuple[float, float]  # One-second displayed sweep vs decision mid.
    short_net: dict[int, tuple[float, float]]

    @property
    def day(self):
        return self.candle.day

    def vector(self, feature_set: str) -> np.ndarray:
        values = dict(zip(CANDLE_NAMES, self.candle.values)) | self.depth
        try:
            result = np.asarray([values[name] for name in FEATURE_SETS[feature_set]], dtype=float)
        except (KeyError, TypeError) as exc:
            raise ValueError("Missing features; no fallback is validated.") from exc
        if not np.isfinite(result).all():
            raise ValueError("Missing/nonfinite features; zero imputation is forbidden.")
        return result


def sigmoid(value):
    return 1 / (1 + np.exp(-np.clip(value, -35, 35)))


def daily_se(values: np.ndarray) -> float:
    n = len(values)
    residual = values - values.mean()
    variance = float(residual @ residual)
    hac = variance + 2 * sum((1 - lag / 5) * float(residual[lag:] @ residual[:-lag]) for lag in range(1, min(5, n)))
    return math.sqrt(max(variance, hac, 0) / max(1, n * (n - 1)))


class DepthModelInterface(Protocol):
    def forecast(self, values: np.ndarray, *, at: datetime) -> dict: ...


@dataclass(frozen=True)
class DepthModel:
    feature_set: str
    mean: np.ndarray
    scale: np.ndarray
    coefficients: np.ndarray
    covariance: np.ndarray
    conditional_net: np.ndarray
    payoff_se: np.ndarray
    execution_coefficients: np.ndarray
    training_examples: int
    training_days: int
    trained_through: datetime
    penalty: float = 1.
    horizon_seconds: int = 900
    payoff_coefficients: np.ndarray | None = None
    payoff_covariance: np.ndarray | None = None
    effective_days: float = 0.

    @classmethod
    def fit(cls, samples: Sequence[Example], feature_set: str, *, penalty: float = 1., horizon_seconds: int = 900):
        if feature_set not in FEATURE_SETS or not 2 <= len(samples) <= 20000 or penalty not in {.5, 1., 2.}:
            raise ValueError("Unsupported model specification or sample count.")
        if horizon_seconds not in {1, 5, 30, 900}:
            raise ValueError("Unregistered horizon.")
        if any(b.at < a.label_end for a, b in zip(samples, samples[1:])) or any(s.label_end <= s.at for s in samples):
            raise ValueError("Training examples must be ordered completed outcomes.")
        x = np.stack([s.vector(feature_set) for s in samples])
        days = np.asarray([s.day for s in samples]); unique = sorted(set(days))
        w = np.asarray([1 / np.sum(days == d) / len(unique) for d in days])
        mean = w @ x; scale = np.maximum(np.sqrt(w @ ((x - mean) ** 2)), .1)
        z = np.column_stack([np.ones(len(x)), np.clip((x - mean) / scale, -8, 8)])
        ridge = np.diag([1e-6] + [penalty / len(x)] * x.shape[1])
        coef, cov, means, ses, execution = [], [], [], [], []
        payoff_coefficients, payoff_covariances = [], []
        day_weights = np.asarray([w[days == day].sum() for day in unique])
        effective_days = float(1 / np.sum(day_weights ** 2))
        for side in (0, 1):
            net = np.asarray([s.net[side] if horizon_seconds == 900 else s.short_net[horizon_seconds][side] for s in samples])
            y = (net > 0).astype(float)
            if not np.isfinite(net).all() or not (np.any(y == 0) and np.any(y == 1)):
                raise ValueError("Both payoff classes and finite outcomes are required.")
            beta = np.zeros(z.shape[1])
            converged = False
            for _ in range(80):
                p = sigmoid(z @ beta)
                hessian = z.T @ ((w * p * (1 - p))[:, None] * z) + ridge
                gradient = z.T @ (w * (p - y)) + ridge @ beta
                step = np.linalg.solve(hessian, gradient)
                # Backtracking uses training likelihood only.
                loss = float(np.sum(w * (np.logaddexp(0, z @ beta) - y * (z @ beta))) + .5 * beta @ ridge @ beta)
                fraction = 1.
                while fraction > 1e-7:
                    trial = beta - fraction * step
                    proposed = float(np.sum(w * (np.logaddexp(0, z @ trial) - y * (z @ trial))) + .5 * trial @ ridge @ trial)
                    if proposed <= loss + 1e-12:
                        break
                    fraction /= 2
                beta -= fraction * step
                if np.max(np.abs(fraction * step)) < 1e-8:
                    converged = True; break
            if not converged:
                raise ValueError("Training optimizer did not converge.")
            p = sigmoid(z @ beta)
            hessian = z.T @ ((w * p * (1 - p))[:, None] * z) + ridge
            scores = np.stack([np.sum((w * (y - p))[:, None] * z * (days == day)[:, None], axis=0) for day in unique])
            inverse = np.linalg.inv(hessian)
            covariance = inverse @ (scores.T @ scores) @ inverse * len(unique) / max(1, len(unique) - 1)
            conditional = [float(np.sum(w[y == k] * net[y == k]) / np.sum(w[y == k])) for k in (0, 1)]
            payoff_inverse = np.linalg.inv(z.T @ (w[:, None] * z) + ridge)
            payoff_beta = payoff_inverse @ (z.T @ (w * net))
            payoff_residual = net - z @ payoff_beta
            payoff_scores = np.stack([np.sum((w * payoff_residual)[:, None] * z * (days == day)[:, None], axis=0) for day in unique])
            payoff_cov = payoff_inverse @ (payoff_scores.T @ payoff_scores) @ payoff_inverse
            payoff_cov *= effective_days / max(1, effective_days-1)
            payoff_coefficients.append(payoff_beta)
            payoff_covariances.append(payoff_cov)
            by_day = np.asarray([np.mean(net[days == day]) for day in unique])
            target = np.asarray([s.execution_cost[side] for s in samples])
            if not np.isfinite(target).all():
                raise ValueError("Execution proxy targets must be finite.")
            execution.append(np.linalg.solve(z.T @ (w[:, None] * z) + ridge, z.T @ (w * target)))
            coef.append(beta); cov.append(covariance); means.append(conditional); ses.append(daily_se(by_day))
        return cls(feature_set, mean, scale, np.asarray(coef), np.asarray(cov), np.asarray(means), np.asarray(ses),
                   np.asarray(execution), len(samples), len(unique), max(s.label_end for s in samples), penalty, horizon_seconds,
                   np.asarray(payoff_coefficients), np.asarray(payoff_covariances), effective_days)

    def design(self, values: np.ndarray, at: datetime) -> np.ndarray:
        if timestamp(at) <= self.trained_through:
            raise ValueError("Training outcomes must precede the decision.")
        x = np.asarray(values, dtype=float)
        if x.shape != self.mean.shape or not np.isfinite(x).all():
            raise ValueError("Missing or invalid forecast input.")
        standardized = (x - self.mean) / self.scale
        if np.max(np.abs(standardized)) > 8:
            raise ValueError("Forecast outside the registered training domain.")
        return np.r_[1., standardized]

    def forecast(self, values: np.ndarray, *, at: datetime) -> dict:
        z = self.design(values, at)
        outcomes = {}
        for index, side in enumerate(("BUY", "SELL")):
            p = float(sigmoid(z @ self.coefficients[index]))
            if self.payoff_coefficients is None or self.payoff_covariance is None:
                raise ValueError("Direct conditional payoff fit is required")
            net = float(z @ self.payoff_coefficients[index])
            se = math.sqrt(max(0., float(z @ self.payoff_covariance[index] @ z)))
            from scipy.stats import t
            penalty = float(t.ppf(1-.01/8, max(1, self.effective_days-1))) * se + 1.
            outcomes[side] = {"probability_net_positive": p, "expected_net_usd": float(net),
                              "uncertainty_penalty_usd": float(penalty), "lower_utility_usd": float(net - penalty),
                              "execution_cost_proxy_usd": float(z @ self.execution_coefficients[index])}
        best = max(outcomes, key=lambda side: outcomes[side]["lower_utility_usd"])
        supported = self.training_examples >= 300 and self.effective_days >= 20 and self.horizon_seconds == 900
        proposal = best if supported and outcomes[best]["lower_utility_usd"] > 1 else "NO_TRADE"
        reasons = ["Unvalidated research estimates; no order routing."]
        if not supported:
            reasons.append("Require 300 nonoverlapping training paths, 20 independent sessions and the 15-minute decision horizon.")
        if proposal == "NO_TRADE":
            reasons.append("No supported action exceeds costs, uncertainty and the $1 minimum net edge.")
        else:
            reasons.append(f"{proposal} has the highest expected net payoff after the uncertainty penalty; shadow proposal only.")
        return {"model_version": VERSION, "feature_set": self.feature_set, "horizon_seconds": self.horizon_seconds,
                "action": "NO_TRADE", "research_action": proposal, "routing_allowed": False,
                "probability_basis": "uncalibrated_model_estimate", "forecasts": outcomes,
                "training_examples": self.training_examples, "training_days": self.training_days,
                "trained_through": self.trained_through.isoformat(), "reasons": reasons,
                "uncertainty_method": "Direct conditional net-payoff regression with day-cluster sandwich covariance and effective-day t penalty. Research only."}

    def to_dict(self) -> dict:
        return {"version": VERSION, "feature_set": self.feature_set, "feature_names": list(FEATURE_SETS[self.feature_set]),
                **{k: getattr(self, k).tolist() for k in ("mean", "scale", "coefficients", "covariance", "conditional_net", "payoff_se", "execution_coefficients", "payoff_coefficients", "payoff_covariance")},
                "effective_days": self.effective_days,
                "training_examples": self.training_examples, "training_days": self.training_days,
                "trained_through": self.trained_through.isoformat(), "penalty": self.penalty, "horizon_seconds": self.horizon_seconds}

    @classmethod
    def from_dict(cls, value: dict):
        if value.get("version") != VERSION or value.get("feature_set") not in FEATURE_SETS:
            raise ValueError("Unknown model version.")
        count = len(FEATURE_SETS[value["feature_set"]])
        if value["feature_names"] != list(FEATURE_SETS[value["feature_set"]]):
            raise ValueError("Feature ordering differs from the trained interface.")
        arrays = {}
        for key, shape in {"mean": (count,), "scale": (count,), "coefficients": (2, count + 1),
                           "covariance": (2, count + 1, count + 1), "conditional_net": (2, 2),
                           "payoff_se": (2,), "execution_coefficients": (2, count + 1),
                           "payoff_coefficients": (2, count + 1), "payoff_covariance": (2, count+1, count+1)}.items():
            arrays[key] = np.asarray(value[key], dtype=float)
            if arrays[key].shape != shape or not np.isfinite(arrays[key]).all() or np.max(np.abs(arrays[key])) > 1e8:
                raise ValueError("Malformed fitted model.")
        if (np.any(arrays["scale"] <= 0) or np.any(arrays["payoff_se"] < 0)
                or not 2 <= value["training_examples"] <= 20000 or not 1 <= value["training_days"] <= value["training_examples"]
                or value["horizon_seconds"] not in {1, 5, 30, 900} or value["penalty"] not in {.5, 1., 2.}):
            raise ValueError("Invalid model metadata.")
        if not 1 <= value["effective_days"] <= value["training_days"] + 1e-6:
            raise ValueError("Invalid effective-day support")
        for matrix in [*arrays["covariance"], *arrays["payoff_covariance"]]:
            if not np.allclose(matrix, matrix.T) or np.min(np.linalg.eigvalsh(matrix)) < -1e-7:
                raise ValueError("Invalid uncertainty covariance.")
        return cls(value["feature_set"], **arrays, training_examples=value["training_examples"], training_days=value["training_days"],
                   trained_through=timestamp(value["trained_through"]), penalty=value["penalty"], horizon_seconds=value["horizon_seconds"],
                   effective_days=value["effective_days"])
