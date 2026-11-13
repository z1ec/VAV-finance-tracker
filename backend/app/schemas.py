import unicodedata
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

Currency = Literal["HUF", "EUR", "RUB"]
RateSource = Literal["api", "manual"]
ExpenseSource = Literal["web", "telegram", "subscription"]
AmountType = Literal["fixed", "variable"]
Period = Literal["weekly", "monthly", "yearly"]
ChargeStatus = Literal["pending", "paid", "skipped"]

# Input bounds (stage 3b): beyond these a value is a typo or an attack, and huge or infinite
# floats broke the HUF conversion (500) instead of failing validation (422).
MAX_AMOUNT = 1_000_000_000  # in any currency
MAX_RATE_TO_HUF = 1_000_000
MAX_BUDGET_HUF = 100_000_000_000
MAX_SORT_ORDER = 1_000_000

# HTML-significant characters: an icon is an emoji or a short symbol, never markup.
_ICON_FORBIDDEN = set("<>&\"'`=/\\")


def _check_icon(value: str | None) -> str | None:
    """Server-side guard for the category icon (the frontend escapes it as well)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if any(ch in _ICON_FORBIDDEN for ch in value) or any(
        unicodedata.category(ch) == "Cc" for ch in value
    ):
        raise ValueError("icon: допустимы только эмодзи и символы без HTML-разметки")
    return value


Icon = Annotated[str | None, Field(default=None, max_length=16), AfterValidator(_check_icon)]
Color = Annotated[str, Field(min_length=4, max_length=9, pattern=r"^#[0-9a-fA-F]{3,8}$")]
Amount = Annotated[float, Field(gt=0, le=MAX_AMOUNT)]
Rate = Annotated[float, Field(gt=0, le=MAX_RATE_TO_HUF)]


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
    color: Color
    icon: Icon = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: Color | None = None
    icon: Icon = None
    sort_order: int | None = Field(default=None, ge=-MAX_SORT_ORDER, le=MAX_SORT_ORDER)


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
    amount: Amount
    currency: Currency
    category_id: int
    occurred_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=500)
    rate_to_huf: Rate | None = None

    @field_validator("occurred_at")
    @classmethod
    def _check_occurred_at(cls, v):
        return _validate_occurred_at(v)


class ExpenseUpdate(BaseModel):
    amount: Amount | None = None
    currency: Currency | None = None
    category_id: int | None = None
    occurred_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=500)
    rate_to_huf: Rate | None = None

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
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency: Currency
    rate_to_huf: Rate

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: str) -> str:
        date.fromisoformat(v)  # ValueError → 422 for 2026-02-30 and the like
        return v


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
    limit_huf: float = Field(ge=0, le=MAX_BUDGET_HUF)


class BudgetsUpdate(BaseModel):
    total_monthly_budget_huf: float | None = Field(default=None, ge=0, le=MAX_BUDGET_HUF)
    categories: list[BudgetCategoryIn] = Field(default_factory=list, max_length=500)


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
    amount: Amount | None = None
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
    amount: Amount | None = None
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
    amount: Amount
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
