"""eventnxt-backend: alembic/versions/0048_order_terms_marketing.py

The Ticket Purchasing Agreement, wired into checkout.

orders.terms_accepted_at: when the buyer checked the required "I agree
to the Ticket Purchasing Agreement" box — enforced server-side (a
checkout without it is a 400), stamped at order creation. The audit
trail matters: what was agreed at purchase is what protects everyone in
a dispute. NULL on pre-0048 orders (sold before the agreement existed).

orders.marketing_opt_in: the OPTIONAL "the Organizer may email me about
future events" box — the explicit consent the agreement's §7 promises.
Default false; surfaced to the organizer on the Orders page so consent
travels with the buyer data they already see.

Revision ID: 0048
Revises: 0047
"""
import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("marketing_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("orders", "marketing_opt_in")
    op.drop_column("orders", "terms_accepted_at")