import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class SeatingCategoryCreateRequest(BaseModel):
    name: str
    capacity: int

    @field_validator("capacity")
    @classmethod
    def check_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("capacity must be at least 1.")
        return v


class SeatingCategoryUpdateRequest(BaseModel):
    name: str
    capacity: int

    @field_validator("capacity")
    @classmethod
    def check_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("capacity must be at least 1.")
        return v


class SeatingCategoryResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    capacity: int
    created_at: datetime

    class Config:
        from_attributes = True


class SeatingSummaryRow(BaseModel):
    """
    One category's live reconciliation across capacity, guest list, and
    box office sales — capacity, box_office, and allotted/committed are
    each their own real number, not derived from each other, so the two
    availability figures can genuinely disagree (that's the point):
    confirmed_avail is capacity minus what's actually locked in (mirrors
    what the real seating-capacity check enforces); estimated_avail is
    the more conservative number, also subtracting tentative holds and
    box-office sales, giving a "worst case" available count.
    """

    category_id: uuid.UUID
    category_name: str
    capacity: int
    box_office: int
    allotted: int
    committed: int
    confirmed_avail: int
    estimated_avail: int