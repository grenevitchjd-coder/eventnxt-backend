"""eventnxt-backend: alembic/versions/0055_seating_category_preset.py

Fixes a real placement bug found in test_section_priorities: a PENDING
guest with no explicit seating category gets one auto-filled at creation
as a display-only placeholder ("which category would they land in
first" — see create_guest's pending branch, no capacity check, no
section). RSVP-yes then saw that non-null seating_category_id and
mistook it for a deliberate organizer preset (the "Row 3 Preferred
Seating dropdown" case documented in respond_to_rsvp) — so it let the
placeholder STICK instead of re-running the type's priority-and-section
walk, confirming the guest at pool level with no section ever actually
claimed. The section's capacity was therefore never touched by that
guest, which is why a later guest explicitly requesting the "full"
section was wrongly let in too.

guests.seating_category_preset is TRUE only when a human explicitly
chose the category — create_guest's `payload.seating_category_id`
branch, or update_guest (which always takes an explicit value, per its
own docstring) — and FALSE for the pending-creation placeholder. RSVP
resolution now only lets a pre-existing seating_category_id stick when
this flag is true; otherwise it falls through to the already-computed,
capacity-checked priority walk.

Revision ID: 0055
Revises: 0054
"""
import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guests",
        sa.Column("seating_category_preset", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("guests", "seating_category_preset")