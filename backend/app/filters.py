from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import Select, select

from app.models import Expense
from app.timeutil import InvalidDateError, parse_lower_bound, parse_upper_bound, to_utc_iso


def date_bounds(
    date_from: str | datetime | None, date_to: str | datetime | None
) -> tuple[datetime | None, datetime | None]:
    """UTC bounds for a period. Bare ``YYYY-MM-DD`` values are local days (``settings.tz``):
    ``date_from`` starts at local midnight, ``date_to`` includes the whole local day."""
    try:
        start = parse_lower_bound(date_from) if date_from else None
        end = parse_upper_bound(date_to) if date_to else None
    except InvalidDateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return start, end


def filtered_expenses_query(
    date_from: str | datetime | None = None,
    date_to: str | datetime | None = None,
    category_id: list[int] | None = None,
    currency: str | None = None,
    q: str | None = None,
    include_deleted: bool = False,
) -> Select:
    query = select(Expense)
    if not include_deleted:
        query = query.where(Expense.deleted_at.is_(None))
    start, end = date_bounds(date_from, date_to)
    # Stored timestamps are UTC ISO strings (``+00:00``), so string comparison is chronological.
    if start is not None:
        query = query.where(Expense.occurred_at >= to_utc_iso(start))
    if end is not None:
        query = query.where(Expense.occurred_at <= to_utc_iso(end))
    if category_id:
        query = query.where(Expense.category_id.in_(category_id))
    if currency:
        query = query.where(Expense.currency == currency)
    if q:
        query = query.where(Expense.comment.ilike(f"%{q}%"))
    return query
