# eventnxt-backend: app/schemas/guest.py
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas.seating_category import AdminSeatResponse


class TicketAllotmentDayItem(BaseModel):
    date: str
    quantity: int = Field(ge=0)


class GuestCreateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    section_label: Optional[str] = None  # section within the pool; needs seating_category_id
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    visit_date: Optional[str] = None
    hold_timing: Optional[Literal["now", "on_confirm", "later"]] = None  # None = guest type default, else "now"
    spend_total: Optional[int] = Field(default=None, ge=1)
    cohort_together: bool = True
    perks: Optional[str] = None
    comments: Optional[str] = None
    ticket_allotment: Optional[List[TicketAllotmentDayItem]] = None
    guest_mode: Optional[Literal["invite", "distribute", "select"]] = None


class GuestUpdateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    section_label: Optional[str] = None
    recipient_seating_category_id: Optional[uuid.UUID] = None
    recipient_section_label: Optional[str] = None
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    visit_date: Optional[str] = None
    hold_timing: Optional[Literal["now", "on_confirm", "later"]] = None  # None = guest type default, else "now"
    spend_total: Optional[int] = Field(default=None, ge=1)
    cohort_together: bool = True
    perks: Optional[str] = None
    comments: Optional[str] = None
    ticket_allotment: Optional[List[TicketAllotmentDayItem]] = None
    guest_mode: Optional[Literal["invite", "distribute", "select"]] = None


class GuestResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    email: str
    guest_type_id: Optional[uuid.UUID] = None  # None = referrer-only guest (0043)
    is_referrer_only: bool = False
    seating_category_id: Optional[uuid.UUID] = None
    section_label: Optional[str] = None
    recipient_seating_category_id: Optional[uuid.UUID] = None
    recipient_section_label: Optional[str] = None
    allocation_status: str
    party_size: int
    perks: Optional[str] = None
    comments: Optional[str] = None
    ticket_allotment_overridden: bool
    ticket_allotment: List[TicketAllotmentDayItem] = []
    allotment_total: int = 0
    allotment_distributed: int = 0
    visit_date: Optional[str] = None
    hold_timing: Literal["now", "on_confirm", "later"] = "now"
    cohort_together: bool = True
    allocated_by_guest_id: Optional[uuid.UUID] = None
    rsvp_token: str
    rsvp_confirmed: Optional[str] = None
    guest_mode: Optional[str] = None
    effective_mode: str = "invite"
    needs_seating: bool = False
    ticket_count: int = 0
    seat_labels: List[str] = []  # assigned seats, display order
    link_sent_at: Optional[datetime] = None
    tickets_sent_at: Optional[datetime] = None
    spend_total: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class GuestSeatsAssignRequest(BaseModel):
    """Wholesale replace of the guest's assigned seats (empty = unassign
    all; released seats stay reserved)."""

    seat_ids: List[uuid.UUID] = []


class GuestSeatDayResponse(BaseModel):
    """One night's seat map for a guest whose ticket spans several
    nights — each night is a SEPARATE pool clone with its own Seat
    rows, so picking or moving a seat for one specific night needs its
    own map, not just the guest's 'home' pool. date=None for a single-
    day/whole-event guest with nothing to pick between."""

    date: Optional[str] = None
    category_id: uuid.UUID
    category_name: str
    unit_label: Optional[str] = None  # the pool's own word for "Seat" (e.g. "Table") — None keeps the default
    seats: List[AdminSeatResponse] = []


class GuestSentStatusRequest(BaseModel):
    # Which manual marker to flip: the RSVP-link one (default, the
    # original behavior) or the external-ticketing tickets-sent one.
    marker: Literal["link", "tickets"] = "link"
    sent: bool