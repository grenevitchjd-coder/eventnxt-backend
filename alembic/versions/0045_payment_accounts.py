"""eventnxt-backend: alembic/versions/0045_payment_accounts.py

Stripe Connect, slice 1: where an organization's ticket money goes.

payment_accounts: ONE row per Events360 organization (not per event) —
an org connects Stripe once and every event it runs pays out through the
same account. The row stores only the Stripe account id and three status
booleans mirrored from Stripe via the account.updated webhook; no bank
details, tax ids, or identity data ever touch this database (that all
lives with Stripe, entered on Stripe-hosted Express onboarding).

organization_id is a stored reference to Events360's Organization, not a
real foreign key (separate databases) — same convention as event_id
everywhere else in this schema.

Note on numbering: the select-mode-kill spec in docs/HANDOFF.md reserved
"0045" for itself before this migration existed; that kill becomes 0046+
whenever it gets the go (same renumbering precedent as 0043).

Revision ID: 0045
Revises: 0044
"""
import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_accounts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False, unique=True, index=True),
        sa.Column("stripe_account_id", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payment_accounts")