# eventnxt-backend: alembic/versions/0030_seat_guest_assignment.py
"""Reserved seats Slice B: hand a held seat to a named guest

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-31

One nullable FK. seats.guest_id says "this seat belongs to this comp
guest" — assignment implies reservation (the assign path also sets
is_blocked), so every sale predicate stays untouched. ondelete SET
NULL: deleting a guest frees the assignment but the seat STAYS
reserved — releasing a press hold is always a deliberate act in the
seat view, never a side effect.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seats", sa.Column("guest_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_seats_guest_id", "seats", "guests", ["guest_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_seats_guest_id", "seats", ["guest_id"])


def downgrade() -> None:
    op.drop_index("ix_seats_guest_id", table_name="seats")
    op.drop_constraint("fk_seats_guest_id", "seats", type_="foreignkey")
    op.drop_column("seats", "guest_id")