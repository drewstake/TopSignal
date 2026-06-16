from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

_NEW_YORK_TZ = ZoneInfo("America/New_York")
_EPSILON = 1e-9

TradeDirection = str
TrendDirection = str
TimeOfDay = str
MarketRegime = str
NewsRisk = str


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    direction: TradeDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    timestamp: datetime
    account_balance: float | None = None
    current_day_pnl: float | None = None
    max_daily_loss: float | None = None
    trailing_drawdown: float | None = None


@dataclass(frozen=True)
class MarketContext:
    current_price: float
    high_of_day: float | None = None
    low_of_day: float | None = None
    previous_day_high: float | None = None
    previous_day_low: float | None = None
    previous_close: float | None = None
    open_price: float | None = None
    vwap: float | None = None
    anchored_vwap: float | None = None
    volume_profile_poc: float | None = None
    value_area_high: float | None = None
    value_area_low: float | None = None
    ema21_5m: float | None = None
    ema21_15m: float | None = None
    ema21_1h: float | None = None
    ema21_4h: float | None = None
    ma200_5m: float | None = None
    ma200_15m: float | None = None
    ma200_1h: float | None = None
    ma200_4h: float | None = None
    trend5m: TrendDirection = "neutral"
    trend15m: TrendDirection = "neutral"
    trend1h: TrendDirection = "neutral"
    trend4h: TrendDirection = "neutral"
    atr1m: float | None = None
    atr5m: float | None = None
    atr15m: float | None = None
    atr1h: float | None = None
    average_daily_range: float | None = None
    current_day_range: float | None = None
    current_volume: float | None = None
    average_volume_at_time: float | None = None
    relative_volume: float | None = None
    cumulative_delta: float | None = None
    time_of_day: TimeOfDay = "overnight"
    market_regime: MarketRegime = "unknown"
    news_risk: NewsRisk = "low"
    es_trend: TrendDirection | None = None
    nq_trend: TrendDirection | None = None
    vix_trend: TrendDirection | None = None
    ten_year_yield_trend: TrendDirection | None = None
    nvda_trend: TrendDirection | None = None
    smh_trend: TrendDirection | None = None


@dataclass(frozen=True)
class TradePlanFeatures:
    risk_points: float
    reward_points: float
    risk_reward_ratio: float | None
    breakeven_win_rate: float | None
    is_long: bool
    is_short: bool
    price_above_vwap: bool | None
    price_below_vwap: bool | None
    entry_distance_from_vwap_points: float | None
    entry_distance_from_vwap_atr: float | None
    vwap_supports_direction: bool | None
    distance_from_high_of_day: float | None
    distance_from_low_of_day: float | None
    distance_from_previous_day_high: float | None
    distance_from_previous_day_low: float | None
    entry_near_high_of_day: bool
    entry_near_low_of_day: bool
    take_profit_blocked_by_high_of_day: bool
    take_profit_blocked_by_low_of_day: bool
    stop_atr_multiple: float | None
    target_atr_multiple: float | None
    is_stop_too_tight: bool
    is_stop_too_wide: bool
    is_target_realistic: bool
    trend_alignment_score: int
    aligned_timeframes: int
    conflicting_timeframes: int
    higher_timeframe_conflict: bool
    stop_behind_structure: bool
    entry_chasing: bool
    has_room_to_target: bool
    bad_location: bool
    max_loss_risk_percent: float | None
    daily_loss_danger: bool | None
    should_reduce_size: bool | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "risk_points": _round_optional(self.risk_points),
            "reward_points": _round_optional(self.reward_points),
            "risk_reward_ratio": _round_optional(self.risk_reward_ratio),
            "breakeven_win_rate": _round_optional(self.breakeven_win_rate),
            "is_long": self.is_long,
            "is_short": self.is_short,
            "price_above_vwap": self.price_above_vwap,
            "price_below_vwap": self.price_below_vwap,
            "entry_distance_from_vwap_points": _round_optional(self.entry_distance_from_vwap_points),
            "entry_distance_from_vwap_atr": _round_optional(self.entry_distance_from_vwap_atr),
            "vwap_supports_direction": self.vwap_supports_direction,
            "distance_from_high_of_day": _round_optional(self.distance_from_high_of_day),
            "distance_from_low_of_day": _round_optional(self.distance_from_low_of_day),
            "distance_from_previous_day_high": _round_optional(self.distance_from_previous_day_high),
            "distance_from_previous_day_low": _round_optional(self.distance_from_previous_day_low),
            "entry_near_high_of_day": self.entry_near_high_of_day,
            "entry_near_low_of_day": self.entry_near_low_of_day,
            "take_profit_blocked_by_high_of_day": self.take_profit_blocked_by_high_of_day,
            "take_profit_blocked_by_low_of_day": self.take_profit_blocked_by_low_of_day,
            "stop_atr_multiple": _round_optional(self.stop_atr_multiple),
            "target_atr_multiple": _round_optional(self.target_atr_multiple),
            "is_stop_too_tight": self.is_stop_too_tight,
            "is_stop_too_wide": self.is_stop_too_wide,
            "is_target_realistic": self.is_target_realistic,
            "trend_alignment_score": self.trend_alignment_score,
            "aligned_timeframes": self.aligned_timeframes,
            "conflicting_timeframes": self.conflicting_timeframes,
            "higher_timeframe_conflict": self.higher_timeframe_conflict,
            "stop_behind_structure": self.stop_behind_structure,
            "entry_chasing": self.entry_chasing,
            "has_room_to_target": self.has_room_to_target,
            "bad_location": self.bad_location,
            "max_loss_risk_percent": _round_optional(self.max_loss_risk_percent),
            "daily_loss_danger": self.daily_loss_danger,
            "should_reduce_size": self.should_reduce_size,
        }


