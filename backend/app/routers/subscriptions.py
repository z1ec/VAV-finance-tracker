"""Subscriptions API: admin cookie and the bot token write, user cookie reads (ADR-0007)."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app import timeutil
from app.currency import to_decimal
from app.db import get_db
from app.deps import Actor, get_reader, require_writer
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
from app.models import Category, Expense, Subscription, SubscriptionCharge, utcnow_iso
from app.schemas import (
    ChargeOut,
    ChargePay,
    SubscriptionCreate,
    SubscriptionMonthlyItem,
    SubscriptionOut,
    SubscriptionsSummaryOut,
    SubscriptionUpdate,
    UpcomingItemOut,
    UpcomingOut,
)
from app.subscriptions import (
    active_subscriptions,
    charge_comment,
    first_due_on_or_after,
    last_paid_charge,
    latest_rates,
    monthly_factor,
    process_due_subscriptions,
    projected_dates,
)

router = APIRouter()

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="UUID; required for the bot token, optional for the site (ADR-0005).",
    ),
]


def _not_found(what: str = "Подписка не найдена") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=what)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _get_subscription(db: Session, subscription_id: int) -> Subscription:
    sub = db.execute(
        select(Subscription)
        .options(joinedload(Subscription.category))
        .where(Subscription.id == subscription_id)
    ).scalar_one_or_none()
    if sub is None:
        raise _not_found()
    return sub


def _charge_out(charge: SubscriptionCharge) -> ChargeOut:
    sub = charge.subscription
    return ChargeOut(
        id=charge.id,
        subscription_id=sub.id,
        subscription_name=sub.name,
        category=sub.category,
        amount_type=sub.amount_type,
        due_date=charge.due_date,
        status=charge.status,
        amount=float(charge.amount) if charge.amount is not None else None,
        currency=charge.currency,
        expense_id=charge.expense_id,
        created_at=charge.created_at,
        updated_at=charge.updated_at,
    )


def _get_charge(db: Session, charge_id: int) -> SubscriptionCharge:
    charge = db.execute(
        select(SubscriptionCharge)
        .options(joinedload(SubscriptionCharge.subscription).joinedload(Subscription.category))
        .where(SubscriptionCharge.id == charge_id)
    ).scalar_one_or_none()
    if charge is None:
        raise _not_found("Списание не найдено")
    return charge


def _validate_state(sub: Subscription, db: Session) -> None:
    """Cross-field rules for the merged state of a created or updated subscription."""
    if sub.amount_type == "fixed" and sub.amount is None:
        raise _unprocessable("amount: обязательна для подписки с фиксированной суммой")
    if sub.amount_type == "variable" and sub.amount is not None:
        raise _unprocessable("amount: для плавающей суммы не указывается (бот спросит сумму)")
    if sub.period == "weekly" and sub.anchor_day is not None:
        raise _unprocessable("anchor_day: используется только для monthly и yearly")
    if sub.period != "weekly" and sub.anchor_day is None:
        sub.anchor_day = date.fromisoformat(sub.next_payment_date).day
    if db.get(Category, sub.category_id) is None:
        raise _not_found("Категория не найдена")


# ---------- Read ----------


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(
    active: bool | None = Query(default=None, description="true/false; omit for all"),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_reader),
):
    query = select(Subscription).options(joinedload(Subscription.category))
    if active is not None:
        query = query.where(Subscription.is_active == (1 if active else 0))
    query = query.order_by(Subscription.is_active.desc(), Subscription.next_payment_date)
    return db.execute(query).scalars().all()


@router.get("/upcoming", response_model=UpcomingOut)
def upcoming(
    days: int = Query(default=7, ge=0, le=366),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_reader),
):
    """Projected charges of active subscriptions in ``max(days, remind_days_before)`` days
    (per subscription) plus every charge still waiting for an amount (``pending``)."""
    today = timeutil.local_today()
    items: list[UpcomingItemOut] = []
    for sub in active_subscriptions(db):
        window = max(days, sub.remind_days_before)
        for due in projected_dates(sub, today + timedelta(days=window)):
            days_left = (due - today).days
            items.append(
                UpcomingItemOut(
                    subscription_id=sub.id,
                    name=sub.name,
                    category=sub.category,
                    amount_type=sub.amount_type,
                    amount=float(sub.amount) if sub.amount is not None else None,
                    currency=sub.currency,
                    due_date=due.isoformat(),
                    days_left=days_left,
                    remind=days_left <= sub.remind_days_before,
                )
            )
    items.sort(key=lambda item: (item.due_date, item.name))
    pending = (
        db.execute(
            select(SubscriptionCharge)
            .options(joinedload(SubscriptionCharge.subscription).joinedload(Subscription.category))
            .where(SubscriptionCharge.status == "pending")
            .order_by(SubscriptionCharge.due_date, SubscriptionCharge.id)
        )
        .scalars()
        .all()
    )
    return UpcomingOut(
        today=today.isoformat(),
        days=days,
        upcoming=items,
        pending=[_charge_out(c) for c in pending],
    )


@router.get("/summary", response_model=SubscriptionsSummaryOut)
def summary(db: Session = Depends(get_db), actor: Actor = Depends(get_reader)):
    """Active subscriptions per month in HUF (weekly × 52/12, yearly / 12, divided by
    ``interval``) at the latest stored rates. A variable subscription counts with its last paid
    amount; without one it is excluded (``basis = "excluded"``)."""
    today = timeutil.local_today()
    rates = latest_rates(db, today)
    items: list[SubscriptionMonthlyItem] = []
    total = 0.0
    for sub in active_subscriptions(db):
        amount = sub.amount
        currency = sub.currency
        basis = "fixed"
        if sub.amount_type == "variable":
            last = last_paid_charge(db, sub)
            if last is None or last.amount is None:
                amount, basis = None, "excluded"
            else:
                amount, currency, basis = last.amount, last.currency, "last_paid"
        monthly_huf: float | None = None
        if amount is not None and currency in rates:
            value = to_decimal(amount) * rates[currency] * monthly_factor(sub.period, sub.interval)
            monthly_huf = round(float(value), 2)
            total += monthly_huf
        elif amount is not None:
            basis = "excluded"  # no stored rate for this currency yet
        items.append(
            SubscriptionMonthlyItem(
                subscription_id=sub.id,
                name=sub.name,
                amount_type=sub.amount_type,
                period=sub.period,
                interval=sub.interval,
                amount=float(amount) if amount is not None else None,
                currency=currency,
                monthly_huf=monthly_huf,
                basis=basis,
            )
        )
    included = sum(1 for item in items if item.basis != "excluded")
    return SubscriptionsSummaryOut(
        monthly_total_huf=round(total),
        active_count=len(items),
        included_count=included,
        excluded_count=len(items) - included,
        items=items,
    )


# ---------- Write ----------


@router.post("", response_model=SubscriptionOut, status_code=201)
def create_subscription(
    payload: SubscriptionCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_writer),
):
    """Creates the subscription; dates already due (``next_payment_date <= today``) are
    charged immediately, exactly like the daily job would."""
    sub = Subscription(
        name=payload.name,
        amount_type=payload.amount_type,
        amount=to_decimal(payload.amount) if payload.amount is not None else None,
        currency=payload.currency,
        category_id=payload.category_id,
        period=payload.period,
        interval=payload.interval,
        anchor_day=payload.anchor_day,
        next_payment_date=payload.next_payment_date.isoformat(),
        remind_days_before=payload.remind_days_before,
        is_active=1 if payload.is_active else 0,
        comment=payload.comment,
    )
    _validate_state(sub, db)
    db.add(sub)
    db.commit()
    process_due_subscriptions(db, subscription_id=sub.id)
    return _get_subscription(db, sub.id)


@router.patch("/{subscription_id}", response_model=SubscriptionOut)
def update_subscription(
    subscription_id: int,
    payload: SubscriptionUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_writer),
):
    """Partial update. Re-activating a subscription moves ``next_payment_date`` to the first due
    date from today on — dates missed while it was inactive are not charged."""
    sub = _get_subscription(db, subscription_id)
    data = payload.model_dump(exclude_unset=True)
    was_active = bool(sub.is_active)

    if "amount_type" in data and data["amount_type"] == "variable" and "amount" not in data:
        data["amount"] = None
    if "period" in data and data["period"] == "weekly" and "anchor_day" not in data:
        data["anchor_day"] = None
    if (
        "next_payment_date" in data
        and "anchor_day" not in data
        and data.get("period", sub.period) != "weekly"
    ):
        data["anchor_day"] = None  # re-derived from the new date below

    for key, value in data.items():
        if value is None and key in {"name", "amount_type", "currency", "category_id", "period"}:
            raise _unprocessable(f"{key}: не может быть пустым")
        if value is None and key in {
            "interval",
            "remind_days_before",
            "is_active",
            "next_payment_date",
        }:
            continue
        if key == "amount":
            value = to_decimal(value) if value is not None else None
        elif key == "next_payment_date":
            value = value.isoformat()
        elif key == "is_active":
            value = 1 if value else 0
        setattr(sub, key, value)

    if sub.is_active and not was_active and "next_payment_date" not in data:
        sub.next_payment_date = first_due_on_or_after(sub, timeutil.local_today()).isoformat()

    _validate_state(sub, db)
    sub.updated_at = utcnow_iso()
    db.commit()
    process_due_subscriptions(db, subscription_id=sub.id)
    return _get_subscription(db, sub.id)


@router.delete("/{subscription_id}", response_model=SubscriptionOut)
def deactivate_subscription(
    subscription_id: int, db: Session = Depends(get_db), actor: Actor = Depends(require_writer)
):
    """Deactivates (``is_active = false``); nothing is deleted, history and charges stay."""
    sub = _get_subscription(db, subscription_id)
    sub.is_active = 0
    sub.updated_at = utcnow_iso()
    db.commit()
    return _get_subscription(db, sub.id)


@router.post(
    "/charges/{charge_id}/pay",
    response_model=ChargeOut,
    responses={409: {"description": "Charge not pending, or Idempotency-Key reused"}},
)
def pay_charge(
    charge_id: int,
    payload: ChargePay,
    idempotency_key: IdempotencyKey = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_writer),
):
    """Records the amount of a ``pending`` charge as an expense and marks it ``paid``.
    Default currency: the subscription's; default time: 12:00 local time on the due date."""
    key = parse_idempotency_key(idempotency_key, required=actor.via_token)
    charge = _get_charge(db, charge_id)
    sub = charge.subscription
    occurred_at = resolve_occurred_at(
        payload.occurred_at,
        actor.via_token,
        timeutil.local_noon_utc(date.fromisoformat(charge.due_date)),
    )
    currency = payload.currency or charge.currency
    fingerprint = request_fingerprint(
        "charge.pay",
        {
            "charge_id": charge.id,
            "amount": normalize_decimal(payload.amount),
            "currency": currency,
            "occurred_at": occurred_at.isoformat() if payload.occurred_at else None,
            "comment": payload.comment,
        },
    )

    def replay(existing: Expense) -> ChargeOut:
        if charge.expense_id != existing.id:
            db.refresh(charge)
        if charge.expense_id != existing.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key уже использован для другого запроса",
            )
        return _charge_out(charge)

    if key:
        existing = find_by_key(db, key)
        if existing is not None:
            ensure_same_request(existing, fingerprint)
            return replay(existing)

    if charge.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Списание уже в статусе {charge.status}",
        )

    expense = build_expense(
        db,
        amount=payload.amount,
        currency=currency,
        category_id=sub.category_id,
        comment=payload.comment or charge_comment(sub),
        occurred_at=occurred_at,
        user_id=actor.user.id,
        source="telegram" if actor.via_token else "subscription",
        client_request_id=key,
        client_request_hash=fingerprint if key else None,
    )
    db.flush()
    # Conditional update: a concurrent payment of the same charge cannot also succeed.
    result = db.execute(
        update(SubscriptionCharge)
        .where(SubscriptionCharge.id == charge.id, SubscriptionCharge.status == "pending")
        .values(
            status="paid",
            amount=to_decimal(payload.amount),
            currency=currency,
            expense_id=expense.id,
            updated_at=utcnow_iso(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Списание уже оплачено")
    replayed = commit_idempotent(db, key, fingerprint, replay)
    if replayed is not None:
        return replayed
    db.expire_all()
    return _charge_out(_get_charge(db, charge_id))


@router.post(
    "/charges/{charge_id}/skip",
    response_model=ChargeOut,
    responses={409: {"description": "Charge is already paid"}},
)
def skip_charge(
    charge_id: int, db: Session = Depends(get_db), actor: Actor = Depends(require_writer)
):
    """Marks a ``pending`` charge as ``skipped`` (repeating it is a no-op)."""
    charge = _get_charge(db, charge_id)
    if charge.status == "paid":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Списание уже оплачено")
    if charge.status == "pending":
        charge.status = "skipped"
        charge.updated_at = utcnow_iso()
        db.commit()
    return _charge_out(_get_charge(db, charge_id))
