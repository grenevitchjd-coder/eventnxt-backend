"""Guest types: replace single default seating with an ordered priority list

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27

BREAKING CHANGE, small in scope: drops guest_types.default_seating_category_id.
Any guest type that had a single default loses that setting — organizers
will need to re-set their seating preferences as an ordered list after
this migration. Nothing else is affected (guests, seating categories, and
existing guest type names/rows all carry over unchanged).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("guest_types", "default_seating_category_id")

    op.create_table(
        "guest_type_seating_priorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "guest_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guest_types.id"), nullable=False
        ),
        sa.Column(
            "seating_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id"),
            nullable=False,
        ),
        sa.Column("priority_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_guest_type_seating_priorities_guest_type_id",
        "guest_type_seating_priorities",
        ["guest_type_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guest_type_seating_priorities_guest_type_id", table_name="guest_type_seating_priorities"
    )
    op.drop_table("guest_type_seating_priorities")
    op.add_column(
        "guest_types",
        sa.Column(
            "default_seating_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id"),
            nullable=True,
        ),
    )