@dataclass(frozen=True)
class ScoringWeights:
    risk_reward: int = 20
    vwap_location: int = 20
    multi_timeframe_trend: int = 20
    stop_target_quality: int = 15
    volatility_atr_fit: int = 10
    time_regime: int = 10
    account_news_penalty: int = 5


@dataclass(frozen=True)
class TradeScoringConfig:
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    stop_too_tight_atr: float = 0.5
    stop_reasonable_min_atr: float = 0.8
    stop_reasonable_max_atr: float = 2.0
    stop_too_wide_atr: float = 2.5
    target_unrealistic_atr: float = 4.0
    chasing_vwap_atr: float = 2.0
    near_day_extreme_atr: float = 0.5
    high_risk_percent: float = 1.5


@dataclass(frozen=True)
class TradeEvaluationResult:
    total_score: int
    grade: str
    decision: str
    confidence: str
    summary: str
    reasons: list[str]
    warnings: list[str]
    positives: list[str]
    suggested_adjustments: list[str]
    features: TradePlanFeatures
    category_scores: dict[str, int]

    def to_payload(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "score": self.total_score,
            "grade": self.grade,
            "decision": self.decision,
            "confidence": self.confidence,
            "summary": self.summary,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "positives": self.positives,
            "suggested_adjustments": self.suggested_adjustments,
            "features": self.features.to_payload(),
            "category_scores": self.category_scores,
        }


class FeatureCalculator:
    def __init__(self, config: TradeScoringConfig | None = None):
        self.config = config or TradeScoringConfig()

    def calculate(self, plan: TradePlan, context: MarketContext) -> TradePlanFeatures:
        direction = _normalize_direction(plan.direction)
        is_long = direction == "long"
        is_short = direction == "short"
        risk_points = _directional_risk(plan, is_long=is_long)
        reward_points = _directional_reward(plan, is_long=is_long)
        risk_reward_ratio = reward_points / risk_points if risk_points > _EPSILON else None
        breakeven_win_rate = 100 / (1 + risk_reward_ratio) if risk_reward_ratio and risk_reward_ratio > 0 else None
        atr = _positive_float(context.atr5m)
        level_threshold = _level_threshold(plan.entry_price, atr, self.config)

        vwap = _positive_or_zero_float(context.vwap)
        price_above_vwap = plan.entry_price > vwap if vwap is not None else None
        price_below_vwap = plan.entry_price < vwap if vwap is not None else None
        entry_distance_from_vwap_points = plan.entry_price - vwap if vwap is not None else None
        entry_distance_from_vwap_atr = (
            entry_distance_from_vwap_points / atr
            if entry_distance_from_vwap_points is not None and atr is not None and atr > _EPSILON
            else None
        )
        vwap_supports_direction = None
        if vwap is not None:
            vwap_supports_direction = price_above_vwap if is_long else price_below_vwap

        distance_from_high_of_day = (
            context.high_of_day - plan.entry_price if context.high_of_day is not None else None
        )
        distance_from_low_of_day = (
            plan.entry_price - context.low_of_day if context.low_of_day is not None else None
        )
        distance_from_previous_day_high = (
            context.previous_day_high - plan.entry_price if context.previous_day_high is not None else None
        )
        distance_from_previous_day_low = (
            plan.entry_price - context.previous_day_low if context.previous_day_low is not None else None
        )
        entry_near_high_of_day = (
            is_long
            and distance_from_high_of_day is not None
            and 0 <= distance_from_high_of_day <= level_threshold
        )
        entry_near_low_of_day = (
            is_short
            and distance_from_low_of_day is not None
            and 0 <= distance_from_low_of_day <= level_threshold
        )
        take_profit_blocked_by_high_of_day = (
            is_long
            and context.high_of_day is not None
            and plan.entry_price < context.high_of_day < plan.take_profit
            and context.market_regime not in {"breakout", "trend"}
        )
        take_profit_blocked_by_low_of_day = (
            is_short
            and context.low_of_day is not None
            and plan.take_profit < context.low_of_day < plan.entry_price
            and context.market_regime not in {"breakout", "trend"}
        )

        stop_atr_multiple = risk_points / atr if atr is not None and atr > _EPSILON and risk_points > 0 else None
        target_atr_multiple = reward_points / atr if atr is not None and atr > _EPSILON and reward_points > 0 else None
        is_stop_too_tight = stop_atr_multiple is not None and stop_atr_multiple < self.config.stop_too_tight_atr
        is_stop_too_wide = stop_atr_multiple is not None and stop_atr_multiple > self.config.stop_too_wide_atr
        is_target_realistic = (
            target_atr_multiple is None
            or target_atr_multiple <= self.config.target_unrealistic_atr
            or context.market_regime in {"trend", "breakout"}
        )

        trend_alignment_score, aligned_timeframes, conflicting_timeframes, higher_timeframe_conflict = (
            _trend_alignment(plan=plan, context=context)
        )
        stop_behind_structure = _stop_behind_structure(plan=plan, context=context, is_long=is_long)
        entry_chasing = _entry_chasing(
            is_long=is_long,
            entry_distance_from_vwap_atr=entry_distance_from_vwap_atr,
            entry_near_high_of_day=entry_near_high_of_day,
            entry_near_low_of_day=entry_near_low_of_day,
            config=self.config,
        )
        has_room_to_target = _has_room_to_target(plan=plan, context=context, is_long=is_long)
        bad_location = _bad_location(
            is_long=is_long,
            context=context,
            vwap_supports_direction=vwap_supports_direction,
            entry_chasing=entry_chasing,
            entry_near_high_of_day=entry_near_high_of_day,
            entry_near_low_of_day=entry_near_low_of_day,
            take_profit_blocked_by_high_of_day=take_profit_blocked_by_high_of_day,
            take_profit_blocked_by_low_of_day=take_profit_blocked_by_low_of_day,
        )

        max_loss = risk_points * max(float(plan.quantity), 0.0) if risk_points > 0 else 0.0
        max_loss_risk_percent = (
            (max_loss / float(plan.account_balance)) * 100
            if plan.account_balance is not None and float(plan.account_balance) > _EPSILON and max_loss > 0
            else None
        )
        daily_loss_danger = _daily_loss_danger(plan=plan, max_loss=max_loss)
        should_reduce_size = (
            True
            if daily_loss_danger is True
            or (max_loss_risk_percent is not None and max_loss_risk_percent > self.config.high_risk_percent)
            else None
        )

        return TradePlanFeatures(
            risk_points=max(0.0, risk_points),
            reward_points=max(0.0, reward_points),
            risk_reward_ratio=risk_reward_ratio,
            breakeven_win_rate=breakeven_win_rate,
            is_long=is_long,
            is_short=is_short,
            price_above_vwap=price_above_vwap,
            price_below_vwap=price_below_vwap,
            entry_distance_from_vwap_points=entry_distance_from_vwap_points,
            entry_distance_from_vwap_atr=entry_distance_from_vwap_atr,
            vwap_supports_direction=vwap_supports_direction,
            distance_from_high_of_day=distance_from_high_of_day,
            distance_from_low_of_day=distance_from_low_of_day,
            distance_from_previous_day_high=distance_from_previous_day_high,
            distance_from_previous_day_low=distance_from_previous_day_low,
            entry_near_high_of_day=entry_near_high_of_day,
            entry_near_low_of_day=entry_near_low_of_day,
            take_profit_blocked_by_high_of_day=take_profit_blocked_by_high_of_day,
            take_profit_blocked_by_low_of_day=take_profit_blocked_by_low_of_day,
            stop_atr_multiple=stop_atr_multiple,
            target_atr_multiple=target_atr_multiple,
            is_stop_too_tight=is_stop_too_tight,
            is_stop_too_wide=is_stop_too_wide,
            is_target_realistic=is_target_realistic,
            trend_alignment_score=trend_alignment_score,
            aligned_timeframes=aligned_timeframes,
            conflicting_timeframes=conflicting_timeframes,
            higher_timeframe_conflict=higher_timeframe_conflict,
            stop_behind_structure=stop_behind_structure,
            entry_chasing=entry_chasing,
            has_room_to_target=has_room_to_target,
            bad_location=bad_location,
            max_loss_risk_percent=max_loss_risk_percent,
            daily_loss_danger=daily_loss_danger,
            should_reduce_size=should_reduce_size,
        )


