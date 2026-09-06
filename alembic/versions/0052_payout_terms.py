"""eventnxt-backend: alembic/versions/0052_payout_terms.py

The payout-terms acceptance WALL (upgrading 0051's disclosure).

A referrer must accept the Referral Program Terms — checkbox PLUS typed
full legal name, e-signature style — before anything referral-facing
exists for them: the portal shows only the acceptance wall, the payload
withholds their codes, the portal-link email carries no codes or share
links, and /refer and /redeem 400. The typed name is recorded alongside
the timestamp as the acceptance record.

guests.payout_terms_accepted_at + guests.payout_terms_legal_name.

(The select-mode kill renumbers to 0053+ — chapter five.)

Revision ID: 0052
Revises: 0051
"""
import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("payout_terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("guests", sa.Column("payout_terms_legal_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "payout_terms_legal_name")
    op.drop_column("guests", "payout_terms_accepted_at")