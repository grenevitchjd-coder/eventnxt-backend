# eventnxt-backend: alembic/versions/0032_multiday_span.py
"""Slice 1 of multi-day: ticket span, event days, dated codes

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-31

Event settings gain the three multi-day declarations (span / pricing /
seating — only span changes behavior in this slice; pricing_mode and
seating_mode drive the slice-2 composer) plus the event's day range.
Days are ISO date STRINGS, matching guests.visit_date and the per-day
allotment keys — one date dialect everywhere.

ticket_types.valid_date: NULL = whole event (every existing type), a
date = that day only (slice 2 exposes it in the composer).
tickets.valid_date: the day THIS code admits on. NULL = any day, which
grandfathers every existing ticket and keeps comps working unchanged.
A whole-event purchase at a multi-day event mints one dated code per
event day — the door's once-only semantics never change.
"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_settings", sa.Column("ticket_span", sa.String(), nullable=False, server_default="single_day"))
    op.add_column("event_settings", sa.Column("pricing_mode", sa.String(), nullable=False, server_default="uniform"))
    op.add_column("event_settings", sa.Column("seating_mode", sa.String(), nullable=False, server_default="uniform"))
    op.add_column("event_settings", sa.Column("first_day", sa.String(), nullable=True))
    op.add_column("event_settings", sa.Column("last_day", sa.String(), nullable=True))
    op.add_column("ticket_types", sa.Column("valid_date", sa.String(), nullable=True))
    op.add_column("tickets", sa.Column("valid_date", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "valid_date")
    op.drop_column("ticket_types", "valid_date")
    op.drop_column("event_settings", "last_day")
    op.drop_column("event_settings", "first_day")
    op.drop_column("event_settings", "seating_mode")
    op.drop_column("event_settings", "pricing_mode")
    op.drop_column("event_settings", "ticket_span")