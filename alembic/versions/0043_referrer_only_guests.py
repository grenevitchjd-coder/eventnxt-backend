"""eventnxt-backend: alembic/versions/0043_referrer_only_guests.py

Referrer-only guests — people who exist to hold promo codes, not to
attend.

The Referral Setup page adds "referral people from scratch": influencers
and salespeople who may never set foot in the event. They're still Guest
rows (a referrer has always been a Guest — that reuse stands), but the
invite/allotment/door machinery must not treat them as attendees:

- the Invites page and its bulk/single invite emails skip them
- the Allotments portal sender skips them
- the door roster (Guest list page) omits them

is_referrer_only is a flag, not a guest_mode: mode drives ticket
minting and RSVP shape, and a referrer-only person has neither. A guest
who both attends AND refers simply stays a normal guest holding codes —
this flag marks only the people created from Referral Setup with no
attendance side at all. Intent gets its own column (the 0041 lesson).

Revision ID: 0043
Revises: 0042
"""
import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guests",
        sa.Column("is_referrer_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # A referrer-only person has no offering type — nothing is offered.
    # Safe to relax: the two shared helpers (effective_allotment,
    # effective_guest_mode) already guard on guest_type_id, and the flag
    # fences these guests out of every attendee flow.
    op.alter_column("guests", "guest_type_id", existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    # Typeless guests can't survive NOT NULL — they're all referrer-only.
    op.execute("DELETE FROM promo_codes WHERE guest_id IN (SELECT id FROM guests WHERE guest_type_id IS NULL)")
    op.execute("DELETE FROM guests WHERE guest_type_id IS NULL")
    op.alter_column("guests", "guest_type_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("guests", "is_referrer_only")