"""eventnxt-backend: alembic/versions/0047_order_reserve.py

Refund-cost reserve, slice A.

orders.reserve_cents: the estimated Stripe processing cost (~2.9% + 30¢
of the charged amount) withheld from the organizer's transfer on every
DESTINATION charge — the application fee sent to Stripe is platform fee
PLUS reserve, so the reserve cash never leaves the platform's custody.
Zero on platform-account orders, $0 orders, and everything pre-0047.

orders.reserve_released_at: stamped when the event's post-event reserve
release pays this order's reserve out to the organizer (slice B). The
refund path reads it: an UNRELEASED reserve absorbs the refund's
processing cost (the platform keeps the withheld amount and the
organizer's reserve for that order simply never releases); a released
or zero reserve falls back to the pre-reserve behavior where the
platform eats the cost (rare post-event stragglers, disclosed).

The whole scheme in one line: RELEASE = SUM(reserve) OVER STILL-PAID
ORDERS. Refunded orders drop out of the sum — that's the organizer
bearing the processing cost, collected by not-paying rather than by a
debit that can fail. Mass event cancellation: every order refunds, the
release sum is zero, and no party's Stripe balance ever goes negative
on the reserve's account.

Note on numbering: the select-mode kill slides to 0048+ (same
renumbering precedent as 0043 and 0045).

Revision ID: 0047
Revises: 0046
"""
import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("reserve_cents", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("reserve_released_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "reserve_released_at")
    op.drop_column("orders", "reserve_cents")