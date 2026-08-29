"""Public page personalization: font, logo placement, banner focus, About Us

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29

Additive only — four new nullable columns on event_profiles, nothing
existing touched or narrowed.

Null is the deliberate default meaning for every one of these: "the
original look, exactly." No backfill needed or wanted — a profile that
never touches these settings renders identically to how it did before
this migration existed:

- font_family    null = Fraunces, the page's original display font
- logo_position  null = centered in-flow above the title (original behavior)
- banner_focus   null = center crop of the banner (original behavior)
- about_us       null = no About Us section rendered
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_profiles", sa.Column("font_family", sa.String(), nullable=True))
    op.add_column("event_profiles", sa.Column("logo_position", sa.String(), nullable=True))
    op.add_column("event_profiles", sa.Column("banner_focus", sa.String(), nullable=True))
    op.add_column("event_profiles", sa.Column("about_us", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("event_profiles", "about_us")
    op.drop_column("event_profiles", "banner_focus")
    op.drop_column("event_profiles", "logo_position")
    op.drop_column("event_profiles", "font_family")