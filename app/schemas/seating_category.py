# eventnxt-backend: app/schemas/seating_category.py
import uuid
from datetime import datetime

from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


ZONE_KINDS = ("ga", "row", "table", "seat")


class _ZoneFieldsMixin(BaseModel):
    name: str
    capacity: int = 1  # ignored (derived) for table zones
    sales_grain: str = "ga"
    row_label: Optional[str] = None
    section_label: Optional[str] = None
    table_count: Optional[int] = None
    seats_per_table: Optional[int] = None

    @field_validator("capacity")
    @classmethod
    def check_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("capacity must be at least 1.")
        return v

    @field_validator("sales_grain")
    @classmethod
    def check_grain(cls, v: str) -> str:
        if v not in ZONE_KINDS:
            raise ValueError(f"sales_grain must be one of: {', '.join(ZONE_KINDS)}.")
        return v

    @model_validator(mode="after")
    def check_table_math(self):
        if self.sales_grain == "table":
            if not self.table_count or self.table_count < 1 or not self.seats_per_table or self.seats_per_table < 1:
                raise ValueError("Table zones need table_count and seats_per_table (both at least 1).")
            # capacity is derived — one true number for all machinery
            self.capacity = self.table_count * self.seats_per_table
        else:
            self.table_count = None
            self.seats_per_table = None
        return self


class SeatingCategoryCreateRequest(_ZoneFieldsMixin):
    pass


class SeatingCategoryUpdateRequest(_ZoneFieldsMixin):
    pass


class SeatingCategoryResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    capacity: int
    sales_grain: str = "ga"
    row_label: Optional[str] = None
    section_label: Optional[str] = None
    table_count: Optional[int] = None
    seats_per_table: Optional[int] = None
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