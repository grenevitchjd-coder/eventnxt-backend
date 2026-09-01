# eventnxt-backend: alembic/versions/0033_pass_members.py
"""Slice 3: derived all-days passes — explicit member links

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-31

A derived pass is a whole-event ticket type that owns NO seats of its
own — buying it claims the same seat identity in every linked night's
pool. The link is an explicit join table (not a name match) so renaming
a nightly type can never silently detach a live pass. Deleting a pass
cascades its memberships away; deleting a MEMBER while a pass points at
it is refused app-side.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pass_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pass_type_id", UUID(as_uuid=True), sa.ForeignKey("ticket_types.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("member_type_id", UUID(as_uuid=True), sa.ForeignKey("ticket_types.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("pass_type_id", "member_type_id", name="uq_pass_member"),
    )


def downgrade() -> None:
    op.drop_table("pass_members")