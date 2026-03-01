from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

ExpenseCategory = Literal["evaluation_fee", "activation_fee", "reset_fee", "data_fee", "other"]
ExpenseAccountType = Literal["no_activation", "standard", "practice"]
ExpensePlanSize = Literal["50k", "100k", "150k"]
ExpenseRange = Literal["week", "month", "ytd", "all_time"]
WeekStart = Literal["monday", "sunday"]


class ExpenseCreateIn(BaseModel):
    expense_date: date
    amount: Decimal | None = None
    amount_cents: int | None = None
    category: ExpenseCategory
    provider: str = "topstep"
    account_id: int | None = None
    account_type: ExpenseAccountType | None = None
    plan_size: ExpensePlanSize | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_practice: bool = False
    currency: str = "USD"

    @model_validator(mode="after")
    def ensure_amount_present(self) -> "ExpenseCreateIn":
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


class ExpenseUpdateIn(BaseModel):
    expense_date: date | None = None
    amount_cents: int | None = None
    category: ExpenseCategory | None = None
    account_id: int | None = None
    account_type: ExpenseAccountType | None = None
    plan_size: ExpensePlanSize | None = None
    description: str | None = None
    tags: list[str] | None = None
    is_practice: bool | None = None

    @model_validator(mode="after")
    def ensure_non_empty(self) -> "ExpenseUpdateIn":
        if not any(
            value is not None
            for value in (
                self.expense_date,
                self.amount_cents,
                self.category,
                self.account_id,
                self.account_type,
                self.plan_size,
                self.description,
                self.tags,
                self.is_practice,
            )
        ):
            raise ValueError("at least one field must be provided")
        return self


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int | None
    provider: str
    expense_date: date
    amount_cents: int
    currency: str
    category: ExpenseCategory
    account_type: ExpenseAccountType | None
    plan_size: ExpensePlanSize | None
    description: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    @computed_field(return_type=float)
    def amount(self) -> float:
        return round(self.amount_cents / 100, 2)


class ExpenseListOut(BaseModel):
    items: list[ExpenseOut]
    total: int


class ExpenseCategoryTotalsOut(BaseModel):
    amount: float
    amount_cents: int
    count: int


class ExpenseTotalsOut(BaseModel):
    range: ExpenseRange
    start_date: date | None
    end_date: date
    total_amount: float
    total_amount_cents: int
    by_category: dict[str, ExpenseCategoryTotalsOut]
    count: int
