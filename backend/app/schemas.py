from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Currency = Literal["HUF", "EUR", "RUB"]
RateSource = Literal["api", "manual"]


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
    icon: Optional[str] = Field(default=None, max_length=16)


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    color: Optional[str] = Field(default=None, min_length=4, max_length=9, pattern=r"^#[0-9a-fA-F]{3,8}$")
    icon: Optional[str] = Field(default=None, max_length=16)
    sort_order: Optional[int] = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    icon: Optional[str] = None
    is_archived: bool
    sort_order: int

    @field_validator("is_archived", mode="before")
    @classmethod
    def _coerce_archived(cls, v):
        return bool(v)


# ---------- Expenses ----------

def _validate_occurred_at(v: Optional[datetime]) -> Optional[datetime]:
    if v is None:
        return v
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    if v > datetime.now(timezone.utc) + timedelta(days=1):
        raise ValueError("occurred_at не может быть больше чем на сутки в будущем")
    return v


class ExpenseCreate(BaseModel):
    amount: float = Field(gt=0)
    currency: Currency
    category_id: int
    occurred_at: Optional[datetime] = None
    comment: Optional[str] = Field(default=None, max_length=500)
    rate_to_huf: Optional[float] = Field(default=None, gt=0)

    @field_validator("occurred_at")
    @classmethod
    def _check_occurred_at(cls, v):
        return _validate_occurred_at(v)


class ExpenseUpdate(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    currency: Optional[Currency] = None
    category_id: Optional[int] = None
    occurred_at: Optional[datetime] = None
    comment: Optional[str] = Field(default=None, max_length=500)
    rate_to_huf: Optional[float] = Field(default=None, gt=0)

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
    comment: Optional[str] = None
    occurred_at: str
    created_by: int
    created_at: str
    updated_at: str


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
    change_pct: Optional[float] = None


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
    comment: Optional[str] = None
    occurred_at: str


class BudgetStatusOut(BaseModel):
    category: CategoryOut
    limit_huf: float
    spent_huf: int
    pct: float


class BudgetsOverallStatusOut(BaseModel):
    total_monthly_budget_huf: Optional[float] = None
    spent_huf: int
    pct: Optional[float] = None
    categories: list[BudgetStatusOut]


# ---------- Budgets settings ----------

class BudgetCategoryIn(BaseModel):
    category_id: int
    limit_huf: float = Field(ge=0)


class BudgetsUpdate(BaseModel):
    total_monthly_budget_huf: Optional[float] = Field(default=None, ge=0)
    categories: list[BudgetCategoryIn] = Field(default_factory=list)


class BudgetCategoryOut(BaseModel):
    category: CategoryOut
    limit_huf: float


class BudgetsOut(BaseModel):
    total_monthly_budget_huf: Optional[float] = None
    categories: list[BudgetCategoryOut]


class HealthOut(BaseModel):
    status: str
    db: str
    last_rates_update: Optional[str] = None
