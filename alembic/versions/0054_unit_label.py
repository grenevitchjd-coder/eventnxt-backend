"""eventnxt-backend: alembic/versions/0054_unit_label.py

One customizable word per seating pool for what its structural units
are actually called — "Table", "Room", "Area", "Row" — instead of the
hardcoded "Section"/"Seat" wording every display used before this.
NULL keeps today's default wording exactly as-is; nothing existing
changes until an organizer explicitly sets it on a pool.

This was the real fix behind "Champagne Lounge" and "Row 3 Preferred
Seating" showing generic or missing structural info on comp tickets —
every display (buyer checkout, comp assignment, PDFs, door scan) had
been independently hardcoding its own wording, which is exactly how
"Row 3" silently vanished from one of them (2026-09).

Revision ID: 0054
Revises: 0053
"""
import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seating_categories", sa.Column("unit_label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("seating_categories", "unit_label")