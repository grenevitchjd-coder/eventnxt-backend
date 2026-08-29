"""Points reward type: per-ticket-type earning rates, plus ticket_type on sales

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-27

Additive: new table, a new nullable column on sales, and a new enum
value on reward_type. Also relaxes promo_codes.reward_value from
required to nullable, since a POINTS code doesn't use that single-number
field at all — its rates live in the new per-ticket-type table instead.
Nothing existing is dropped or narrowed, so no data loss for any
existing flat/percentage/free_tickets code.

Like the guest_allocation_status enum addition in migration 0007, adding
a value to a Postgres enum can't run inside a transaction block, so this
commits first, adds the value on its own, then continues.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE reward_type ADD VALUE IF NOT EXISTS 'points'")

    op.add_column("sales", sa.Column("ticket_type", sa.String(), nullable=True))
    op.alter_column("promo_codes", "reward_value", nullable=True)

    op.create_table(
        "promo_code_points_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=False
        ),
        sa.Column("ticket_type", sa.String(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_promo_code_points_rates_promo_code_id", "promo_code_points_rates", ["promo_code_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_promo_code_points_rates_promo_code_id", table_name="promo_code_points_rates")
    op.drop_table("promo_code_points_rates")

    op.alter_column("promo_codes", "reward_value", nullable=False)
    op.drop_column("sales", "ticket_type")

    # Postgres has no ALTER TYPE ... DROP VALUE — reversing the enum
    # addition isn't supported here, matching the same limitation noted
    # in migration 0007.