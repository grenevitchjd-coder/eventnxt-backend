# eventnxt-backend: alembic/versions/0021_guest_modes_comp_tickets.py
"""Guest modes, the needs-seating queue, comp tickets, and ticket requests

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-30

Additive, with three column-nullability relaxations on tickets. Every
default preserves current behavior:

guest_types.guest_mode / guests.guest_mode  (both nullable String)
    The explicit guest experience: 'invite' (here's a ticket, are you
    coming?), 'distribute' (assign your allotment to named people),
    'select' (pick your own day). NULL = legacy/derived — a guest with no
    explicit mode behaves exactly as today: allotment holders distribute,
    everyone else is a plain invite. Guest override beats type default.

guests.needs_seating  (bool, default false)
    The soft landing for an RSVP "yes" that can't seat: instead of the
    old hard "no room left" error, the yes is RECORDED (rsvp_confirmed =
    'yes'), allocation stays PENDING (so capacity math never counts a
    seat that doesn't exist), and this flag puts them in the organizer's
    Needs-seating queue to resolve.

tickets.order_id / order_item_id / ticket_type_id  ->  nullable
tickets.guest_id  (nullable FK -> guests)
    Comp tickets: same tickets table, same codes, same door scan — just
    minted from a guest instead of an order. A ticket has either an order
    lineage or a guest, never neither (enforced in code; existing rows
    all keep their order lineage untouched).

guest_ticket_requests  (new table)
    An RSVP guest asking for more tickets — quantity + optional note,
    pending until the organizer approves (party_size grows, extra
    tickets mint) or denies.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guest_types", sa.Column("guest_mode", sa.String(), nullable=True))
    op.add_column("guests", sa.Column("guest_mode", sa.String(), nullable=True))
    op.add_column(
        "guests",
        sa.Column("needs_seating", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.alter_column("tickets", "order_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.alter_column("tickets", "order_item_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.alter_column("tickets", "ticket_type_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column("tickets", sa.Column("guest_id", UUID(as_uuid=True), sa.ForeignKey("guests.id"), nullable=True))
    op.create_index("ix_tickets_guest_id", "tickets", ["guest_id"])

    op.create_table(
        "guest_ticket_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("guest_id", UUID(as_uuid=True), sa.ForeignKey("guests.id"), nullable=False, index=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("guest_ticket_requests")
    op.drop_index("ix_tickets_guest_id", table_name="tickets")
    op.drop_column("tickets", "guest_id")
    op.alter_column("tickets", "ticket_type_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.alter_column("tickets", "order_item_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.alter_column("tickets", "order_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_column("guests", "needs_seating")
    op.drop_column("guests", "guest_mode")
    op.drop_column("guest_types", "guest_mode")