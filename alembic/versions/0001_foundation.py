"""Foundation: guest_types, seating_categories, guests

Revision ID: 0001
Revises:
Create Date: 2026-08-23

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_guest_types_organization_id", "guest_types", ["organization_id"])

    op.create_table(
        "seating_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_seating_categories_event_id", "seating_categories", ["event_id"])

    # Native enum column, defined as part of create_table only — SQLAlchemy
    # auto-manages the type's creation via the table's own DDL event. NOT
    # calling .create(bind, checkfirst=True) separately here — doing both
    # was the exact bug that caused a duplicate-type error early in the
    # Events360 build; this migration deliberately avoids repeating it.
    guest_allocation_status = postgresql.ENUM(
        "confirmed", "pending", name="guest_allocation_status"
    )

    op.create_table(
        "guests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "guest_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guest_types.id"), nullable=False
        ),
        sa.Column(
            "seating_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("seating_categories.id"),
            nullable=True,
        ),
        sa.Column(
            "allocation_status", guest_allocation_status, nullable=False, server_default="confirmed"
        ),
        sa.Column("rsvp_token", sa.String(), nullable=False, unique=True),
        sa.Column("rsvp_confirmed", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_guests_event_id", "guests", ["event_id"])
    op.create_index("ix_guests_email", "guests", ["email"])
    op.create_index("ix_guests_rsvp_token", "guests", ["rsvp_token"])


def downgrade() -> None:
    op.drop_index("ix_guests_rsvp_token", table_name="guests")
    op.drop_index("ix_guests_email", table_name="guests")
    op.drop_index("ix_guests_event_id", table_name="guests")
    op.drop_table("guests")
    postgresql.ENUM(name="guest_allocation_status").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_seating_categories_event_id", table_name="seating_categories")
    op.drop_table("seating_categories")

    op.drop_index("ix_guest_types_organization_id", table_name="guest_types")
    op.drop_table("guest_types")