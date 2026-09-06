"""eventnxt-backend: alembic/versions/0050_all_days_sales.py

Weekend packages sold on the OUTSIDE platform (external-events slice 4).

A "Weekend Pass" row in a box-office export consumes a head EVERY
night, but a 0049 stamp points at one pool and one day — imported
packages would count into Thursday and lie about Friday and Saturday.

sale_type_mappings.all_days + sales.all_days: the organizer marks the
label "counts every night" once in staging; matching stamps such rows
to the family's BASE pool with NO single day, and the shared
imported_heads_for_pool counts them into every member of that pool's
name family — each night's Sold and comp-room math see the package
holder. Day tokens and the file-day selector are ignored for these
rows: every-night is the definition.

Revision ID: 0050
Revises: 0049
"""
import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("all_days", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("sale_type_mappings", sa.Column("all_days", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("sale_type_mappings", "all_days")
    op.drop_column("sales", "all_days")