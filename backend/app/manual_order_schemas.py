from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class ManualOrderTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    side: Literal["BUY", "SELL"]
    quantity: int = Field(strict=True, ge=1, le=10)
    stop_loss_ticks: int = Field(strict=True, ge=1, le=1000)
    take_profit_ticks: int = Field(strict=True, ge=1, le=1000)
    confirm_live_order_routing: StrictBool

    @field_validator("confirm_live_order_routing")
    @classmethod
    def require_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required for a live test order.")
        return value
