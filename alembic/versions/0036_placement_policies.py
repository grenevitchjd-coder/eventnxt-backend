# eventnxt-backend: alembic/versions/0036_placement_policies.py
"""Invites slice C2: spread/together placement + allowed sections + cohorts

Revision ID: 0036
Revises: 0035

A priority can now target a SET of sections ("Row 4, sections C/D/E —
never A/B") with a placement policy: 'together' fills sections in
declared order; 'spread' round-robins guests to the emptiest allowed
section. guests.cohort_together (on the distributing parent) controls
whether recipients from the same allocation on the same day cluster
into one section (models, small grants) or spread individually (big
sponsor blocks).
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guest_type_seating_priorities", sa.Column("allowed_sections", sa.String(), nullable=True))
    op.add_column("guest_type_seating_priorities", sa.Column("placement", sa.String(), nullable=False, server_default="together"))
    op.add_column("guests", sa.Column("cohort_together", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("guests", "cohort_together")
    op.drop_column("guest_type_seating_priorities", "placement")
    op.drop_column("guest_type_seating_priorities", "allowed_sections")