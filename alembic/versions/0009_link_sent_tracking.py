"""Track whether a guest's RSVP link has been sent

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-27

Additive only — one nullable column, no data loss, no breaking changes.
Every existing guest gets link_sent_at = null, meaning "not marked as
sent" — a reasonable default since nothing was tracking this before.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("link_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "link_sent_at")