from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Currency = Literal["HUF", "EUR", "RUB"]
RateSource = Literal["api", "manual"]
ExpenseSource = Literal["web", "telegram", "subscription"]
AmountType = Literal["fixed", "variable"]
Period = Literal["weekly", "monthly", "yearly"]
ChargeStatus = Literal["pending", "paid", "skipped"]


# ---------- Auth ----------


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str


# ---------- Categories ----------


class CategoryBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(min_length=4, max_length=9, pattern=r"^#[0-9a-fA-F]{3,8}$")
    icon: str | None = Field(default=None, max_length=16)


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(
        default=None, min_length=4, max_length=9, pattern=r"^#[0-9a-fA-F]{3,8}$"
    )
    icon: str | None = Field(default=None, max_length=16)
    sort_order: int | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    icon: str | None = None
    is_archived: bool
    sort_order: int

    @field_validator("is_archived", mode="before")
    @classmethod
    def _coerce_archived(cls, v):
        return bool(v)


# ---------- Expenses ----------


def _validate_occurred_at(v: datetime | None) -> datetime | None:
    """Reject far-future dates. A naive value is kept naive: the endpoint decides whether it is
    accepted (site: treated as UTC, as before) or rejected (bot token: offset required)."""
    if v is None:
        return v
    aware = v if v.tzinfo is not None else v.replace(tzinfo=UTC)
    if aware > datetime.now(UTC) + timedelta(days=1):
        raise ValueError("occurred_at не может быть больше чем на сутки в будущем")
    return v


class ExpenseCreate(BaseModel):
    amount: float = Field(gt=0)
    currency: Currency
    category_id: int
    occurred_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=500)
    rate_to_huf: float | None = Field(default=None, gt=0)

    @field_validator("occurred_at")
    @classmethod
    def _check_occurred_at(cls, v):
        return _validate_occurred_at(v)


class ExpenseUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    currency: Currency | None = None
    category_id: int | None = None
    occurred_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=500)
    rate_to_huf: float | None = Field(default=None, gt=0)

    @field_validator("occurred_at")
    @classmethod
    def _check_occurred_at(cls, v):
        return _validate_occurred_at(v)


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: float
    currency: str
    amount_huf: int
    rate_to_huf: float
    rate_source: str
    category: CategoryOut
    comment: str | None = None
    occurred_at: str
    created_by: int
    created_at: str
    updated_at: str
    source: ExpenseSource = "web"


class ExpenseListOut(BaseModel):
    items: list[ExpenseOut]
    total: int
    page: int
    per_page: int
    sum_huf: int


# ---------- Rates ----------


class RateManualCreate(BaseModel):
    date: str
    currency: Currency
    rate_to_huf: float = Field(gt=0)


class RateOut(BaseModel):
    date: str
    currency: str
    rate_to_huf: float
    source: str


# ---------- Stats ----------


class SummaryOut(BaseModel):
    total_huf: int
    count: int
    avg_per_day: float
    avg_per_expense: float
    prev_period_total_huf: int
    change_pct: float | None = None


class ByCategoryOut(BaseModel):
    category: CategoryOut
    total_huf: int
    count: int
    pct: float


class ByMonthOut(BaseModel):
    month: str
    total_huf: int


class ByCurrencyOut(BaseModel):
    currency: str
    total_original: float
    total_huf: int
    count: int


class TopExpenseOut(BaseModel):
    id: int
    amount: float
    currency: str
    amount_huf: int
    category: CategoryOut
    comment: str | None = None
    occurred_at: str


class BudgetStatusOut(BaseModel):
    category: CategoryOut
    limit_huf: float
    spent_huf: int
    pct: float


class BudgetsOverallStatusOut(BaseModel):
    total_monthly_budget_huf: float | None = None
    spent_huf: int
    pct: float | None = None
    categories: list[BudgetStatusOut]


# ---------- Budgets settings ----------


class BudgetCategoryIn(BaseModel):
    category_id: int
    limit_huf: float = Field(ge=0)


class BudgetsUpdate(BaseModel):
    total_monthly_budget_huf: float | None = Field(default=None, ge=0)
    categories: list[BudgetCategoryIn] = Field(default_factory=list)


class BudgetCategoryOut(BaseModel):
    category: CategoryOut
    limit_huf: float


class BudgetsOut(BaseModel):
    total_monthly_budget_huf: float | None = None
    categories: list[BudgetCategoryOut]


# ---------- Subscriptions ----------


class SubscriptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    amount_type: AmountType = "fixed"
    amount: float | None = Field(default=None, gt=0)
    currency: Currency
    category_id: int
    period: Period = "monthly"
    interval: int = Field(default=1, ge=1, le=120)
    anchor_day: int | None = Field(default=None, ge=1, le=31)
    next_payment_date: date
    remind_days_before: int = Field(default=3, ge=0, le=60)
    is_active: bool = True
    comment: str | None = Field(default=None, max_length=500)


class SubscriptionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    amount_type: AmountType | None = None
    amount: float | None = Field(default=None, gt=0)
    currency: Currency | None = None
    category_id: int | None = None
    period: Period | None = None
    interval: int | None = Field(default=None, ge=1, le=120)
    anchor_day: int | None = Field(default=None, ge=1, le=31)
    next_payment_date: date | None = None
    remind_days_before: int | None = Field(default=None, ge=0, le=60)
    is_active: bool | None = None
    comment: str | None = Field(default=None, max_length=500)


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    amount_type: AmountType
    amount: float | None = None
    currency: Currency
    category: CategoryOut
    period: Period
    interval: int
    anchor_day: int | None = None
    next_payment_date: str
    remind_days_before: int
    is_active: bool
    comment: str | None = None
    created_at: str
    updated_at: str

    @field_validator("is_active", mode="before")
    @classmethod
    def _coerce_active(cls, v):
        return bool(v)


class ChargeOut(BaseModel):
    id: int
    subscription_id: int
    subscription_name: str
    category: CategoryOut
    amount_type: AmountType
    due_date: str
    status: ChargeStatus
    amount: float | None = None
    currency: Currency
    expense_id: int | None = None
    created_at: str
    updated_at: str


class ChargePay(BaseModel):
    amount: float = Field(gt=0)
    currency: Currency | None = None
    occurred_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("occurred_at")
    @classmethod
    def _check_occurred_at(cls, v):
        return _validate_occurred_at(v)


class UpcomingItemOut(BaseModel):
    subscription_id: int
    name: str
    category: CategoryOut
    amount_type: AmountType
    amount: float | None = None
    currency: Currency
    due_date: str
    days_left: int
    remind: bool


class UpcomingOut(BaseModel):
    today: str
    days: int
    upcoming: list[UpcomingItemOut]
    pending: list[ChargeOut]


class SubscriptionMonthlyItem(BaseModel):
    subscription_id: int
    name: str
    amount_type: AmountType
    period: Period
    interval: int
    amount: float | None = None
    currency: Currency
    monthly_huf: float | None = None
    basis: Literal["fixed", "last_paid", "excluded"]


class SubscriptionsSummaryOut(BaseModel):
    monthly_total_huf: int
    active_count: int
    included_count: int
    excluded_count: int
    items: list[SubscriptionMonthlyItem]


class HealthOut(BaseModel):
    status: str
    db: str
    last_rates_update: str | None = None
