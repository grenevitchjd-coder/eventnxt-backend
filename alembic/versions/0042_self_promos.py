"""eventnxt-backend: alembic/versions/0042_self_promos.py

Self promos — promo codes with no referrer behind them.

The Promote-section split (Promos page vs Referral Setup page) makes
"a marketing discount code the ORG creates for itself" (EARLYBIRD,
WEEKEND20) a first-class thing, distinct from a referral code that
belongs to a person. Two columns relax to allow it:

- guest_id nullable: NULL = self promo, owned by the event, nobody
  earns anything from it. Set = referral code, exactly as before.
- reward_type nullable: a self promo has no referrer to reward, so it
  has no reward terms at all. NULL is only ever valid together with
  guest_id NULL — enforced at the API layer (same place that already
  enforces which reward fields each reward_type needs), not as a DB
  constraint, matching how reward_value's conditionality is handled.

No data changes: every existing code has both columns set and keeps
meaning what it meant.

Revision ID: 0042
Revises: 0041
"""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("promo_codes", "guest_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column(
        "promo_codes",
        "reward_type",
        existing_type=sa.Enum("flat_amount", "percentage", "free_tickets", "points", name="reward_type"),
        nullable=True,
    )


def downgrade() -> None:
    # Self promos (NULL guest_id) can't survive a downgrade to NOT NULL —
    # they have no referrer to assign. Delete them, dependents first.
    op.execute(
        "DELETE FROM promo_code_points_rates WHERE promo_code_id IN "
        "(SELECT id FROM promo_codes WHERE guest_id IS NULL)"
    )
    op.execute(
        "DELETE FROM promo_code_bonus_tiers WHERE promo_code_id IN "
        "(SELECT id FROM promo_codes WHERE guest_id IS NULL)"
    )
    op.execute("DELETE FROM promo_codes WHERE guest_id IS NULL")
    op.alter_column(
        "promo_codes",
        "reward_type",
        existing_type=sa.Enum("flat_amount", "percentage", "free_tickets", "points", name="reward_type"),
        nullable=False,
    )
    op.alter_column("promo_codes", "guest_id", existing_type=sa.UUID(), nullable=False)