"""Subscriptions: date arithmetic, the daily charge job, upcoming charges, monthly total.

Dates are local calendar dates (``settings.tz``). See ADR-0007.

- ``monthly``/``yearly`` charges fall on ``anchor_day``; in a shorter month the last day of the
  month is used, and the anchor is kept for the following months (31 → 30 Apr → 31 May).
- ``weekly`` adds ``7 × interval`` days; ``anchor_day`` is not used.
- The job creates one ``subscription_charges`` row per due date (``UNIQUE(subscription_id,
  due_date)``) and advances ``next_payment_date``; fixed amounts become an expense right away
  (``source = subscription``, 12:00 local time), variable ones stay ``pending``.
"""

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import timeutil
from app.config import settings
from app.currency import to_decimal
from app.expense_service import build_expense
from app.models import ExchangeRate, Subscription, SubscriptionCharge, User, utcnow_iso

logger = logging.getLogger("app.subscriptions")

# Upper bound of due dates handled for one subscription in one run (a weekly subscription
# created years in the past must not lock the database for long).
MAX_CATCH_UP = 400


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def advance(current: date, period: str, interval: int, anchor_day: int | None) -> date:
    """The due date after ``current``."""
    if period == "weekly":
        return current + timedelta(weeks=interval)
    day = anchor_day or current.day
    if period == "monthly":
        index = current.year * 12 + (current.month - 1) + interval
        return _clamped(index // 12, index % 12 + 1, day)
    if period == "yearly":
        return _clamped(current.year + interval, current.month, day)
    raise ValueError(f"unknown period {period!r}")


def advance_sub(sub: Subscription, current: date) -> date:
    return advance(current, sub.period, sub.interval, sub.anchor_day)


def first_due_on_or_after(sub: Subscription, target: date) -> date:
    due = date.fromisoformat(sub.next_payment_date)
    for _ in range(MAX_CATCH_UP * 10):
        if due >= target:
            return due
        due = advance_sub(sub, due)
    return due


def projected_dates(sub: Subscription, until: date) -> list[date]:
    dates: list[date] = []
    due = date.fromisoformat(sub.next_payment_date)
    while due <= until and len(dates) < MAX_CATCH_UP:
        dates.append(due)
        due = advance_sub(sub, due)
    return dates


def owner_user_id(db: Session) -> int:
    """User recorded as the author of automatic charges: the bot user, else the main admin."""
    for username in (settings.bot_api_username, settings.admin_username):
        if not username:
            continue
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is not None and user.role == "admin":
            return user.id
    admin = db.execute(
        select(User).where(User.role == "admin").order_by(User.id).limit(1)
    ).scalar_one_or_none()
    if admin is None:
        raise RuntimeError("no admin user to record subscription charges")
    return admin.id


def charge_comment(sub: Subscription) -> str:
    return f"Подписка: {sub.name}"


def process_subscription(db: Session, sub: Subscription, today: date) -> int:
    """Create charges for every due date ``<= today``; commit after each date.

    Re-running is safe: the next date is advanced in the same transaction as the charge, and an
    already existing charge for a date is never duplicated.
    """
    created = 0
    due = date.fromisoformat(sub.next_payment_date)
    for _ in range(MAX_CATCH_UP):
        if due > today:
            break
        due_str = due.isoformat()
        exists = db.execute(
            select(SubscriptionCharge.id).where(
                SubscriptionCharge.subscription_id == sub.id,
                SubscriptionCharge.due_date == due_str,
            )
        ).first()
        if exists is None:
            charge = SubscriptionCharge(
                subscription_id=sub.id,
                due_date=due_str,
                status="pending",
                amount=sub.amount if sub.amount_type == "fixed" else None,
                currency=sub.currency,
            )
            db.add(charge)
            if sub.amount_type == "fixed" and sub.amount is not None:
                expense = build_expense(
                    db,
                    amount=sub.amount,
                    currency=sub.currency,
                    category_id=sub.category_id,
                    comment=charge_comment(sub),
                    occurred_at=timeutil.local_noon_utc(due),
                    user_id=owner_user_id(db),
                    source="subscription",
                )
                db.flush()
                charge.expense_id = expense.id
                charge.status = "paid"
            created += 1
        due = advance_sub(sub, due)
        sub.next_payment_date = due.isoformat()
        sub.updated_at = utcnow_iso()
        db.commit()
    return created


def process_due_subscriptions(
    db: Session, today: date | None = None, subscription_id: int | None = None
) -> int:
    """Charge every active subscription that is due; returns the number of new charges."""
    today = today or timeutil.local_today()
    query = select(Subscription).where(
        Subscription.is_active == 1, Subscription.next_payment_date <= today.isoformat()
    )
    if subscription_id is not None:
        query = query.where(Subscription.id == subscription_id)
    created = 0
    for sub_id in db.execute(query.with_only_columns(Subscription.id)).scalars().all():
        sub = db.get(Subscription, sub_id)
        if sub is None:
            continue
        try:
            created += process_subscription(db, sub, today)
        except IntegrityError:
            db.rollback()
            logger.warning("subscription_charge_conflict", extra={"subscription_id": sub_id})
        except Exception:
            db.rollback()
            logger.exception("subscription_charge_failed", extra={"subscription_id": sub_id})
    return created


# ---------- Monthly total ----------


def monthly_factor(period: str, interval: int) -> Decimal:
    if period == "weekly":
        return Decimal(52) / Decimal(12) / Decimal(interval)
    if period == "monthly":
        return Decimal(1) / Decimal(interval)
    return Decimal(1) / (Decimal(12) * Decimal(interval))


def latest_rates(db: Session, today: date) -> dict[str, Decimal]:
    """Latest stored rate per currency (no network calls)."""
    rates = {"HUF": Decimal(1)}
    for cur in ("EUR", "RUB"):
        row = db.execute(
            select(ExchangeRate)
            .where(ExchangeRate.currency == cur, ExchangeRate.date <= today.isoformat())
            .order_by(ExchangeRate.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            rates[cur] = to_decimal(row.rate_to_huf)
    return rates


def last_paid_charge(db: Session, sub: Subscription) -> SubscriptionCharge | None:
    return db.execute(
        select(SubscriptionCharge)
        .where(
            SubscriptionCharge.subscription_id == sub.id,
            SubscriptionCharge.status == "paid",
            SubscriptionCharge.amount.is_not(None),
        )
        .order_by(SubscriptionCharge.due_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def active_subscriptions(db: Session) -> list[Subscription]:
    return list(
        db.execute(
            select(Subscription)
            .options(joinedload(Subscription.category))
            .where(Subscription.is_active == 1)
            .order_by(Subscription.next_payment_date, Subscription.name)
        )
        .scalars()
        .all()
    )
