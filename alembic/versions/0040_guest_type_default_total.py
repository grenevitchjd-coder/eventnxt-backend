"""eventnxt-backend: alembic/versions/0040_guest_type_default_total.py

Type-level default TOTAL for choose-within-caps.

Guest types already default the per-day caps (shape defaults or explicit
per-day rows); this adds guest_types.default_spend_total so the ACROSS-
days cap is a type default too. Inherited onto guests.spend_total at add
(explicit payload wins), so "Volunteer: 2 across Thu 2 / Fri 2" is fully
configured once on the type — every added volunteer gets the chooser.

Revision ID: 0040
Revises: 0039
"""
import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guest_types", sa.Column("default_spend_total", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("guest_types", "default_spend_total")