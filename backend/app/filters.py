from sqlalchemy import Select, select

from app.models import Expense


def _normalize_date_to(value: str | None) -> str | None:
    """A bare 'YYYY-MM-DD' date_to should include the whole day."""
    if value and len(value) == 10:
        return value + "T23:59:59.999999+00:00"
    return value


def filtered_expenses_query(
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: list[int] | None = None,
    currency: str | None = None,
    q: str | None = None,
    include_deleted: bool = False,
) -> Select:
    query = select(Expense)
    if not include_deleted:
        query = query.where(Expense.deleted_at.is_(None))
    if date_from:
        query = query.where(Expense.occurred_at >= date_from)
    date_to = _normalize_date_to(date_to)
    if date_to:
        query = query.where(Expense.occurred_at <= date_to)
    if category_id:
        query = query.where(Expense.category_id.in_(category_id))
    if currency:
        query = query.where(Expense.currency == currency)
    if q:
        query = query.where(Expense.comment.ilike(f"%{q}%"))
    return query
