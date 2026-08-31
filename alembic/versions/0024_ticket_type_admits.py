# eventnxt-backend: alembic/versions/0024_ticket_type_admits.py
"""Ticket types can admit N people per purchased unit

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-30

One column, additive, default 1 so every existing ticket type behaves
identically. `admits` makes one PURCHASED UNIT mint N admission codes
at fulfillment — the native shape for whole-table selling ("VIP Table,
$400, admits 4") and group packages ("4-Pack, $300, admits 4") without
any discount engine or change to checkout/hold machinery:

- `quantity` stays what it always was: purchasable UNITS.
- Fulfillment mints quantity × admits codes per order item.
- Sale rows keep recording UNITS (promo rewards and referral bonus
  tiers count purchases, not heads — inflating them would corrupt
  referral math).
- The seating summary counts HEADS: native sales as units × admits
  from paid order items; CSV-imported sales as-is.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticket_types",
        sa.Column("admits", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("ticket_types", "admits")