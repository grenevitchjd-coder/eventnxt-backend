"""Sale quantity (bulk-transaction support) and guest perks/comments

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-29

Additive only — three new nullable-or-defaulted columns, nothing
existing touched or narrowed.

sales.quantity defaults to 1 for every existing row, which is the
correct backfill: prior to this migration, every Sale implicitly
represented exactly one ticket (there was no way to record otherwise),
so 1 preserves the true historical meaning rather than introducing a
guess. Going forward, a bulk box-office transaction (e.g. 50 tickets in
one purchase) can be recorded accurately, and POINTS reward calculation
now multiplies by this quantity instead of treating every Sale row as a
single ticket regardless of what it actually represented.

guests.perks / guests.comments are plain free-text fields — comp items
beyond the ticket itself, and general notes — with no computation
attached.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("guests", sa.Column("perks", sa.String(), nullable=True))
    op.add_column("guests", sa.Column("comments", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "comments")
    op.drop_column("guests", "perks")
    op.drop_column("sales", "quantity")