class TradeScoringEngine:
    def __init__(self, config: TradeScoringConfig | None = None):
        self.config = config or TradeScoringConfig()

    def score(self, plan: TradePlan, context: MarketContext, features: TradePlanFeatures) -> TradeEvaluationResult:
        category_scores = {
            "risk_reward": self._score_risk_reward(features),
            "vwap_location": self._score_vwap_location(context, features),
            "multi_timeframe_trend": self._score_trend(features),
            "stop_target_quality": self._score_stop_target_quality(features),
            "volatility_atr_fit": self._score_atr_fit(features),
            "time_regime": self._score_time_regime(context),
            "account_news_penalty": self._score_account_news(context, features),
        }
        total_score = max(0, min(100, int(round(sum(category_scores.values())))))
        total_score = _apply_score_caps(total_score, context=context, features=features)
        warnings = self._warnings(plan=plan, context=context, features=features)
        positives = self._positives(context=context, features=features)
        reasons = self._reasons(context=context, features=features, category_scores=category_scores)
        suggested_adjustments = self._suggested_adjustments(context=context, features=features)
        grade = _grade_for_score(total_score)
        decision = _decision_for_score(total_score, features=features, context=context)
        confidence = _confidence_for_score(total_score, warnings=warnings, features=features)
        summary = _summary(
            plan=plan,
            context=context,
            features=features,
            score=total_score,
            grade=grade,
            decision=decision,
        )

        return TradeEvaluationResult(
            total_score=total_score,
            grade=grade,
            decision=decision,
            confidence=confidence,
            summary=summary,
            reasons=reasons,
            warnings=warnings,
            positives=positives,
            suggested_adjustments=suggested_adjustments,
            features=features,
            category_scores=category_scores,
        )

    def _score_risk_reward(self, features: TradePlanFeatures) -> int:
        weight = self.config.weights.risk_reward
        rr = features.risk_reward_ratio
        if features.risk_points <= 0 or features.reward_points <= 0 or rr is None:
            return 0
        if rr < 1:
            return int(round(weight * 0.15))
        if rr < 1.5:
            return int(round(weight * 0.45))
        if rr < 2:
            return int(round(weight * 0.7))
        if rr < 3:
            return int(round(weight * 0.85))
        return weight if features.is_target_realistic else int(round(weight * 0.75))

    def _score_vwap_location(self, context: MarketContext, features: TradePlanFeatures) -> int:
        weight = self.config.weights.vwap_location
        score = 8 if features.vwap_supports_direction is None else 6
        if features.vwap_supports_direction is True:
            score += 8
        elif features.vwap_supports_direction is False and context.market_regime == "reversal":
            score += 4
        if not features.entry_chasing:
            score += 4
        if features.take_profit_blocked_by_high_of_day or features.take_profit_blocked_by_low_of_day:
            score -= 4
        if features.bad_location:
            score -= 3
        return _clamp_score(score, weight)

    def _score_trend(self, features: TradePlanFeatures) -> int:
        weight = self.config.weights.multi_timeframe_trend
        score = round(weight * (features.trend_alignment_score / 100))
        if features.higher_timeframe_conflict and features.aligned_timeframes <= 2:
            score = min(score, 12)
        return _clamp_score(score, weight)

    def _score_stop_target_quality(self, features: TradePlanFeatures) -> int:
        weight = self.config.weights.stop_target_quality
        if features.risk_points <= 0 or features.reward_points <= 0:
            return 0
        score = weight
        if features.is_stop_too_tight:
            score -= 5
        if features.is_stop_too_wide:
            score -= 3
        if not features.is_target_realistic:
            score -= 5
        if not features.stop_behind_structure:
            score -= 3
        if not features.has_room_to_target:
            score -= 4
        return _clamp_score(score, weight)

    def _score_atr_fit(self, features: TradePlanFeatures) -> int:
        weight = self.config.weights.volatility_atr_fit
        if features.stop_atr_multiple is None or features.target_atr_multiple is None:
            return int(round(weight * 0.55))
        score = weight
        if features.is_stop_too_tight:
            score -= 4
        elif features.stop_atr_multiple < self.config.stop_reasonable_min_atr:
            score -= 2
        if features.is_stop_too_wide:
            score -= 3
        if not features.is_target_realistic:
            score -= 3
        return _clamp_score(score, weight)

    def _score_time_regime(self, context: MarketContext) -> int:
        weight = self.config.weights.time_regime
        regime = context.market_regime
        if regime == "chop":
            score = 2
        elif regime in {"trend", "breakout"}:
            score = 9
        elif regime == "reversal":
            score = 7
        elif regime == "range":
            score = 6
        else:
            score = 5

        if context.time_of_day == "lunch":
            score -= 3
        elif context.time_of_day in {"open", "ny_am", "power_hour"}:
            score += 1
        elif context.time_of_day in {"overnight", "premarket"}:
            score -= 1
        return _clamp_score(score, weight)

    def _score_account_news(self, context: MarketContext, features: TradePlanFeatures) -> int:
        weight = self.config.weights.account_news_penalty
        score = weight
        if context.news_risk == "high":
            score -= 5
        elif context.news_risk == "medium":
            score -= 2
        if features.daily_loss_danger:
            score -= 4
        elif features.should_reduce_size:
            score -= 2
        return _clamp_score(score, weight)

    def _warnings(self, *, plan: TradePlan, context: MarketContext, features: TradePlanFeatures) -> list[str]:
        warnings: list[str] = []
        if features.risk_points <= 0:
            warnings.append("Stop loss is not on the correct side of the entry.")
        if features.reward_points <= 0:
            warnings.append("Take profit is not on the reward side of the entry.")
        if features.risk_reward_ratio is not None and features.risk_reward_ratio < 1:
            warnings.append(f"Risk/reward is only {features.risk_reward_ratio:.2f}R, which is below 1.0R.")
        if features.vwap_supports_direction is False:
            side = "below" if features.is_long else "above"
            warnings.append(f"{plan.direction.title()} entry is {side} VWAP, so VWAP does not support the direction.")
        if features.entry_chasing:
            warnings.append("Entry is extended from VWAP or near the day extreme, which may be chasing.")
        if features.is_stop_too_tight:
            warnings.append("Stop is less than 0.5 ATR, so normal noise could stop the trade out.")
        if features.is_stop_too_wide:
            warnings.append("Stop is wider than 2.5 ATR, which makes the setup expensive to invalidate.")
        if not features.is_target_realistic:
            warnings.append("Target is more than 4 ATR away outside a trend or breakout regime.")
        if features.take_profit_blocked_by_high_of_day:
            warnings.append("Take profit is above high-of-day resistance without breakout context.")
        if features.take_profit_blocked_by_low_of_day:
            warnings.append("Take profit is below low-of-day support without breakdown context.")
        if features.higher_timeframe_conflict:
            warnings.append("Higher timeframe trend conflicts with the proposed direction.")
        if context.market_regime == "chop":
            warnings.append("Market regime is chop, so signal quality is penalized.")
        if context.time_of_day == "lunch":
            warnings.append("Lunch session usually has lower follow-through unless the setup is strong.")
        if context.news_risk == "high":
            warnings.append("News risk is high; avoid new discretionary entries around major releases.")
        elif context.news_risk == "medium":
            warnings.append("News risk is medium; size and timing should be more conservative.")
        if features.daily_loss_danger:
            warnings.append("The planned loss could push the account near or through the daily loss limit.")
        elif features.should_reduce_size:
            warnings.append("The planned loss is large relative to account risk; consider reducing size.")
        return _dedupe(warnings)

    def _positives(self, *, context: MarketContext, features: TradePlanFeatures) -> list[str]:
        positives: list[str] = []
        if features.risk_reward_ratio is not None and features.risk_reward_ratio >= 2:
            positives.append(f"Risk/reward is {features.risk_reward_ratio:.2f}R.")
        elif features.risk_reward_ratio is not None and features.risk_reward_ratio >= 1.5:
            positives.append(f"Risk/reward is acceptable at {features.risk_reward_ratio:.2f}R.")
        if features.vwap_supports_direction:
            positives.append("VWAP supports the proposed direction.")
        if features.trend_alignment_score >= 75:
            positives.append("Most monitored timeframes agree with the trade direction.")
        elif features.aligned_timeframes >= 2:
            positives.append("The lower timeframes support the proposed direction.")
        if features.stop_behind_structure and not features.is_stop_too_tight:
            positives.append("Stop placement gives the trade a defined invalidation area.")
        if features.has_room_to_target:
            positives.append("No nearby major level blocks the path to target.")
        if context.market_regime in {"trend", "breakout"}:
            positives.append(f"Market regime is {context.market_regime}, which favors continuation setups.")
        return _dedupe(positives)

    def _reasons(
        self,
        *,
        context: MarketContext,
        features: TradePlanFeatures,
        category_scores: dict[str, int],
    ) -> list[str]:
        rr_text = (
            f"{features.risk_reward_ratio:.2f}R"
            if features.risk_reward_ratio is not None
            else "invalid"
        )
        return [
            f"Risk/reward scored {category_scores['risk_reward']}/{self.config.weights.risk_reward} with {rr_text}.",
            (
                f"VWAP/location scored {category_scores['vwap_location']}/{self.config.weights.vwap_location}; "
                f"entry distance from VWAP is {_format_optional_ratio(features.entry_distance_from_vwap_atr)} ATR."
            ),
            (
                f"Trend alignment is {features.trend_alignment_score}/100 with "
                f"{features.aligned_timeframes} aligned and {features.conflicting_timeframes} conflicting timeframe(s)."
            ),
            f"Stop/target quality scored {category_scores['stop_target_quality']}/{self.config.weights.stop_target_quality}.",
            f"Regime is {context.market_regime} during {context.time_of_day} with {context.news_risk} news risk.",
        ]

    def _suggested_adjustments(self, *, context: MarketContext, features: TradePlanFeatures) -> list[str]:
        suggestions: list[str] = []
        if features.risk_reward_ratio is not None and features.risk_reward_ratio < 1.5:
            suggestions.append("Improve the plan to at least 1.5R or skip the setup.")
        if features.vwap_supports_direction is False:
            suggestions.append("Wait for price to reclaim VWAP in the trade direction or reframe it as a reversal setup.")
        if features.entry_chasing:
            suggestions.append("Wait for a pullback closer to VWAP, the 21 EMA, or the invalidation level.")
        if features.is_stop_too_tight:
            suggestions.append("Widen the stop beyond normal ATR noise or reduce size to keep risk constant.")
        if features.is_stop_too_wide:
            suggestions.append("Move entry closer to invalidation instead of accepting a wide stop.")
        if not features.is_target_realistic:
            suggestions.append("Move the target inside a 4 ATR path unless breakout volume confirms expansion.")
        if features.take_profit_blocked_by_high_of_day:
            suggestions.append("Place take profit before high of day unless breakout confirmation appears.")
        if features.take_profit_blocked_by_low_of_day:
            suggestions.append("Place take profit before low of day unless breakdown confirmation appears.")
        if context.market_regime == "chop":
            suggestions.append("Avoid trend entries in chop; wait for range edges or a clean regime shift.")
        if context.news_risk == "high":
            suggestions.append("Stand aside until the news window passes and spreads/volatility normalize.")
        if features.daily_loss_danger or features.should_reduce_size:
            suggestions.append("Reduce size or stop trading for the session if account risk limits are close.")
        return _dedupe(suggestions)


