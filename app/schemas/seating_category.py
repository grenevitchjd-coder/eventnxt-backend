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