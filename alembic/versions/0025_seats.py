# eventnxt-backend: alembic/versions/0025_seats.py
"""Seat records and seat-level checkout holds

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-30

The assigned-seat substrate:

seats — one row per physical seat in an assigned pool, GENERATED from
    zone_sections (Section A · Row 1 · seats 1..25), never hand-typed.
    Identity is (pool, section_label, row_label, seat_number) so a
    section edit re-links seats instead of destroying them —
    zone_section_id is ON DELETE SET NULL and re-attached by the sync.
    A seat is taken when a VALID ticket points at it or an unexpired
    pending / paid order holds it; is_blocked lets an organizer pull a
    broken chair from sale without touching capacity math.

order_item_seats — the buyer's chosen seats on a pending order: the
    seat-level mirror of the existing 30-minute quantity hold. Expiry
    frees seats exactly like it frees quantity (the availability
    predicate ignores expired pending orders); refunds free them because
    the predicate only counts VALID tickets.

tickets.seat_id — the column that has waited for this migration since
    the ticketing build — gains its real FK (SET NULL on seat delete).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "seating_category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "zone_section_id",
            UUID(as_uuid=True),
            sa.ForeignKey("zone_sections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("section_label", sa.String(), nullable=False),
        sa.Column("row_label", sa.String(), nullable=True),
        sa.Column("seat_number", sa.Integer(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_seats_identity",
        "seats",
        ["seating_category_id", "section_label", "row_label", "seat_number"],
        unique=False,  # row_label NULLs make a DB unique constraint porous; identity is enforced by the sync
    )

    op.create_table(
        "order_item_seats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("order_items.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("seat_id", UUID(as_uuid=True), sa.ForeignKey("seats.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_foreign_key(
        "fk_tickets_seat_id", "tickets", "seats", ["seat_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_tickets_seat_id", "tickets", ["seat_id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_seat_id", table_name="tickets")
    op.drop_constraint("fk_tickets_seat_id", "tickets", type_="foreignkey")
    op.drop_table("order_item_seats")
    op.drop_index("ix_seats_identity", table_name="seats")
    op.drop_table("seats")