# eventnxt-backend: alembic/versions/0034_ticket_request_day.py
"""Invites slice B: more-ticket requests can name a day

Revision ID: 0034
Revises: 0033

A day-granted guest asking for extra tickets asks for a SPECIFIC day
("2 more for Saturday"); approval bumps that day's grant instead of
party_size. NULL = the legacy whole-party request, unchanged.
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guest_ticket_requests", sa.Column("date", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("guest_ticket_requests", "date")