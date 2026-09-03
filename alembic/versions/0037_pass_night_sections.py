"""eventnxt-backend: alembic/versions/0037_pass_night_sections.py

Row/GA all-days passes: a pass over UNASSIGNED nightly inventory claims
one head of section capacity per night instead of a chair. The buyer
picks a section for EACH night (different views of the show welcome) —
one claim row per (order item, night's section). Counted into
section_heads_taken, so nightly buyers see honest remaining numbers,
and freed automatically on expiry/refund exactly like seat holds
(availability stays derived, never bookkept).

Revision ID: 0037
Revises: 0036
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_item_pass_sections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("order_items.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "zone_section_id",
            UUID(as_uuid=True),
            sa.ForeignKey("zone_sections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("order_item_id", "zone_section_id", name="uq_pass_section_claim"),
    )


def downgrade() -> None:
    op.drop_table("order_item_pass_sections")