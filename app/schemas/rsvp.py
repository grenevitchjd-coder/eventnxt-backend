from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class DayAllotment(BaseModel):
    """One day's pool for an allotment holder — its own separate total,
    distributed count, and remaining count. Never mixed with any other
    day's numbers."""

    date: str
    total: int
    distributed: int
    remaining: int


class RSVPInfoResponse(BaseModel):
    """
    What a guest sees when they open their own RSVP link. Two shapes in
    one response, distinguished by is_allotment_holder: a plain guest (or
    a delegated recipient) just confirms/declines for themselves; an
    allotment holder (model, sponsor) sees their per-day ticket pools and
    distributes them to others instead.
    """

    guest_name: str
    guest_type_name: str
    allocation_status: str
    visit_date: Optional[str] = None
    party_size: int

    is_allotment_holder: bool
    day_allotments: Optional[List[DayAllotment]] = None
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
    visit_date: str  # required — capacity is tracked per specific day
    party_size: int = Field(default=1, ge=1)


class RSVPDistributeRequest(BaseModel):
    recipients: List[RSVPDistributeRecipient]


RSVPInfoResponse.model_rebuild()