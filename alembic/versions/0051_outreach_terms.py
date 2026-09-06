"""eventnxt-backend: alembic/versions/0051_outreach_terms.py

Referrer policies (the referral program's legal surface).

guests.outreach_terms_accepted_at: when a referrer accepted the
Referral Outreach Policy — REQUIRED before the platform sends invite
emails under their words (/public/rsvp/{token}/refer 400s without it,
enforced server-side FIRST, then stamps once; the same pattern as the
purchasing agreement). The platform's SMTP carries referrer-written
text, so the acceptance timestamp is what protects EventNXT and the
organizer if a recipient complains.

Payout-terms disclosure is copy, not schema: the portal-link email and
the portal's progress tab state that current effective terms live on
the portal and future terms may differ; canonical text at the
frontend's public /terms/referral (ReferralTermsPage.jsx — the
attorney-editable copy, like /terms/purchase).

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("outreach_terms_accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "outreach_terms_accepted_at")