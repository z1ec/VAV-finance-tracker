import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.currency import (
    RateUnavailableError,
    convert_to_huf,
    resolve_expense_rate,
    to_decimal,
)
from app.db import get_db
from app.deps import get_current_user, require_admin
from app.filters import filtered_expenses_query
from app.models import Category, Expense, User
from app.schemas import ExpenseCreate, ExpenseListOut, ExpenseOut, ExpenseUpdate

router = APIRouter()


@router.get("/trash", response_model=list[ExpenseOut])
def trash(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    query = (
        select(Expense)
        .options(joinedload(Expense.category))
        .where(Expense.deleted_at.is_not(None), Expense.deleted_at >= cutoff)
        .order_by(Expense.deleted_at.desc())
    )
    return db.execute(query).scalars().unique().all()


@router.get("/export")
def export_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: list[int] | None = Query(default=None),
    currency: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        filtered_expenses_query(date_from, date_to, category_id, currency, q)
        .options(joinedload(Expense.category))
        .order_by(Expense.occurred_at.desc())
    )
    items = db.execute(query).scalars().unique().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "occurred_at", "category", "amount", "currency", "rate_to_huf", "amount_huf", "rate_source", "comment"])
    for e in items:
        writer.writerow(
            [e.id, e.occurred_at, e.category.name, e.amount, e.currency, e.rate_to_huf, e.amount_huf, e.rate_source, e.comment or ""]
        )

    csv_content = "﻿" + buffer.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"},
    )


@router.get("", response_model=ExpenseListOut)
def list_expenses(
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: list[int] | None = Query(default=None),
    currency: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    sort: str = Query(default="-occurred_at"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    base_query = filtered_expenses_query(date_from, date_to, category_id, currency, q)

    sort_field = sort.lstrip("-")
    sort_column = Expense.amount_huf if sort_field == "amount_huf" else Expense.occurred_at
    order = sort_column.desc() if sort.startswith("-") else sort_column.asc()

    items_query = base_query.options(joinedload(Expense.category)).order_by(order, Expense.id.desc())
    items_query = items_query.offset((page - 1) * per_page).limit(per_page)
    items = db.execute(items_query).scalars().unique().all()

    subq = base_query.subquery()
    total, sum_huf = db.execute(
        select(func.count(), func.coalesce(func.sum(subq.c.amount_huf), 0)).select_from(subq)
    ).one()

    return ExpenseListOut(items=items, total=total, page=page, per_page=per_page, sum_huf=int(sum_huf))


@router.post("", response_model=ExpenseOut, status_code=201)
def create_expense(payload: ExpenseCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    category = db.get(Category, payload.category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    occurred_at = payload.occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    amount = to_decimal(payload.amount)
    manual_rate = to_decimal(payload.rate_to_huf) if payload.rate_to_huf is not None else None

    try:
        rate, source = resolve_expense_rate(db, payload.currency, occurred_at, manual_rate)
    except RateUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    expense = Expense(
        amount=amount,
        currency=payload.currency,
        rate_to_huf=rate,
        amount_huf=convert_to_huf(amount, rate),
        rate_source=source,
        category_id=payload.category_id,
        comment=payload.comment,
        occurred_at=occurred_at.isoformat(),
        created_by=user.id,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.patch("/{expense_id}", response_model=ExpenseOut)
def update_expense(expense_id: int, payload: ExpenseUpdate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    expense = db.get(Expense, expense_id)
    if not expense or expense.deleted_at:
        raise HTTPException(status_code=404, detail="Операция не найдена")

    data = payload.model_dump(exclude_unset=True)

    if "category_id" in data:
        category = db.get(Category, data["category_id"])
        if not category:
            raise HTTPException(status_code=404, detail="Категория не найдена")
        expense.category_id = data["category_id"]

    if "comment" in data:
        expense.comment = data["comment"]

    if "occurred_at" in data and data["occurred_at"] is not None:
        occurred_at = data["occurred_at"]
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        expense.occurred_at = occurred_at.isoformat()
    occurred_at_dt = datetime.fromisoformat(expense.occurred_at)

    new_amount = to_decimal(data["amount"]) if "amount" in data else to_decimal(expense.amount)
    new_currency = data.get("currency", expense.currency)

    recalc_needed = any(k in data for k in ("amount", "currency", "occurred_at"))
    manual_rate_provided = data.get("rate_to_huf") is not None

    if manual_rate_provided:
        rate, source = to_decimal(data["rate_to_huf"]), "manual"
    elif recalc_needed:
        try:
            rate, source = resolve_expense_rate(db, new_currency, occurred_at_dt, None)
        except RateUnavailableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        rate, source = to_decimal(expense.rate_to_huf), expense.rate_source

    expense.amount = new_amount
    expense.currency = new_currency
    expense.rate_to_huf = rate
    expense.rate_source = source
    expense.amount_huf = convert_to_huf(new_amount, rate)
    expense.updated_at = datetime.now(timezone.utc).isoformat()

    db.commit()
    db.refresh(expense)
    return expense


@router.delete("/{expense_id}", status_code=204)
def delete_expense(expense_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    expense = db.get(Expense, expense_id)
    if not expense or expense.deleted_at:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    expense.deleted_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return Response(status_code=204)


@router.post("/{expense_id}/restore", response_model=ExpenseOut)
def restore_expense(expense_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    expense = db.get(Expense, expense_id)
    if not expense or not expense.deleted_at:
        raise HTTPException(status_code=404, detail="Операция не найдена в корзине")
    expense.deleted_at = None
    db.commit()
    db.refresh(expense)
    return expense
