"""Event profile: logo, cached dates, contact/social links, schedule, gallery

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_profiles", sa.Column("logo_url", sa.String(), nullable=True))
    op.add_column("event_profiles", sa.Column("cached_start_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("event_profiles", sa.Column("cached_end_date", sa.DateTime(timezone=True), nullable=True))

    link_kind = postgresql.ENUM("contact", "social", name="link_kind")

    op.create_table(
        "event_profile_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_profiles.id"),
            nullable=False,
        ),
        sa.Column("kind", link_kind, nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_event_profile_links_event_profile_id", "event_profile_links", ["event_profile_id"])

    op.create_table(
        "event_profile_schedule_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_profiles.id"),
            nullable=False,
        ),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("event_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_event_profile_schedule_items_event_profile_id",
        "event_profile_schedule_items",
        ["event_profile_id"],
    )

    op.create_table(
        "event_profile_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_profiles.id"),
            nullable=False,
        ),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_event_profile_photos_event_profile_id", "event_profile_photos", ["event_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_event_profile_photos_event_profile_id", table_name="event_profile_photos")
    op.drop_table("event_profile_photos")

    op.drop_index(
        "ix_event_profile_schedule_items_event_profile_id", table_name="event_profile_schedule_items"
    )
    op.drop_table("event_profile_schedule_items")

    op.drop_index("ix_event_profile_links_event_profile_id", table_name="event_profile_links")
    op.drop_table("event_profile_links")
    postgresql.ENUM(name="link_kind").drop(op.get_bind(), checkfirst=True)

    op.drop_column("event_profiles", "cached_end_date")
    op.drop_column("event_profiles", "cached_start_date")
    op.drop_column("event_profiles", "logo_url")