from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, computed_field, model_validator


class PayoutCreateIn(BaseModel):
    payout_date: date
    amount: Decimal | None = None
    amount_cents: int | None = None
    notes: str | None = None
    currency: str = "USD"

    @model_validator(mode="after")
    def ensure_amount_present(self) -> "PayoutCreateIn":
        if self.amount is None and self.amount_cents is None:
            raise ValueError("either amount or amount_cents is required")

        if self.amount is not None:
            normalized_amount = self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            normalized_cents = int((normalized_amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
            if self.amount_cents is None:
                self.amount_cents = normalized_cents
            elif self.amount_cents != normalized_cents:
                raise ValueError("amount and amount_cents must match")

        return self


class PayoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payout_date: date
    amount_cents: int
    currency: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field(return_type=float)
    def amount(self) -> float:
        return round(self.amount_cents / 100, 2)


class PayoutListOut(BaseModel):
    items: list[PayoutOut]
    total: int


class PayoutTotalsOut(BaseModel):
    total_amount: float
    total_amount_cents: int
    average_amount: float
    average_amount_cents: int
    count: int
