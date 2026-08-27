"""Ticket allotments: guest types/guests can hold and distribute tickets

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-27

Adds:
- guest_types.default_ticket_count / default_valid_dates — the allotment
  a guest of this type gets by default, to hand out via their RSVP page.
- guests.allotment_ticket_count / allotment_valid_dates — per-guest
  override of the above (null = not an allotment holder).
- guests.party_size — how many tickets/seats this one guest record
  consumes (default 1; lets a distributor put more than one ticket
  under a single recipient instead of duplicate rows).
- guests.visit_date — which specific day this guest's own ticket is for.
- guests.allocated_by_guest_id — self-referential FK, set when this
  guest was created via someone else's distribution.
- a new DECLINED value on guest_allocation_status, so a guest can say no
  on their RSVP page (distinct from PENDING, which means "hasn't
  responded yet").

Additive only — no columns dropped, nothing existing changes shape.
Existing guests get party_size=1 (already true of every guest today,
since there was no way to be anything else) and everything else null,
which is the correct "not an allotment, single ordinary ticket" default.

The enum addition can't run inside a transaction block in Postgres, so
this migration commits first, adds the enum value on its own, then
continues with the (fully additive, low-risk) column changes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction block.
    op.execute("COMMIT")
    op.execute("ALTER TYPE guest_allocation_status ADD VALUE IF NOT EXISTS 'declined'")

    op.add_column(
        "guest_types", sa.Column("default_ticket_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "guest_types",
        sa.Column("default_valid_dates", postgresql.ARRAY(sa.String()), nullable=True),
    )

    op.add_column("guests", sa.Column("allotment_ticket_count", sa.Integer(), nullable=True))
    op.add_column(
        "guests", sa.Column("allotment_valid_dates", postgresql.ARRAY(sa.String()), nullable=True)
    )
    op.add_column(
        "guests", sa.Column("party_size", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("guests", sa.Column("visit_date", sa.String(), nullable=True))
    op.add_column(
        "guests",
        sa.Column(
            "allocated_by_guest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guests.id"), nullable=True
        ),
    )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE — reversing the enum
    # addition isn't supported here. The column changes below are fully
    # reversible on their own.
    op.drop_column("guests", "allocated_by_guest_id")
    op.drop_column("guests", "visit_date")
    op.drop_column("guests", "party_size")
    op.drop_column("guests", "allotment_valid_dates")
    op.drop_column("guests", "allotment_ticket_count")
    op.drop_column("guest_types", "default_valid_dates")
    op.drop_column("guest_types", "default_ticket_count")