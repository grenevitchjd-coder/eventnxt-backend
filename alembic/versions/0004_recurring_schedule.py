"""Schedule items: support a daily-recurring mode, not just one-time

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_profile_schedule_items",
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("event_profile_schedule_items", sa.Column("time_of_day", sa.Time(), nullable=True))
    # event_datetime is now optional (only required for one-time items) — was
    # NOT NULL before, since every item used to be a specific date+time.
    op.alter_column("event_profile_schedule_items", "event_datetime", nullable=True)


def downgrade() -> None:
    op.alter_column("event_profile_schedule_items", "event_datetime", nullable=False)
    op.drop_column("event_profile_schedule_items", "time_of_day")
    op.drop_column("event_profile_schedule_items", "is_recurring")