# eventnxt-backend: alembic/versions/0035_guest_hold_timing.py
"""Invites slice C1: hold timing — when a guest's heads block sales

Revision ID: 0035
Revises: 0034

'now'        — heads held from the moment the guest is saved (default,
               per the invite design): pending AND confirmed count
               against buyer availability.
'on_confirm' — heads count only once the guest confirms (the old
               implicit behavior).
'later'      — never counts while pending; the organizer flips it (or
               confirmation does).
Existing guests get 'now' via the server default — note this makes
already-pending guests start protecting their sections on deploy.
"""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("hold_timing", sa.String(), nullable=False, server_default="now"))


def downgrade() -> None:
    op.drop_column("guests", "hold_timing")