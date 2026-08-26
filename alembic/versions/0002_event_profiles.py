"""Event profiles: public-facing content and shareable pages

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("banner_photo_url", sa.String(), nullable=True),
        sa.Column("external_ticket_url", sa.String(), nullable=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_event_profiles_event_id", "event_profiles", ["event_id"])
    op.create_index("ix_event_profiles_slug", "event_profiles", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_event_profiles_slug", table_name="event_profiles")
    op.drop_index("ix_event_profiles_event_id", table_name="event_profiles")
    op.drop_table("event_profiles")