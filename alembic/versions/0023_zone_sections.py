# eventnxt-backend: alembic/versions/0023_zone_sections.py
"""Per-section breakdown under seating pools

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-30

Additive only. A seating pool (seating_categories row) can now carry an
ordered list of member SECTIONS — "Row 1 · Section A · 25", "Row 1 ·
Section B · 25" — entered inline from the ticket-type composer:

zone_sections
- section_label / row_label   what the piece is
- capacity                    seats in this piece; for table sections,
                              DERIVED = table_count × seats_per_table
- table_count/seats_per_table per-section table math (table basis)
- sort_order                  display order

When a pool has sections, the pool's own `capacity` is DERIVED as the
sum of its sections (recomputed on every section write), so the single
number every existing consumer reads — holds, priorities, ticket
inventory, reconciliation — stays authoritative and untouched. Pools
with no sections behave exactly as before (GA/VIP named areas need no
breakdown). Slice 3 generates seat records directly from these rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zone_sections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "seating_category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("section_label", sa.String(), nullable=False),
        sa.Column("row_label", sa.String(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=True),
        sa.Column("seats_per_table", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("zone_sections")