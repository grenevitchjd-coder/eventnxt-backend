# eventnxt-backend: app/models/pass_member.py
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class PassMember(Base):
    """
    One night inside a derived all-days pass. The pass ticket type
    (valid_date NULL, no pool of its own) points at each nightly type it
    covers; a pass purchase claims the same seat identity in every
    member's pool and mints one dated code per member. Explicit links —
    never a name match — so renames can't detach a live pass.
    """

    __tablename__ = "pass_members"
    __table_args__ = (UniqueConstraint("pass_type_id", "member_type_id", name="uq_pass_member"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pass_type_id = Column(UUID(as_uuid=True), ForeignKey("ticket_types.id", ondelete="CASCADE"), nullable=False, index=True)
    member_type_id = Column(UUID(as_uuid=True), ForeignKey("ticket_types.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)