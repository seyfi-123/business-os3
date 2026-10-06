from pydantic import BaseModel, Field
from datetime import datetime


class PlanOut(BaseModel):
    code: str; name_tj: str; description_tj: str
    price_tjs: float; billing_period_days: int; trial_days: int
    limits: dict; features: dict


class SubscriptionOut(BaseModel):
    id: int; tenant_id: int; plan_code: str
    plan: dict | None = None
    status: str
    started_at: datetime | None = None
    trial_end: datetime | None = None
    current_period_start: datetime
    current_period_end: datetime
    cancelled_at: datetime | None = None
    auto_renew: bool
    days_remaining: int | None = None


class SubscribeIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=32)


class InvoiceOut(BaseModel):
    id: int; number: str; tenant_id: int; plan_code: str
    amount: float; currency: str; status: str
    period_start: datetime; period_end: datetime
    issued_at: datetime | None = None
    due_at: datetime | None = None
    paid_at: datetime | None = None
    payment_method: str | None = None


class UsageItem(BaseModel):
    name: str; current: int | float
    limit: int | float | None
    used_pct: float | None = None


class UsageOut(BaseModel):
    plan_code: str | None = None
    status: str | None = None
    items: list[UsageItem]
