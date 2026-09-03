"""eventnxt-backend: alembic/versions/0038_guest_tickets_sent.py

External-ticketing events track comps by hand: the organizer checks
availability on Tickets & seating, orders tickets on their external
platform, sends them, and marks the guest here. tickets_sent_at is that
marker — organizer record-keeping, one timestamp, clearable (same
contract as link_sent_at from 0009). Native-selling events don't need
it (codes mint automatically) but may use it however they like.

Revision ID: 0038
Revises: 0037
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("tickets_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "tickets_sent_at")