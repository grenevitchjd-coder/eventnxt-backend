# eventnxt-backend: alembic/versions/0027_order_item_sections.py
"""Section choice on order items — required for sectioned, unassigned types

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-30

Two columns on order_items, additive:

zone_section_id — the section the buyer chose (FK, SET NULL so section
    restructuring never breaks an order's history).
section_label   — display snapshot ("C · Row 1"-style label parts live
    on the section; the label snapshot survives any later renaming,
    same snapshot philosophy as ticket_type_name).

Checkout requires a section whenever the ticket type's pool has
sections and isn't seat-assigned ('row' and 'table' grains): quantity
is enforced against THAT section's capacity — heads (units × admits)
vs section capacity — under FOR UPDATE on the section row, the same
one-winner discipline as seats. Tickets then display the section the
way assigned tickets display their seat. GA pools (no sections) and
assigned pools (seat picking) are untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("zone_section_id", UUID(as_uuid=True), sa.ForeignKey("zone_sections.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("order_items", sa.Column("section_label", sa.String(), nullable=True))
    op.create_index("ix_order_items_zone_section_id", "order_items", ["zone_section_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_zone_section_id", table_name="order_items")
    op.drop_column("order_items", "section_label")
    op.drop_column("order_items", "zone_section_id")