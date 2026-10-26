"""Bounded inputs for read-only account risk scenarios."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .input_bounds import MAX_DATABASE_ID


class RiskForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source_id: str = Field(default="trades", pattern=r"^(trades|saved:[1-9][0-9]{0,18}|job:[a-f0-9]{32})$")
    lookback_days: int = Field(default=365, ge=30, le=3650, strict=True)
    horizon_days: int = Field(default=20, ge=1, le=60, strict=True)
    starting_balance: float = Field(ge=-1e9, le=1e9)
    loss_buffer: float = Field(gt=0, le=1e9)
    daily_loss_limit: float | None = Field(default=None, gt=0, le=1e9)
    trailing_mode: Literal["fixed", "end_of_day", "trade_close"] = "fixed"
    trailing_distance: float | None = Field(default=None, gt=0, le=1e9)
    floor_cap: float | None = Field(default=None, ge=-1e9, le=1e9)
    extra_cost_per_trade: float = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def coherent_limits(self):
        if self.source_id.startswith("saved:") and int(self.source_id.split(":")[1]) > MAX_DATABASE_ID:
            raise ValueError("Saved backtest ID is out of range.")
        floor = self.starting_balance - self.loss_buffer
        if self.trailing_mode == "fixed":
            if self.trailing_distance is not None or self.floor_cap is not None:
                raise ValueError("Fixed floors do not use a trailing distance or cap.")
        else:
            if self.trailing_distance is None:
                raise ValueError("Set the trailing distance for a trailing floor.")
            if self.floor_cap is not None and self.floor_cap < floor:
                raise ValueError("The floor cap cannot be below the current loss floor.")
            minimum_floor = self.starting_balance - self.trailing_distance
            if self.floor_cap is not None:
                minimum_floor = min(minimum_floor, self.floor_cap)
            if floor < minimum_floor - .001:
                raise ValueError("The remaining buffer exceeds the trailing distance before the floor cap is reached.")
        return self
