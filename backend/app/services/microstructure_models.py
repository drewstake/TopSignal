"""Strict HTTP research contracts; no client-owned source or identity fields."""
from datetime import datetime, date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Window(StrictModel):
    start: datetime
    end: datetime

    @model_validator(mode='after')
    def bounds(self):
        if self.start.tzinfo is None or self.end.tzinfo is None or not 0 < (self.end-self.start).total_seconds() <= 600:
            raise ValueError('Each window must include a timezone and span at most 10 minutes')
        return self


Feature = Literal['imbalance', 'bid_contracts', 'ask_contracts', 'bid_depth_ratio', 'ask_depth_ratio',
    'bid_depth_z', 'ask_depth_z', 'bid_thin_seconds', 'ask_thin_seconds',
    'negative_imbalance_seconds', 'positive_imbalance_seconds', 'spread', 'volatility',
    'trade_count_rate', 'volume_rate', 'bid_depletion_rate', 'ask_depletion_rate', 'bid_refill_rate', 'ask_refill_rate']


class Condition(StrictModel):
    feature: Feature = 'imbalance'
    operator: Literal['lte', 'gte'] = 'lte'
    threshold: float = Field(default=-.3, ge=-1000000, le=1000000)


class Execution(StrictModel):
    direction: Literal['long', 'short'] = 'short'
    order: Literal['market', 'limit_unresolved'] = 'market'
    entry_delay_ms: int = Field(default=0, ge=0, le=60000)
    entry_latency_ms: int = Field(default=250, ge=0, le=10000)
    exit_latency_ms: int = Field(default=250, ge=0, le=10000)
    stop_ticks: int = Field(default=8, ge=1, le=1000)
    target_ticks: int = Field(default=8, ge=1, le=1000)
    equal_reward_risk: bool = True
    max_hold_seconds: int = Field(default=30, ge=1, le=300)
    commission_fees_round_trip: float = Field(default=1.24, ge=0, le=100)
    slippage_ticks_per_side: float = Field(default=1, ge=0, le=20)
    size: int = Field(default=1, ge=1, le=100)
    tick_size: Literal[.25] = .25
    tick_value: Literal[.5] = .5
    overlapping: Literal['skip', 'independent'] = 'skip'
    ambiguity: Literal['stop_first', 'exclude'] = 'stop_first'


class Study(StrictModel):
    conditions: list[Condition] = Field(default_factory=lambda: [Condition()], min_length=1, max_length=4)
    combine: Literal['all', 'any'] = 'all'
    activity_window: Literal[1, 5, 15] = 5
    persistence_seconds: float = Field(default=1, ge=0, le=30)
    cooldown_seconds: int = Field(default=30, ge=1, le=600)
    horizons: list[int] = Field(default_factory=lambda: [5, 15, 60], min_length=1, max_length=6)
    nothing_ticks: float = Field(default=2, ge=0, le=100)
    validation_session: date | None = None
    test_session: date | None = None
    execution: Execution = Field(default_factory=Execution)
    threshold_rationale: str = Field(default='Manual exploratory threshold; not optimized.', min_length=1, max_length=2000)

    @model_validator(mode='after')
    def split(self):
        if any(type(n) is not int or not 1 <= n <= 300 for n in self.horizons):
            raise ValueError('Horizons must be 1–300 seconds')
        if bool(self.validation_session) != bool(self.test_session) or (self.validation_session and self.validation_session >= self.test_session):
            raise ValueError('Validation and test must start on distinct chronological sessions')
        return self


class ResearchRequest(StrictModel):
    kind: Literal['window', 'study'] = 'window'
    windows: list[Window] = Field(min_length=1, max_length=12)
    scope: str = Field(default='10', pattern=r'^(all|1|5|10|20|50|100|ticks:([1-9][0-9]?|100))$')
    tick_distances: list[int] = Field(default_factory=lambda: [1, 5, 10, 20], min_length=1, max_length=8)
    study: Study = Field(default_factory=Study)
    experiment_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
    forward: bool = False

    @model_validator(mode='after')
    def bounded(self):
        if self.kind == 'window' and len(self.windows) != 1:
            raise ValueError('Replay takes one window')
        if any(type(n) is not int or not 1 <= n <= 100 for n in self.tick_distances):
            raise ValueError('Tick distances must be 1–100')
        if self.scope.startswith('ticks:') and int(self.scope[6:]) not in self.tick_distances:
            raise ValueError('Selected tick distance must be measured')
        ordered = sorted(self.windows, key=lambda w: w.start)
        if any(b.start < a.end for a, b in zip(ordered, ordered[1:])):
            raise ValueError('Research windows must not overlap')
        return self


class BookmarkInput(StrictModel):
    job_id: str = Field(pattern='^[a-f0-9]{32}$')
    timestamp: datetime
    note: str = Field(min_length=1, max_length=2000)


class ExperimentInput(StrictModel):
    job_id: str = Field(pattern='^[a-f0-9]{32}$')
    hypothesis: str = Field(min_length=1, max_length=4000)
    notes: str = Field(default='', max_length=4000)
    freeze: bool = False
    parent_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
