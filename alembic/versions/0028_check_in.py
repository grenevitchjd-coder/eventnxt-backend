# eventnxt-backend: alembic/versions/0028_check_in.py
"""Door check-in: one timestamp per ticket

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-30

One nullable column. A ticket is checked in when checked_in_at is set —
status stays VALID, so every existing predicate (seat taken, Find My
Tickets, refund release) is untouched. Redemption is enforced
once-only under FOR UPDATE on the ticket row: two doors scanning the
same code produce exactly one admit and one clear "already checked in
at HH:MM" refusal.
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "checked_in_at")