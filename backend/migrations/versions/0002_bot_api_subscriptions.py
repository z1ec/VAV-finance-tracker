"""bot API: expense source and idempotency key; subscriptions and their charges

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12

Existing expenses are back-filled with ``source = 'web'`` (column default). Plain
``ALTER TABLE … ADD COLUMN`` is used so the populated ``expenses`` table is not rebuilt.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE expenses ADD COLUMN source VARCHAR NOT NULL DEFAULT 'web' "
        "CONSTRAINT ck_expenses_source CHECK (source IN ('web','telegram','subscription'))"
    )
    op.add_column("expenses", sa.Column("client_request_id", sa.String, nullable=True))
    op.add_column("expenses", sa.Column("client_request_hash", sa.String, nullable=True))
    op.create_index("ux_expenses_client_request_id", "expenses", ["client_request_id"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("amount_type", sa.String, nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String, nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("period", sa.String, nullable=False),
        sa.Column("interval", sa.Integer, nullable=False, server_default="1"),
        sa.Column("anchor_day", sa.Integer, nullable=True),
        sa.Column("next_payment_date", sa.String, nullable=False),
        sa.Column("remind_days_before", sa.Integer, nullable=False, server_default="3"),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.CheckConstraint(
            "amount_type IN ('fixed','variable')", name="ck_subscriptions_amount_type"
        ),
        sa.CheckConstraint(
            "(amount_type = 'fixed' AND amount IS NOT NULL AND amount > 0)"
            " OR (amount_type = 'variable' AND amount IS NULL)",
            name="ck_subscriptions_amount",
        ),
        sa.CheckConstraint("currency IN ('HUF','EUR','RUB')", name="ck_subscriptions_currency"),
        sa.CheckConstraint(
            "period IN ('weekly','monthly','yearly')", name="ck_subscriptions_period"
        ),
        sa.CheckConstraint("interval >= 1", name="ck_subscriptions_interval"),
        sa.CheckConstraint(
            "anchor_day IS NULL OR (anchor_day BETWEEN 1 AND 31)", name="ck_subscriptions_anchor"
        ),
        sa.CheckConstraint("remind_days_before >= 0", name="ck_subscriptions_remind"),
    )
    op.create_index("ix_subscriptions_next_payment_date", "subscriptions", ["next_payment_date"])

    op.create_table(
        "subscription_charges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subscription_id", sa.Integer, sa.ForeignKey("subscriptions.id"), nullable=False),
        sa.Column("due_date", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String, nullable=False),
        sa.Column("expense_id", sa.Integer, sa.ForeignKey("expenses.id"), nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.UniqueConstraint("subscription_id", "due_date", name="uq_subscription_charges_due"),
        sa.CheckConstraint(
            "status IN ('pending','paid','skipped')", name="ck_subscription_charges_status"
        ),
        sa.CheckConstraint(
            "currency IN ('HUF','EUR','RUB')", name="ck_subscription_charges_currency"
        ),
    )
    op.create_index("ix_subscription_charges_status", "subscription_charges", ["status"])


def downgrade() -> None:
    op.drop_index("ix_subscription_charges_status", table_name="subscription_charges")
    op.drop_table("subscription_charges")
    op.drop_index("ix_subscriptions_next_payment_date", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ux_expenses_client_request_id", table_name="expenses")
    # SQLite ≥ 3.35 drops columns in place (the CHECK belongs to the dropped column itself).
    op.execute("ALTER TABLE expenses DROP COLUMN client_request_hash")
    op.execute("ALTER TABLE expenses DROP COLUMN client_request_id")
    op.execute("ALTER TABLE expenses DROP COLUMN source")