class TradePlanEvaluator:
    def __init__(self, config: TradeScoringConfig | None = None):
        self.config = config or TradeScoringConfig()
        self.feature_calculator = FeatureCalculator(self.config)
        self.scoring_engine = TradeScoringEngine(self.config)

    def evaluate(self, plan: TradePlan, context: MarketContext) -> TradeEvaluationResult:
        _validate_plan(plan)
        normalized_plan = TradePlan(
            symbol=plan.symbol,
            direction=_normalize_direction(plan.direction),
            entry_price=float(plan.entry_price),
            stop_loss=float(plan.stop_loss),
            take_profit=float(plan.take_profit),
            quantity=float(plan.quantity),
            timestamp=_ensure_aware_datetime(plan.timestamp),
            account_balance=plan.account_balance,
            current_day_pnl=plan.current_day_pnl,
            max_daily_loss=plan.max_daily_loss,
            trailing_drawdown=plan.trailing_drawdown,
        )
        features = self.feature_calculator.calculate(normalized_plan, context)
        return self.scoring_engine.score(normalized_plan, context, features)


def build_market_context_from_ohlcv(
    candles: Sequence[Mapping[str, Any]],
    *,
    current_price: float | None = None,
    timestamp: datetime | None = None,
    market_regime: MarketRegime = "unknown",
    news_risk: NewsRisk = "low",
) -> MarketContext | None:
    rows = _normalize_candles(candles)
    if not rows:
        return None
    latest = rows[-1]
    latest_timestamp = timestamp or latest["timestamp"]
    latest_price = float(current_price if current_price is not None else latest["close"])
    session_rows = _session_rows(rows, latest_timestamp)
    previous_rows = [row for row in rows if row not in session_rows]
    high_of_day = max((row["high"] for row in session_rows), default=latest["high"])
    low_of_day = min((row["low"] for row in session_rows), default=latest["low"])
    previous_day_high = max((row["high"] for row in previous_rows), default=None)
    previous_day_low = min((row["low"] for row in previous_rows), default=None)
    previous_close = previous_rows[-1]["close"] if previous_rows else (rows[-2]["close"] if len(rows) >= 2 else None)
    session_open = session_rows[0]["open"] if session_rows else latest["open"]
    session_vwap = _vwap(session_rows) or _vwap(rows)
    true_ranges = _true_ranges(rows)
    atr5m = _average(true_ranges[-14:]) if true_ranges else None
    closes = [row["close"] for row in rows]
    ema21_5m = _ema(closes, 21)
    ma200_5m = _sma(closes, min(200, len(closes))) if closes else None
    current_day_range = high_of_day - low_of_day if high_of_day is not None and low_of_day is not None else None
    latest_volume = latest["volume"]
    average_volume = _average([row["volume"] for row in rows[-21:-1]]) if len(rows) > 1 else None
    relative_volume = latest_volume / average_volume if average_volume and average_volume > _EPSILON else None

    return MarketContext(
        current_price=latest_price,
        high_of_day=high_of_day,
        low_of_day=low_of_day,
        previous_day_high=previous_day_high,
        previous_day_low=previous_day_low,
        previous_close=previous_close,
        open_price=session_open,
        vwap=session_vwap,
        ema21_5m=ema21_5m,
        ema21_15m=_ema(_sample_every(closes, 3), 21),
        ema21_1h=_ema(_sample_every(closes, 12), 21),
        ema21_4h=_ema(_sample_every(closes, 48), 21),
        ma200_5m=ma200_5m,
        ma200_15m=_sma(_sample_every(closes, 3), 200),
        ma200_1h=_sma(_sample_every(closes, 12), 200),
        ma200_4h=_sma(_sample_every(closes, 48), 200),
        trend5m=_classify_trend(closes, 12, atr5m),
        trend15m=_classify_trend(closes, 36, atr5m),
        trend1h=_classify_trend(closes, 144, atr5m),
        trend4h=_classify_trend(closes, 288, atr5m),
        atr5m=atr5m,
        current_day_range=current_day_range,
        current_volume=latest_volume,
        average_volume_at_time=average_volume,
        relative_volume=relative_volume,
        time_of_day=_classify_time_of_day(latest_timestamp),
        market_regime=market_regime,
        news_risk=news_risk,
    )


