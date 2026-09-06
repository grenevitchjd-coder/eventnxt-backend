"""eventnxt-backend: alembic/versions/0046_order_destination_account.py

Stripe Connect, slice 2: destination charges.

orders.stripe_destination_account snapshots WHERE the money was routed
at charge time — the connected acct_... id for a destination charge,
NULL for a platform-account charge (the pre-Connect fallback), a $0
order, or an order that never reached payment. Snapshot, not lookup:
refunds must reverse the transfer only when a transfer actually
happened, and the org's account status at refund time proves nothing
about where an old charge settled.

Revision ID: 0046
Revises: 0045
"""
import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("stripe_destination_account", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "stripe_destination_account")