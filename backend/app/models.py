from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    amount: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    rate_to_huf: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    amount_huf: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_source: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)

    category: Mapped["Category"] = relationship(back_populates="expenses")
    creator: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint("currency IN ('HUF','EUR','RUB')", name="ck_expenses_currency"),
        CheckConstraint("rate_source IN ('api','manual')", name="ck_expenses_rate_source"),
        Index("ix_expenses_occurred_at", "occurred_at"),
        Index("ix_expenses_category_id", "category_id"),
        Index("ix_expenses_deleted_at", "deleted_at"),
    )


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[str] = mapped_column(String, primary_key=True)
    rate_to_huf: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[str] = mapped_column(String, nullable=False, default=utcnow_iso)

    __table_args__ = (
        CheckConstraint("source IN ('api','manual')", name="ck_rates_source"),
    )


class Budget(Base):
    __tablename__ = "budgets"

    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), primary_key=True)
    limit_huf: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    category: Mapped["Category"] = relationship(back_populates="budget")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
