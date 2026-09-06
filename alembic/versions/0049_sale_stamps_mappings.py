"""eventnxt-backend: alembic/versions/0049_sale_stamps_mappings.py

Imported-sales matching, made explicit (the external-events slice 3).

sales.seating_category_id + sales.event_day: the MATCH, stamped at
import time by one shared service instead of re-run as name-guessing
inside every seating query. The stamp survives pool renames, routes
per-day through the same pool_for_day families comps use, and is what
Seating summary and comp-room math now count (pre-0049 rows keep a
normalized-name fallback). SET NULL on pool delete: the sale is a
historical record; the room it pointed at isn't.

sales.is_admission: false for rows that aren't seats at all (drink
coupons, merch) — still imported, still promo/referral-attributable,
never counted into room math.

sale_type_mappings: the organizer's saved answer to "which area is
this ticket-type string?" — keyed by the NORMALIZED raw label per
event, with an optional face value (fills missing amounts so
percentage rewards can compute; box-office exports often carry no
price column) and the admission flag. Mapped once in import staging,
applied automatically on every future upload.

Revision ID: 0049
Revises: 0048
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales",
        sa.Column(
            "seating_category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("sales", sa.Column("event_day", sa.String(), nullable=True))
    op.add_column("sales", sa.Column("is_admission", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_sales_seating_category_id", "sales", ["seating_category_id"])

    op.create_table(
        "sale_type_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("raw_label", sa.String(), nullable=False),  # normalized ticket-type string
        sa.Column(
            "seating_category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("face_value_cents", sa.Integer(), nullable=True),
        sa.Column("is_admission", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", "raw_label", name="uq_sale_type_mappings_event_label"),
    )


def downgrade() -> None:
    op.drop_table("sale_type_mappings")
    op.drop_index("ix_sales_seating_category_id", table_name="sales")
    op.drop_column("sales", "is_admission")
    op.drop_column("sales", "event_day")
    op.drop_column("sales", "seating_category_id")