"""Volume bonus tiers: event-wide default + per-code override, auto-awarded

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-27

Additive only — three new tables plus one new boolean column on
promo_codes, nothing existing touched.

EventBonusTier is the organizer's default volume-bonus structure
("sell 20 tickets, get a $50 bonus"). PromoCodeBonusTier is a specific
code's own override of that (only used when
promo_codes.bonus_tiers_overridden is True — same default+override
mechanism as ticket_allotment_overridden on guests). BonusAward is the
audit trail of bonuses actually given, snapshotted so later config
changes don't rewrite history, and is what prevents the same tier from
being awarded twice when sales get re-imported.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "promo_codes", sa.Column("bonus_tiers_overridden", sa.Boolean(), nullable=False, server_default="false")
    )

    op.create_table(
        "event_bonus_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tickets_required", sa.Integer(), nullable=False),
        sa.Column("bonus_value", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_event_bonus_tiers_event_id", "event_bonus_tiers", ["event_id"])

    op.create_table(
        "promo_code_bonus_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=False
        ),
        sa.Column("tickets_required", sa.Integer(), nullable=False),
        sa.Column("bonus_value", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_promo_code_bonus_tiers_promo_code_id", "promo_code_bonus_tiers", ["promo_code_id"]
    )

    op.create_table(
        "bonus_awards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=False
        ),
        sa.Column("tickets_required", sa.Integer(), nullable=False),
        sa.Column("bonus_value", sa.Numeric(), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bonus_awards_promo_code_id", "bonus_awards", ["promo_code_id"])


def downgrade() -> None:
    op.drop_index("ix_bonus_awards_promo_code_id", table_name="bonus_awards")
    op.drop_table("bonus_awards")

    op.drop_index("ix_promo_code_bonus_tiers_promo_code_id", table_name="promo_code_bonus_tiers")
    op.drop_table("promo_code_bonus_tiers")

    op.drop_index("ix_event_bonus_tiers_event_id", table_name="event_bonus_tiers")
    op.drop_table("event_bonus_tiers")

    op.drop_column("promo_codes", "bonus_tiers_overridden")