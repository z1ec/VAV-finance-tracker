"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String, nullable=False, unique=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.CheckConstraint("role IN ('admin','user')", name="ck_users_role"),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("color", sa.String, nullable=False),
        sa.Column("icon", sa.String, nullable=True),
        sa.Column("is_archived", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String, nullable=False),
        sa.Column("rate_to_huf", sa.Numeric(18, 8), nullable=False),
        sa.Column("amount_huf", sa.Integer, nullable=False),
        sa.Column("rate_source", sa.String, nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("occurred_at", sa.String, nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.Column("deleted_at", sa.String, nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        sa.CheckConstraint("currency IN ('HUF','EUR','RUB')", name="ck_expenses_currency"),
        sa.CheckConstraint("rate_source IN ('api','manual')", name="ck_expenses_rate_source"),
    )
    op.create_index("ix_expenses_occurred_at", "expenses", ["occurred_at"])
    op.create_index("ix_expenses_category_id", "expenses", ["category_id"])
    op.create_index("ix_expenses_deleted_at", "expenses", ["deleted_at"])

    op.create_table(
        "exchange_rates",
        sa.Column("date", sa.String, primary_key=True),
        sa.Column("currency", sa.String, primary_key=True),
        sa.Column("rate_to_huf", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("fetched_at", sa.String, nullable=False),
        sa.CheckConstraint("source IN ('api','manual')", name="ck_rates_source"),
    )

    op.create_table(
        "budgets",
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), primary_key=True),
        sa.Column("limit_huf", sa.Numeric(18, 2), nullable=False),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.String, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("budgets")
    op.drop_table("exchange_rates")
    op.drop_index("ix_expenses_deleted_at", table_name="expenses")
    op.drop_index("ix_expenses_category_id", table_name="expenses")
    op.drop_index("ix_expenses_occurred_at", table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("categories")
    op.drop_table("users")
