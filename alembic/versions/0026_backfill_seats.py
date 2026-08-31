# eventnxt-backend: alembic/versions/0026_backfill_seats.py
"""Backfill seat records for assigned pools created before 0025

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-30

Data-only migration, no schema change. Seats normally generate when a
pool's sections are saved — but pools that were already assigned
(sales_grain 'seat') before 0025 shipped never had that save happen, so
their seat picker shows an empty section list. This generates their
seats now, idempotently (only missing seats are inserted):

- Pools WITH section rows: one seat per section per 1..capacity.
- Pools WITHOUT sections (pre-composer era): the pool itself acts as one
  implicit section — labeled by its section_label, else its name —
  numbered 1..capacity. Runtime sync now mirrors this rule too.

Safe to re-run; inserts nothing that already exists.
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pools with sections: seat per (section, 1..capacity)
    op.execute(
        """
        INSERT INTO seats (id, event_id, seating_category_id, zone_section_id,
                           section_label, row_label, seat_number, is_blocked)
        SELECT gen_random_uuid(), sc.event_id, zs.seating_category_id, zs.id,
               zs.section_label, zs.row_label, gs.n, false
        FROM zone_sections zs
        JOIN seating_categories sc ON sc.id = zs.seating_category_id
        CROSS JOIN LATERAL generate_series(1, zs.capacity) AS gs(n)
        WHERE sc.sales_grain = 'seat'
          AND NOT EXISTS (
              SELECT 1 FROM seats s
              WHERE s.seating_category_id = zs.seating_category_id
                AND s.section_label = zs.section_label
                AND s.row_label IS NOT DISTINCT FROM zs.row_label
                AND s.seat_number = gs.n
          )
        """
    )
    # Sectionless assigned pools: the pool is one implicit section
    op.execute(
        """
        INSERT INTO seats (id, event_id, seating_category_id, zone_section_id,
                           section_label, row_label, seat_number, is_blocked)
        SELECT gen_random_uuid(), sc.event_id, sc.id, NULL,
               COALESCE(sc.section_label, sc.name), sc.row_label, gs.n, false
        FROM seating_categories sc
        CROSS JOIN LATERAL generate_series(1, sc.capacity) AS gs(n)
        WHERE sc.sales_grain = 'seat'
          AND NOT EXISTS (SELECT 1 FROM zone_sections z WHERE z.seating_category_id = sc.id)
          AND NOT EXISTS (
              SELECT 1 FROM seats s
              WHERE s.seating_category_id = sc.id
                AND s.section_label = COALESCE(sc.section_label, sc.name)
                AND s.row_label IS NOT DISTINCT FROM sc.row_label
                AND s.seat_number = gs.n
          )
        """
    )


def downgrade() -> None:
    pass  # backfilled data is indistinguishable from normally-generated seats