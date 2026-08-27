from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class RSVPInfoResponse(BaseModel):
    """
    What a guest sees when they open their own RSVP link. Two shapes in
    one response, distinguished by is_allotment_holder: a plain guest (or
    a delegated recipient) just confirms/declines for themselves; an
    allotment holder (model, sponsor) sees their remaining tickets and
    distributes them to others instead.
    """

    guest_name: str
    guest_type_name: str
    allocation_status: str
    visit_date: Optional[str] = None
    party_size: int

    is_allotment_holder: bool
    ticket_count: Optional[int] = None
    valid_dates: Optional[List[str]] = None
    tickets_distributed: Optional[int] = None
    tickets_remaining: Optional[int] = None
    distributed_recipients: Optional[List["DistributedRecipient"]] = None


class DistributedRecipient(BaseModel):
    name: str
    email: str
    visit_date: Optional[str] = None
    party_size: int
    allocation_status: str


class RSVPRespondRequest(BaseModel):
    attending: bool


class RSVPDistributeRecipient(BaseModel):
    name: str
    email: EmailStr
    visit_date: Optional[str] = None
    party_size: int = Field(default=1, ge=1)


class RSVPDistributeRequest(BaseModel):
    recipients: List[RSVPDistributeRecipient]


RSVPInfoResponse.model_rebuild()