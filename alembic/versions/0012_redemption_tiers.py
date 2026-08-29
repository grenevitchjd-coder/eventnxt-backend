"""Redemption tiers: shared point thresholds, per-code reward options, redemption records

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-27

Additive only — three new tables, nothing existing touched.

RedemptionTier is the event-wide shared threshold structure ("at 50
points," "at 100 points" — same set for everyone). PromoCodeRedemptionOption
is per-code: what a specific referrer's code actually offers at a given
shared tier (cash value and/or ticket count — a code with no row for a
tier just doesn't participate in it). RewardRedemption is the actual
claim event, snapshotting the tier/option values at the moment of
redemption so later config changes don't rewrite history, and is what
points-spent (and therefore available balance) gets computed from.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "redemption_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("points_required", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_redemption_tiers_event_id", "redemption_tiers", ["event_id"])

    op.create_table(
        "promo_code_redemption_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=False
        ),
        sa.Column(
            "redemption_tier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("redemption_tiers.id"),
            nullable=False,
        ),
        sa.Column("cash_value", sa.Numeric(), nullable=True),
        sa.Column("ticket_value", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_promo_code_redemption_options_promo_code_id", "promo_code_redemption_options", ["promo_code_id"]
    )
    op.create_unique_constraint(
        "uq_promo_code_redemption_options_code_tier",
        "promo_code_redemption_options",
        ["promo_code_id", "redemption_tier_id"],
    )

    op.create_table(
        "reward_redemptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=False
        ),
        sa.Column(
            "redemption_tier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("redemption_tiers.id"),
            nullable=False,
        ),
        sa.Column(
            "choice", postgresql.ENUM("cash", "ticket", name="redemption_choice"), nullable=False
        ),
        sa.Column("points_spent", sa.Integer(), nullable=False),
        sa.Column("cash_value", sa.Numeric(), nullable=True),
        sa.Column("ticket_value", sa.Integer(), nullable=True),
        sa.Column("created_guest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guests.id"), nullable=True),
        sa.Column("payout_status", postgresql.ENUM("pending", "paid", name="payout_status"), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_reward_redemptions_promo_code_id", "reward_redemptions", ["promo_code_id"])


def downgrade() -> None:
    op.drop_index("ix_reward_redemptions_promo_code_id", table_name="reward_redemptions")
    op.drop_table("reward_redemptions")

    op.drop_constraint(
        "uq_promo_code_redemption_options_code_tier", "promo_code_redemption_options", type_="unique"
    )
    op.drop_index(
        "ix_promo_code_redemption_options_promo_code_id", table_name="promo_code_redemption_options"
    )
    op.drop_table("promo_code_redemption_options")

    op.drop_index("ix_redemption_tiers_event_id", table_name="redemption_tiers")
    op.drop_table("redemption_tiers")

    op.execute("DROP TYPE IF EXISTS payout_status")
    op.execute("DROP TYPE IF EXISTS redemption_choice")