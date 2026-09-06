"""eventnxt-backend: alembic/versions/0044_referral_contacts.py

Per-recipient outreach tracking — the portal's "refer people" tab.

referral_contacts: one row per person a referrer entered by name/email.
Each carries its own token; the invite email's link is
/e/<slug>?ref=<CODE>&r=<token>, so a sale can be traced to the SPECIFIC
person invited, not just the code. clicked_at stamps on first tracked
landing.

orders.referral_contact_id / sales.referral_contact_id: which invited
person's link produced this purchase. Stamped three ways, in order of
certainty (the locked attribution policy):
  1. Native checkout with the token remembered in the buyer's browser —
     certain, last-click-wins, and a TYPED different code beats it.
  2. Paid native order with NO code and NO token (device-switch buyer):
     email-match fallback, credited only when exactly ONE contact row in
     the event matches the buyer's email — ambiguous double-invites stay
     unattributed for the organizer to see.
  3. CSV-imported external sales with no code column: same single-match
     email rule.

Revision ID: 0044
Revises: 0043
"""
import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_contacts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("event_id", sa.UUID(), nullable=False, index=True),
        sa.Column("promo_code_id", sa.UUID(), sa.ForeignKey("promo_codes.id"), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("token", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("orders", sa.Column("referral_contact_id", sa.UUID(), sa.ForeignKey("referral_contacts.id"), nullable=True))
    op.add_column("sales", sa.Column("referral_contact_id", sa.UUID(), sa.ForeignKey("referral_contacts.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("sales", "referral_contact_id")
    op.drop_column("orders", "referral_contact_id")
    op.drop_table("referral_contacts")