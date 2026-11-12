from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)

    __table_args__ = (CheckConstraint("role IN ('admin','user')", name="ck_users_role"),)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String, nullable=False)
    icon: Mapped[str | None] = mapped_column(String, nullable=True)
    is_archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)

    expenses: Mapped[list["Expense"]] = relationship(back_populates="category")
    budget: Mapped["Budget"] = relationship(back_populates="category", uselist=False)


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    rate_to_huf: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    amount_huf: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_source: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Channel the expense came from: web (site), telegram (bot token), subscription (scheduler).
    source: Mapped[str] = mapped_column(String, nullable=False, default="web", server_default="web")
    # Idempotency-Key of the request that created the row (ADR-0005) and a hash of its content.
    client_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    client_request_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    category: Mapped["Category"] = relationship(back_populates="expenses")
    creator: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint("currency IN ('HUF','EUR','RUB')", name="ck_expenses_currency"),
        CheckConstraint("rate_source IN ('api','manual')", name="ck_expenses_rate_source"),
        CheckConstraint("source IN ('web','telegram','subscription')", name="ck_expenses_source"),
        Index("ix_expenses_occurred_at", "occurred_at"),
        Index("ix_expenses_category_id", "category_id"),
        Index("ix_expenses_deleted_at", "deleted_at"),
        Index("ux_expenses_client_request_id", "client_request_id", unique=True),
    )


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[str] = mapped_column(String, primary_key=True)
    rate_to_huf: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)

    __table_args__ = (CheckConstraint("source IN ('api','manual')", name="ck_rates_source"),)


class Budget(Base):
    __tablename__ = "budgets"

    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), primary_key=True)
    limit_huf: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    category: Mapped["Category"] = relationship(back_populates="budget")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)


class Subscription(Base):
    """A recurring payment; see ADR-0007 for the date rules (``anchor_day``)."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    amount_type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    period: Mapped[str] = mapped_column(String, nullable=False)
    interval: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    anchor_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Local calendar date (YYYY-MM-DD, Europe/Budapest) of the next charge.
    next_payment_date: Mapped[str] = mapped_column(String, nullable=False)
    remind_days_before: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)

    category: Mapped["Category"] = relationship()
    charges: Mapped[list["SubscriptionCharge"]] = relationship(back_populates="subscription")

    __table_args__ = (
        CheckConstraint("amount_type IN ('fixed','variable')", name="ck_subscriptions_amount_type"),
        CheckConstraint(
            "(amount_type = 'fixed' AND amount IS NOT NULL AND amount > 0)"
            " OR (amount_type = 'variable' AND amount IS NULL)",
            name="ck_subscriptions_amount",
        ),
        CheckConstraint("currency IN ('HUF','EUR','RUB')", name="ck_subscriptions_currency"),
        CheckConstraint("period IN ('weekly','monthly','yearly')", name="ck_subscriptions_period"),
        CheckConstraint("interval >= 1", name="ck_subscriptions_interval"),
        CheckConstraint(
            "anchor_day IS NULL OR (anchor_day BETWEEN 1 AND 31)", name="ck_subscriptions_anchor"
        ),
        CheckConstraint("remind_days_before >= 0", name="ck_subscriptions_remind"),
        Index("ix_subscriptions_next_payment_date", "next_payment_date"),
    )


class SubscriptionCharge(Base):
    """One due date of a subscription: pending (waiting for an amount), paid or skipped."""

    __tablename__ = "subscription_charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), nullable=False)
    due_date: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)

    subscription: Mapped["Subscription"] = relationship(back_populates="charges")
    expense: Mapped["Expense | None"] = relationship()

    __table_args__ = (
        UniqueConstraint("subscription_id", "due_date", name="uq_subscription_charges_due"),
        CheckConstraint(
            "status IN ('pending','paid','skipped')", name="ck_subscription_charges_status"
        ),
        CheckConstraint("currency IN ('HUF','EUR','RUB')", name="ck_subscription_charges_currency"),
        Index("ix_subscription_charges_status", "status"),
    )
