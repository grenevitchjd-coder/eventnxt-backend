"""Guest types: move from org-scoped to event-scoped, add default seating

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-26

BREAKING DATA CHANGE: existing guest_types rows were org-wide, with no
recorded event association at all. There is no automatic way to know
which specific event each one "belongs to" under the new event-scoped
model, so existing rows are cleared rather than guessed at — organizers
will need to recreate their guest types per-event after this migration
runs. This is called out explicitly (not silent) because it's a real,
visible change for anyone who already set guest types up.

This ALSO clears existing `guests` rows: Guest.guest_type_id is a
mandatory (NOT NULL) reference, so a guest cannot exist without a valid
guest_type once the old ones are gone — there's no way to preserve guest
records in a valid state through this change. If real guest data already
exists, treat this migration as a hard reset for both guest_types and
guests, not just guest_types.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guests reference guest_types via a NOT NULL foreign key — they can't
    # be preserved in a valid state once guest_types is cleared, so they
    # have to go first.
    op.execute("DELETE FROM guests")
    op.execute("DELETE FROM guest_types")

    op.drop_index("ix_guest_types_organization_id", table_name="guest_types")
    op.drop_column("guest_types", "organization_id")

    op.add_column("guest_types", sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False))
    op.create_index("ix_guest_types_event_id", "guest_types", ["event_id"])

    op.add_column(
        "guest_types",
        sa.Column(
            "default_seating_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("guest_types", "default_seating_category_id")
    op.drop_index("ix_guest_types_event_id", table_name="guest_types")
    op.drop_column("guest_types", "event_id")
    op.add_column("guest_types", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False))
    op.create_index("ix_guest_types_organization_id", "guest_types", ["organization_id"])