def _validate_plan(plan: TradePlan) -> None:
    if _normalize_direction(plan.direction) not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    if not str(plan.symbol).strip():
        raise ValueError("symbol is required")
    for name in ["entry_price", "stop_loss", "take_profit", "quantity"]:
        value = getattr(plan, name)
        if not isinstance(value, (int, float)) or float(value) != float(value):
            raise ValueError(f"{name} must be a finite number")
    if float(plan.quantity) <= 0:
        raise ValueError("quantity must be greater than zero")


def _normalize_direction(direction: str) -> str:
    normalized = str(direction).strip().lower()
    if normalized == "buy":
        return "long"
    if normalized == "sell":
        return "short"
    return normalized


def _directional_risk(plan: TradePlan, *, is_long: bool) -> float:
    return plan.entry_price - plan.stop_loss if is_long else plan.stop_loss - plan.entry_price


def _directional_reward(plan: TradePlan, *, is_long: bool) -> float:
    return plan.take_profit - plan.entry_price if is_long else plan.entry_price - plan.take_profit


def _positive_float(value: float | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if parsed > _EPSILON and parsed == parsed else None


def _positive_or_zero_float(value: float | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if parsed >= 0 and parsed == parsed else None


def _level_threshold(entry_price: float, atr: float | None, config: TradeScoringConfig) -> float:
    atr_threshold = atr * config.near_day_extreme_atr if atr is not None else 0.0
    price_threshold = abs(entry_price) * 0.00075
    return max(atr_threshold, price_threshold, 0.01)


def _trend_alignment(plan: TradePlan, context: MarketContext) -> tuple[int, int, int, bool]:
    desired = "bullish" if _normalize_direction(plan.direction) == "long" else "bearish"
    weights = [
        ("5m", context.trend5m, 15),
        ("15m", context.trend15m, 30),
        ("1h", context.trend1h, 35),
        ("4h", context.trend4h, 20),
    ]
    score = 0.0
    aligned = 0
    conflicting = 0
    higher_conflict = False
    for name, raw_trend, weight in weights:
        trend = _normalize_trend(raw_trend)
        if trend == desired:
            score += weight
            aligned += 1
        elif trend == "neutral" or trend == "unknown":
            score += weight * 0.45
        else:
            conflicting += 1
            if name in {"1h", "4h"}:
                higher_conflict = True
    return int(round(max(0, min(100, score)))), aligned, conflicting, higher_conflict


def _normalize_trend(trend: str | None) -> str:
    normalized = str(trend or "neutral").strip().lower()
    if normalized in {"bull", "up", "long"}:
        return "bullish"
    if normalized in {"bear", "down", "short"}:
        return "bearish"
    if normalized in {"bullish", "bearish", "neutral"}:
        return normalized
    return "unknown"


def _stop_behind_structure(*, plan: TradePlan, context: MarketContext, is_long: bool) -> bool:
    if is_long:
        anchors = [
            context.vwap,
            context.value_area_low,
            context.volume_profile_poc,
            context.low_of_day,
            context.previous_day_low,
        ]
        defined_anchors = [float(anchor) for anchor in anchors if anchor is not None]
        if defined_anchors:
            return any(plan.stop_loss <= anchor for anchor in defined_anchors)
        return plan.stop_loss < plan.entry_price

    anchors = [
        context.vwap,
        context.value_area_high,
        context.volume_profile_poc,
        context.high_of_day,
        context.previous_day_high,
    ]
    defined_anchors = [float(anchor) for anchor in anchors if anchor is not None]
    if defined_anchors:
        return any(plan.stop_loss >= anchor for anchor in defined_anchors)
    return plan.stop_loss > plan.entry_price


def _entry_chasing(
    *,
    is_long: bool,
    entry_distance_from_vwap_atr: float | None,
    entry_near_high_of_day: bool,
    entry_near_low_of_day: bool,
    config: TradeScoringConfig,
) -> bool:
    if entry_distance_from_vwap_atr is not None:
        if is_long and entry_distance_from_vwap_atr > config.chasing_vwap_atr:
            return True
        if not is_long and entry_distance_from_vwap_atr < -config.chasing_vwap_atr:
            return True
    return entry_near_high_of_day if is_long else entry_near_low_of_day


def _has_room_to_target(*, plan: TradePlan, context: MarketContext, is_long: bool) -> bool:
    if is_long:
        blockers = [
            context.high_of_day,
            context.previous_day_high,
            context.value_area_high,
            context.volume_profile_poc,
        ]
        return not any(level is not None and plan.entry_price < float(level) < plan.take_profit for level in blockers)
    blockers = [
        context.low_of_day,
        context.previous_day_low,
        context.value_area_low,
        context.volume_profile_poc,
    ]
    return not any(level is not None and plan.take_profit < float(level) < plan.entry_price for level in blockers)


def _bad_location(
    *,
    is_long: bool,
    context: MarketContext,
    vwap_supports_direction: bool | None,
    entry_chasing: bool,
    entry_near_high_of_day: bool,
    entry_near_low_of_day: bool,
    take_profit_blocked_by_high_of_day: bool,
    take_profit_blocked_by_low_of_day: bool,
) -> bool:
    continuation_regime = context.market_regime in {"trend", "breakout"}
    reversal_regime = context.market_regime == "reversal"
    if vwap_supports_direction is False and not reversal_regime:
        return True
    if entry_chasing and not continuation_regime:
        return True
    if is_long and entry_near_high_of_day and take_profit_blocked_by_high_of_day:
        return True
    if not is_long and entry_near_low_of_day and take_profit_blocked_by_low_of_day:
        return True
    return False


def _daily_loss_danger(*, plan: TradePlan, max_loss: float) -> bool | None:
    if plan.max_daily_loss is None or float(plan.max_daily_loss) <= _EPSILON:
        return None
    current_day_pnl = float(plan.current_day_pnl or 0.0)
    projected_pnl = current_day_pnl - max_loss
    return projected_pnl <= -abs(float(plan.max_daily_loss)) * 0.9


def _grade_for_score(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _decision_for_score(score: int, *, features: TradePlanFeatures, context: MarketContext) -> str:
    if (
        score < 55
        or features.risk_points <= 0
        or features.reward_points <= 0
        or (features.risk_reward_ratio is not None and features.risk_reward_ratio < 1)
        or context.news_risk == "high"
        or features.daily_loss_danger
    ):
        return "avoid"
    if context.market_regime == "chop":
        return "wait"
    if score >= 70 and not features.entry_chasing and not features.bad_location:
        return "take"
    return "wait"


def _confidence_for_score(score: int, *, warnings: list[str], features: TradePlanFeatures) -> str:
    if score >= 80 and len(warnings) <= 2 and not features.higher_timeframe_conflict:
        return "high"
    if score < 55 or len(warnings) >= 5:
        return "low"
    return "medium"


def _summary(
    *,
    plan: TradePlan,
    context: MarketContext,
    features: TradePlanFeatures,
    score: int,
    grade: str,
    decision: str,
) -> str:
    direction = _normalize_direction(plan.direction)
    rr_text = f"{features.risk_reward_ratio:.2f}R" if features.risk_reward_ratio is not None else "invalid R:R"
    if decision == "take":
        lead = f"{grade} {direction} plan scores {score}/100 with {rr_text}."
    elif decision == "wait":
        lead = f"{grade} {direction} plan scores {score}/100, but waiting improves the setup."
    else:
        lead = f"{grade} {direction} plan scores {score}/100 and should be avoided."

    context_bits: list[str] = []
    if features.vwap_supports_direction is True:
        context_bits.append("VWAP supports direction")
    elif features.vwap_supports_direction is False:
        context_bits.append("VWAP conflicts")
    if features.trend_alignment_score >= 75:
        context_bits.append("timeframes mostly align")
    elif features.higher_timeframe_conflict:
        context_bits.append("higher timeframes conflict")
    if context.market_regime != "unknown":
        context_bits.append(f"regime is {context.market_regime}")
    if features.entry_chasing:
        context_bits.append("entry is extended")
    return f"{lead} {'; '.join(context_bits)}."


def _clamp_score(value: float | int, max_value: int) -> int:
    return max(0, min(max_value, int(round(value))))


def _apply_score_caps(score: int, *, context: MarketContext, features: TradePlanFeatures) -> int:
    capped = score
    if features.risk_points <= 0 or features.reward_points <= 0:
        capped = min(capped, 39)
    if features.risk_reward_ratio is not None and features.risk_reward_ratio < 1:
        capped = min(capped, 54)
    if context.market_regime == "chop":
        capped = min(capped, 69)
    if context.news_risk == "high":
        capped = min(capped, 60)
    if features.daily_loss_danger:
        capped = min(capped, 54)
    return capped


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _format_optional_ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _dedupe(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _ensure_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _normalize_candles(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        try:
            timestamp = candle.get("timestamp")
            parsed_timestamp = _parse_datetime(timestamp)
            row = {
                "timestamp": parsed_timestamp,
                "open": float(candle.get("open")),
                "high": float(candle.get("high")),
                "low": float(candle.get("low")),
                "close": float(candle.get("close")),
                "volume": float(candle.get("volume", 0) or 0),
            }
        except (TypeError, ValueError):
            continue
        if row["high"] < row["low"]:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: row["timestamp"])


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _ensure_aware_datetime(value)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return _ensure_aware_datetime(datetime.fromisoformat(normalized))
    raise ValueError("timestamp must be datetime or ISO string")


def _session_rows(rows: list[dict[str, Any]], latest_timestamp: datetime) -> list[dict[str, Any]]:
    latest_local_date = latest_timestamp.astimezone(_NEW_YORK_TZ).date()
    session = [row for row in rows if row["timestamp"].astimezone(_NEW_YORK_TZ).date() == latest_local_date]
    return session or rows


def _vwap(rows: Sequence[dict[str, Any]]) -> float | None:
    total_volume = sum(max(row["volume"], 0.0) for row in rows)
    if total_volume <= _EPSILON:
        return None
    total_price_volume = 0.0
    for row in rows:
        typical_price = (row["high"] + row["low"] + row["close"]) / 3
        total_price_volume += typical_price * max(row["volume"], 0.0)
    return total_price_volume / total_volume


def _true_ranges(rows: Sequence[dict[str, Any]]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for row in rows:
        if previous_close is None:
            true_range = row["high"] - row["low"]
        else:
            true_range = max(row["high"] - row["low"], abs(row["high"] - previous_close), abs(row["low"] - previous_close))
        ranges.append(max(0.0, true_range))
        previous_close = row["close"]
    return ranges


def _average(values: Sequence[float]) -> float | None:
    finite = [float(value) for value in values if isinstance(value, (int, float)) and float(value) == float(value)]
    if not finite:
        return None
    return sum(finite) / len(finite)


def _ema(values: Sequence[float], period: int) -> float | None:
    if not values:
        return None
    period = max(1, int(period))
    alpha = 2 / (period + 1)
    ema_value = float(values[0])
    for value in values[1:]:
        ema_value = (float(value) * alpha) + (ema_value * (1 - alpha))
    return ema_value


def _sma(values: Sequence[float], period: int) -> float | None:
    if not values:
        return None
    period = min(max(1, int(period)), len(values))
    return sum(values[-period:]) / period


def _sample_every(values: Sequence[float], step: int) -> list[float]:
    if step <= 1:
        return list(values)
    return [float(values[index]) for index in range(step - 1, len(values), step)] or list(values[-1:])


def _classify_trend(closes: Sequence[float], lookback: int, atr: float | None) -> str:
    if len(closes) < 3:
        return "neutral"
    lookback = min(max(2, lookback), len(closes) - 1)
    delta = float(closes[-1]) - float(closes[-(lookback + 1)])
    threshold = max((atr or 0.0) * 0.6, abs(float(closes[-1])) * 0.0005)
    if delta > threshold:
        return "bullish"
    if delta < -threshold:
        return "bearish"
    return "neutral"


def _classify_time_of_day(timestamp: datetime) -> str:
    local = _ensure_aware_datetime(timestamp).astimezone(_NEW_YORK_TZ).time()
    if time(4, 0) <= local < time(9, 30):
        return "premarket"
    if time(9, 30) <= local < time(10, 0):
        return "open"
    if time(10, 0) <= local < time(11, 30):
        return "ny_am"
    if time(11, 30) <= local < time(13, 30):
        return "lunch"
    if time(13, 30) <= local < time(15, 0):
        return "ny_am"
    if time(15, 0) <= local < time(15, 45):
        return "power_hour"
    if time(15, 45) <= local < time(16, 15):
        return "close"
    return "overnight"
