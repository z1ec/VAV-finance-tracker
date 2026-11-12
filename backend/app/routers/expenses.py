import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.currency import (
    RateUnavailableError,
    convert_to_huf,
    resolve_expense_rate,
    to_decimal,
)
from app.db import get_db
from app.deps import Actor, get_current_user, require_admin, require_writer
from app.expense_service import (
    build_expense,
    commit_idempotent,
    ensure_same_request,
    find_by_key,
    normalize_decimal,
    parse_idempotency_key,
    request_fingerprint,
    resolve_occurred_at,
)
from app.filters import filtered_expenses_query
from app.models import Category, Expense, User
from app.schemas import ExpenseCreate, ExpenseListOut, ExpenseOut, ExpenseUpdate

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="UUID; required for the bot token, optional for the site (ADR-0005).",
    ),
]

router = APIRouter()


@router.get("/trash", response_model=list[ExpenseOut])
def trash(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
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
    writer.writerow(
        [
            "id",
            "occurred_at",
            "category",
            "amount",
            "currency",
            "rate_to_huf",
            "amount_huf",
            "rate_source",
            "comment",
            "source",
        ]
    )
    for e in items:
        writer.writerow(
            [
                e.id,
                e.occurred_at,
                e.category.name,
                e.amount,
                e.currency,
                e.rate_to_huf,
                e.amount_huf,
                e.rate_source,
                e.comment or "",
                e.source,
            ]
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

    items_query = base_query.options(joinedload(Expense.category)).order_by(
        order, Expense.id.desc()
    )
    items_query = items_query.offset((page - 1) * per_page).limit(per_page)
    items = db.execute(items_query).scalars().unique().all()

    subq = base_query.subquery()
    total, sum_huf = db.execute(
        select(func.count(), func.coalesce(func.sum(subq.c.amount_huf), 0)).select_from(subq)
    ).one()

    return ExpenseListOut(
        items=items, total=total, page=page, per_page=per_page, sum_huf=int(sum_huf)
    )


def _load(db: Session, expense_id: int) -> Expense:
    return db.execute(
        select(Expense).options(joinedload(Expense.category)).where(Expense.id == expense_id)
    ).scalar_one()


@router.post(
    "",
    response_model=ExpenseOut,
    status_code=201,
    responses={
        200: {"description": "Replay: the expense created earlier with this Idempotency-Key"},
        409: {"description": "Idempotency-Key already used with different content"},
    },
)
def create_expense(
    payload: ExpenseCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_writer),
):
    key = parse_idempotency_key(idempotency_key, required=actor.via_token)
    occurred_at = (
        resolve_occurred_at(payload.occurred_at, actor.via_token, datetime.now(UTC))
        if payload.occurred_at is not None
        else None
    )
    fingerprint = request_fingerprint(
        "expense.create",
        {
            "amount": normalize_decimal(payload.amount),
            "currency": payload.currency,
            "category_id": payload.category_id,
            "comment": payload.comment,
            "occurred_at": occurred_at.isoformat() if occurred_at else None,
            "rate_to_huf": normalize_decimal(payload.rate_to_huf),
        },
    )

    def replay(existing: Expense) -> Expense:
        response.status_code = status.HTTP_200_OK
        return _load(db, existing.id)

    if key:
        existing = find_by_key(db, key)
        if existing is not None:
            ensure_same_request(existing, fingerprint)
            return replay(existing)

    category = db.get(Category, payload.category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    expense = build_expense(
        db,
        amount=payload.amount,
        currency=payload.currency,
        category_id=payload.category_id,
        comment=payload.comment,
        occurred_at=occurred_at or datetime.now(UTC),
        user_id=actor.user.id,
        source="telegram" if actor.via_token else "web",
        manual_rate=payload.rate_to_huf,
        client_request_id=key,
        client_request_hash=fingerprint if key else None,
    )
    replayed = commit_idempotent(db, key, fingerprint, replay)
    if replayed is not None:
        return replayed
    return _load(db, expense.id)


@router.patch("/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_writer),
):
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
        occurred_at = resolve_occurred_at(data["occurred_at"], actor.via_token, datetime.now(UTC))
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
    expense.updated_at = datetime.now(UTC).isoformat()

    db.commit()
    db.refresh(expense)
    return expense


@router.delete("/{expense_id}", status_code=204)
def delete_expense(
    expense_id: int, db: Session = Depends(get_db), _: Actor = Depends(require_writer)
):
    expense = db.get(Expense, expense_id)
    if not expense or expense.deleted_at:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    expense.deleted_at = datetime.now(UTC).isoformat()
    db.commit()
    return Response(status_code=204)


@router.post("/{expense_id}/restore", response_model=ExpenseOut)
def restore_expense(
    expense_id: int, db: Session = Depends(get_db), _: Actor = Depends(require_writer)
):
    expense = db.get(Expense, expense_id)
    if not expense or not expense.deleted_at:
        raise HTTPException(status_code=404, detail="Операция не найдена в корзине")
    expense.deleted_at = None
    db.commit()
    db.refresh(expense)
    return expense
