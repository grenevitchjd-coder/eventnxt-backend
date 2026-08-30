# eventnxt-backend: alembic/versions/0022_seating_zone_structure.py
"""Seating zone structure: kinds, row/section labels, table math

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-30

Additive only. A seating category is now a ZONE — one price + one
treatment — and gains metadata describing what it physically is:

sales_grain (existing column) widens from 'ga' | 'seat' to
    'ga' | 'row' | 'table' | 'seat'  (no DB change needed — plain String;
    the new values are accepted by the schema layer).
row_label / section_label   descriptive structure for row zones —
    "Row 1" across "All sections", "Rows 3–4" in "Sections C–D".
    Free text in this slice; the grid builder (next slice) fills them
    systematically.
table_count / seats_per_table   structured capacity for table zones;
    capacity is DERIVED (count × seats) server-side so every existing
    consumer of `capacity` — holds, priorities, reconciliation, ticket
    pools — keeps reading one true number, untouched.

Every existing category reads as plain GA and behaves identically.
This is the substrate for assigned-seat selling: the seats table and
schematic picker (slice 3) generate from exactly this structure.
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seating_categories", sa.Column("row_label", sa.String(), nullable=True))
    op.add_column("seating_categories", sa.Column("section_label", sa.String(), nullable=True))
    op.add_column("seating_categories", sa.Column("table_count", sa.Integer(), nullable=True))
    op.add_column("seating_categories", sa.Column("seats_per_table", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("seating_categories", "seats_per_table")
    op.drop_column("seating_categories", "table_count")
    op.drop_column("seating_categories", "section_label")
    op.drop_column("seating_categories", "row_label")