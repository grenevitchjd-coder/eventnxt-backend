"""Unique constraint backstop on bonus_awards, preventing double-award at the DB level

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-27

The primary protection against awarding the same volume bonus twice is a
row lock on the promo code during the check-and-award step (see
app/services/bonuses.py) — this constraint is a defense-in-depth
backstop, matching the same "lock is primary, constraint is backstop"
discipline used elsewhere in this project. Additive, and safe to apply
even if bonus_awards already has rows (0013 is new enough that in
practice it won't).
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_bonus_awards_code_threshold", "bonus_awards", ["promo_code_id", "tickets_required"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_bonus_awards_code_threshold", "bonus_awards", type_="unique")