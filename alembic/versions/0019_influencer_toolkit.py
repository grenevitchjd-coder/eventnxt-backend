"""Influencer toolkit: per-code buyer discounts + tracked-link click counts

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-30

Additive only.

promo_codes gains the BUYER side of a code (the referrer/reward side has
existed since 0010):
- discount_type / discount_value   optional buyer discount ('percentage'
                                   of the order, or 'flat_amount' dollars
                                   off). Null = attribution-only code —
                                   every pre-existing code keeps exactly
                                   its current behavior.
- link_clicks                      how many times this code's tracked
                                   link (/e/<slug>?ref=CODE) has been
                                   landed on — the top of the
                                   clicks -> sales -> conversion funnel.

orders gains:
- discount_cents                   SNAPSHOT of the discount applied at
                                   purchase (0 for none) — subtotal_cents
                                   stays face value, so charged amount =
                                   subtotal - discount, forever auditable
                                   even if the code's terms change later.
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("promo_codes", sa.Column("discount_type", sa.String(), nullable=True))
    op.add_column("promo_codes", sa.Column("discount_value", sa.Numeric(), nullable=True))
    op.add_column("promo_codes", sa.Column("link_clicks", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("discount_cents", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("orders", "discount_cents")
    op.drop_column("promo_codes", "link_clicks")
    op.drop_column("promo_codes", "discount_value")
    op.drop_column("promo_codes", "discount_type")