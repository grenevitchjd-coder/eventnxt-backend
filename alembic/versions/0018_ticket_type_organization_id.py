"""ticket_types.organization_id — the org snapshot checkout copies onto orders

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-30

One additive column. Why it exists: orders snapshot organization_id for
the per-organizer ledger, but checkout is UNAUTHENTICATED — a buyer has
no token that knows the org. The organizer, however, is authenticated
when they create a ticket type (require_event_access fetches the event
from Events360, whose payload includes organization_id) — so the org is
snapshotted here at creation time, and checkout simply copies it down
onto each order.

nullable=False is safe because this runs before any UI to create ticket
types exists — the table is empty in every environment.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticket_types",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_index("ix_ticket_types_organization_id", "ticket_types", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_types_organization_id", table_name="ticket_types")
    op.drop_column("ticket_types", "organization_id")