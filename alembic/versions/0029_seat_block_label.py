# eventnxt-backend: alembic/versions/0029_seat_block_label.py
"""Reserved seats: a label on seat blocks

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-31

One nullable column. is_blocked already exists (0025) and the public
picker already refuses blocked seats — this promotes the flag from a
"broken chair" kill switch into labeled organizer holds ("Press",
"Sponsor hold"). No predicate changes: blocked stays blocked; the
label is display-only context for the admin seat view and Slice B's
guest assignment.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seats", sa.Column("block_label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("seats", "block_label")