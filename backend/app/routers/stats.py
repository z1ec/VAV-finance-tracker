from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app import timeutil
from app.db import get_db
from app.deps import Actor, get_current_user, get_reader
from app.filters import date_bounds, filtered_expenses_query
from app.models import Budget, Category, Expense, Setting, User
from app.schemas import (
    BudgetsOverallStatusOut,
    BudgetStatusOut,
    ByCategoryOut,
    ByCurrencyOut,
    ByMonthOut,
    SummaryOut,
    TopExpenseOut,
)

router = APIRouter()


@dataclass
class StatsFilters:
    date_from: str | None = None
    date_to: str | None = None
    category_id: list[int] | None = None
    currency: str | None = None


def stats_filters(
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: list[int] | None = Query(default=None),
    currency: str | None = None,
) -> StatsFilters:
    return StatsFilters(
        date_from=date_from, date_to=date_to, category_id=category_id, currency=currency
    )


def _period_bounds(date_from: str | None, date_to: str | None) -> tuple[datetime, datetime]:
    """Default period: from the start of the current local month until now."""
    now = timeutil.now_utc()
    start, end = date_bounds(date_from, date_to)
    if start is None:
        start = timeutil.local_midnight_utc(timeutil.local_today(now).replace(day=1))
    return start, end or now


def _local_date(value: datetime) -> date:
    return value.astimezone(timeutil.local_tz()).date()


@router.get("/summary", response_model=SummaryOut)
def summary(
    filters: StatsFilters = Depends(stats_filters),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_reader),
):
    start, end = _period_bounds(filters.date_from, filters.date_to)

    query = filtered_expenses_query(start, end, filters.category_id, filters.currency, None)
    subq = query.subquery()
    total_huf, count = db.execute(
        select(func.coalesce(func.sum(subq.c.amount_huf), 0), func.count()).select_from(subq)
    ).one()

    days = max((_local_date(end) - _local_date(start)).days + 1, 1)
    avg_per_day = float(total_huf) / days
    avg_per_expense = float(total_huf) / count if count else 0.0

    period_length = end - start
    prev_end = start - timedelta(microseconds=1)
    prev_start = prev_end - period_length

    prev_query = filtered_expenses_query(
        prev_start, prev_end, filters.category_id, filters.currency, None
    )
    prev_subq = prev_query.subquery()
    prev_total = db.execute(
        select(func.coalesce(func.sum(prev_subq.c.amount_huf), 0)).select_from(prev_subq)
    ).scalar_one()

    change_pct = None
    if prev_total:
        change_pct = round(((total_huf - prev_total) / prev_total) * 100, 2)
    elif total_huf:
        change_pct = 100.0

    return SummaryOut(
        total_huf=int(total_huf),
        count=count,
        avg_per_day=round(avg_per_day, 2),
        avg_per_expense=round(avg_per_expense, 2),
        prev_period_total_huf=int(prev_total),
        change_pct=change_pct,
    )


@router.get("/by-category", response_model=list[ByCategoryOut])
def by_category(
    filters: StatsFilters = Depends(stats_filters),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start, end = _period_bounds(filters.date_from, filters.date_to)
    query = filtered_expenses_query(start, end, filters.category_id, filters.currency, None)
    subq = query.subquery()

    rows = db.execute(
        select(Category, func.coalesce(func.sum(subq.c.amount_huf), 0), func.count(subq.c.id))
        .join(subq, subq.c.category_id == Category.id)
        .group_by(Category.id)
        .order_by(func.sum(subq.c.amount_huf).desc())
    ).all()

    total = sum(row[1] for row in rows) or 1

    return [
        ByCategoryOut(
            category=cat,
            total_huf=int(total_huf),
            count=count,
            pct=round(total_huf / total * 100, 2),
        )
        for cat, total_huf, count in rows
    ]


@router.get("/by-month", response_model=list[ByMonthOut])
def by_month(
    category_id: list[int] | None = Query(default=None),
    currency: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Twelve local calendar months ending with the current one.
    cursor = timeutil.local_today().replace(day=1)
    months: list[date] = []
    for _ in range(12):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()

    range_start = timeutil.local_midnight_utc(months[0])
    query = filtered_expenses_query(range_start, None, category_id, currency, None)
    subq = query.subquery()
    rows = db.execute(select(subq.c.occurred_at, subq.c.amount_huf)).all()

    totals = {m.strftime("%Y-%m"): 0 for m in months}
    for occurred_at, amount_huf in rows:
        key = timeutil.to_local_date(occurred_at).strftime("%Y-%m")
        if key in totals:
            totals[key] += amount_huf

    return [ByMonthOut(month=key, total_huf=int(value)) for key, value in totals.items()]


@router.get("/by-currency", response_model=list[ByCurrencyOut])
def by_currency(
    filters: StatsFilters = Depends(stats_filters),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start, end = _period_bounds(filters.date_from, filters.date_to)
    query = filtered_expenses_query(start, end, filters.category_id, filters.currency, None)
    subq = query.subquery()

    rows = db.execute(
        select(
            subq.c.currency, func.sum(subq.c.amount), func.sum(subq.c.amount_huf), func.count()
        ).group_by(subq.c.currency)
    ).all()

    return [
        ByCurrencyOut(
            currency=cur, total_original=float(total_orig), total_huf=int(total_huf), count=count
        )
        for cur, total_orig, total_huf, count in rows
    ]


@router.get("/top", response_model=list[TopExpenseOut])
def top_expenses(
    limit: int = Query(default=10, ge=1, le=100),
    filters: StatsFilters = Depends(stats_filters),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start, end = _period_bounds(filters.date_from, filters.date_to)
    query = (
        filtered_expenses_query(start, end, filters.category_id, filters.currency, None)
        .options(joinedload(Expense.category))
        .order_by(Expense.amount_huf.desc())
        .limit(limit)
    )
    return db.execute(query).scalars().unique().all()


@router.get("/budget", response_model=BudgetsOverallStatusOut)
def budget_status(db: Session = Depends(get_db), actor: Actor = Depends(get_reader)):
    """Budget usage for the current local month (Europe/Budapest) up to now."""
    now = timeutil.now_utc()
    month_start = timeutil.local_midnight_utc(timeutil.local_today(now).replace(day=1))

    query = filtered_expenses_query(month_start, now, None, None, None)
    subq = query.subquery()
    spent_by_category: dict[int, int] = {
        category_id: spent
        for category_id, spent in db.execute(
            select(subq.c.category_id, func.sum(subq.c.amount_huf)).group_by(subq.c.category_id)
        ).all()
    }
    total_spent = sum(spent_by_category.values())

    budgets = db.execute(select(Budget).options(joinedload(Budget.category))).scalars().all()
    categories_status = []
    for b in budgets:
        spent = spent_by_category.get(b.category_id, 0)
        pct = round(spent / float(b.limit_huf) * 100, 2) if b.limit_huf else 0.0
        categories_status.append(
            BudgetStatusOut(
                category=b.category, limit_huf=float(b.limit_huf), spent_huf=int(spent), pct=pct
            )
        )

    total_budget_setting = db.get(Setting, "total_monthly_budget_huf")
    total_budget = (
        float(total_budget_setting.value)
        if total_budget_setting and total_budget_setting.value
        else None
    )
    overall_pct = round(total_spent / total_budget * 100, 2) if total_budget else None

    return BudgetsOverallStatusOut(
        total_monthly_budget_huf=total_budget,
        spent_huf=int(total_spent),
        pct=overall_pct,
        categories=categories_status,
    )
