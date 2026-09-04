"""eventnxt-backend: alembic/versions/0039_guest_type_defaults.py

Guest-type shape defaults + choose-within-caps as data.

Guest types now declare SHAPE, not dates: day_scope ('single' /
'specific' / 'choose' / 'all'), a default_ticket_count, and a
default_hold_timing — actual days live per guest, so one "Influencer"
type covers a Thu-only offer and a Fri-only offer, and 'all' scope
resolves against the event's CURRENT days at mint time (self-healing
when Events360 shifts dates).

guests.spend_total makes "choose within caps" pure data: when a guest's
total is less than the sum of their per-day grants, the RSVP page lets
them choose where to spend it — universally, replacing the invite /
select mode split (Auto's legacy allotment-holder inference retires in
the same slice; see comp_tickets.effective_guest_mode).

Revision ID: 0039
Revises: 0038
"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guest_types", sa.Column("day_scope", sa.String(), nullable=True))
    op.add_column("guest_types", sa.Column("default_ticket_count", sa.Integer(), nullable=True))
    op.add_column("guest_types", sa.Column("default_hold_timing", sa.String(), nullable=True))
    op.add_column("guests", sa.Column("spend_total", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "spend_total")
    op.drop_column("guest_types", "default_hold_timing")
    op.drop_column("guest_types", "default_ticket_count")
    op.drop_column("guest_types", "day_scope")