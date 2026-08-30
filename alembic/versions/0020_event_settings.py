# eventnxt-backend: alembic/versions/0020_event_settings.py
"""Event operating profile: the event_settings table

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-30

Additive only — no existing table is touched.

One row per event, created lazily the first time settings are read or
written; a missing row means "never explicitly chosen" and the settings
endpoint infers values from what the event already does (native ticket
types -> native, external ticket link -> external). So no backfill, and
every existing event keeps exactly its current behavior.

Columns:
- ticketing_mode  'native' | 'external' | 'invite_only'
- sales_source    'native' | 'csv' | 'api'
- comp_delivery   'rsvp_required' | 'auto_send'

Plain strings, not PG enums, so future values are a code change rather
than a migration (same tradeoff as event_profiles.logo_position).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False, unique=True, index=True),
        sa.Column("ticketing_mode", sa.String(), nullable=False, server_default="native"),
        sa.Column("sales_source", sa.String(), nullable=False, server_default="native"),
        sa.Column("comp_delivery", sa.String(), nullable=False, server_default="rsvp_required"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("event_settings")