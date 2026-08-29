"""Per-day ticket allotment pools, replacing single ticket_count + valid_dates

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-27

BREAKING for the ticket-allotment feature specifically: drops
guest_types.default_ticket_count / default_valid_dates and
guests.allotment_ticket_count / allotment_valid_dates. Any guest type or
guest that had these set loses that setting — this only affects the
ticket-distribution feature (models/sponsors giving out tickets), which
is new and lightly used, so the practical impact should be small. Nothing
about seating capacity, seating priorities, or guest identity/status is
touched.

Replaces them with two new tables — GuestTypeTicketAllotment and
GuestTicketAllotment — one row per (holder, date, quantity), so
"10 Thursday tickets, 5 Saturday tickets" are genuinely separate pools
instead of one shared number across a list of valid dates. Adds
guests.ticket_allotment_overridden (boolean) to say whether a guest uses
its own rows or inherits its type's rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("guest_types", "default_ticket_count")
    op.drop_column("guest_types", "default_valid_dates")
    op.drop_column("guests", "allotment_ticket_count")
    op.drop_column("guests", "allotment_valid_dates")

    op.add_column(
        "guests", sa.Column("ticket_allotment_overridden", sa.Boolean(), nullable=False, server_default="false")
    )

    op.create_table(
        "guest_type_ticket_allotments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "guest_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guest_types.id"), nullable=False
        ),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_guest_type_ticket_allotments_guest_type_id",
        "guest_type_ticket_allotments",
        ["guest_type_id"],
    )
    op.create_unique_constraint(
        "uq_guest_type_ticket_allotments_type_date",
        "guest_type_ticket_allotments",
        ["guest_type_id", "date"],
    )

    op.create_table(
        "guest_ticket_allotments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("guest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guests.id"), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_guest_ticket_allotments_guest_id", "guest_ticket_allotments", ["guest_id"])
    op.create_unique_constraint(
        "uq_guest_ticket_allotments_guest_date", "guest_ticket_allotments", ["guest_id", "date"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_guest_ticket_allotments_guest_date", "guest_ticket_allotments", type_="unique")
    op.drop_index("ix_guest_ticket_allotments_guest_id", table_name="guest_ticket_allotments")
    op.drop_table("guest_ticket_allotments")

    op.drop_constraint(
        "uq_guest_type_ticket_allotments_type_date", "guest_type_ticket_allotments", type_="unique"
    )
    op.drop_index(
        "ix_guest_type_ticket_allotments_guest_type_id", table_name="guest_type_ticket_allotments"
    )
    op.drop_table("guest_type_ticket_allotments")

    op.drop_column("guests", "ticket_allotment_overridden")

    op.add_column("guests", sa.Column("allotment_valid_dates", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("guests", sa.Column("allotment_ticket_count", sa.Integer(), nullable=True))
    op.add_column(
        "guest_types", sa.Column("default_valid_dates", postgresql.ARRAY(sa.String()), nullable=True)
    )
    op.add_column("guest_types", sa.Column("default_ticket_count", sa.Integer(), nullable=True))