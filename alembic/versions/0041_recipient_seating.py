"""eventnxt-backend: alembic/versions/0041_recipient_seating.py

Per-allotment recipient seating override.

An allotment holder's own seating_category_id is frequently AUTO-filled
at create (priority walk parks confirmed holders somewhere), so it can't
carry organizer intent for the holder's recipients. These two columns
hold that intent explicitly: when set (from the Allotments grid), the
holder's recipients are placed in this pool/section ahead of the type's
priorities — day-aware, overflowing within the pool, falling back to
priorities when full. NULL = automatic, exactly as before.

Revision ID: 0041
Revises: 0040
"""
import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("recipient_seating_category_id", sa.UUID(), nullable=True))
    op.add_column("guests", sa.Column("recipient_section_label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "recipient_section_label")
    op.drop_column("guests", "recipient_seating_category_id")