"""eventnxt-backend: alembic/versions/0053_cash_door_sales.py

Cash door sales — an organizer staff member (same access as Guest list)
rings up a walk-up ticket from a tablet/computer at the door, buyer pays
cash in person, order is paid instantly with no Stripe involved at all.

orders.payment_method: 'stripe' (default, backfilled on every existing
row) or 'cash'. Everything downstream — availability math, Earnings,
Promo tracking, Referral attribution, refunds — already keys off Order
rows generically and needs no changes; this column is purely how the
Door sales screen and the Orders page tell the two apart.

orders.sold_by_user_id / sold_by_name: WHO rang it up, snapshotted at
sale time (same snapshot discipline as ticket_type_name, organizer_net,
etc.) — a name lookup against Events360 later would need another call
and could drift if the staffer's name changes; NULL on every order that
isn't a cash door sale.

No platform fee is charged on cash sales for now (organizer's explicit
call) — cash orders simply get platform_fee_cents=0, reserve_cents=0,
organizer_net_cents = subtotal - discount, same shape the code already
knows how to sum.

Revision ID: 0053
Revises: 0052
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("payment_method", sa.String(), nullable=False, server_default="stripe"),
    )
    op.add_column(
        "orders",
        sa.Column("sold_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("orders", sa.Column("sold_by_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "sold_by_name")
    op.drop_column("orders", "sold_by_user_id")
    op.drop_column("orders", "payment_method")