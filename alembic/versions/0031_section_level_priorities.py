# eventnxt-backend: alembic/versions/0031_section_level_priorities.py
"""Slice C: guest-type priorities and guest placement at SECTION level

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-31

Two nullable String columns, both storing a section LABEL rather than a
zone_sections FK — deliberately. The sections editor replaces section
rows wholesale on every save, so an FK would orphan on each structure
change; labels are the durable identity here, exactly the philosophy
seats use ((pool, section_label, …) survives restructures). A priority
whose label no longer exists is simply skipped by the resolver — it
degrades, never breaks.

- guest_type_seating_priorities.section_label: NULL = whole pool (the
  behavior every existing row keeps); "B" = only Section B of that pool.
- guests.section_label: which section within their pool a comp guest was
  placed in (by the resolver or by the organizer). NULL = floats at pool
  level, exactly as all comps did before this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guest_type_seating_priorities", sa.Column("section_label", sa.String(), nullable=True))
    op.add_column("guests", sa.Column("section_label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "section_label")
    op.drop_column("guest_type_seating_priorities", "section_label")