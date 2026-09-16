from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import uuid


class State(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    code: str
    slug: str
    is_active: bool = True
    display_order: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StateCreate(BaseModel):
    name: str
    code: Optional[str] = None
    slug: Optional[str] = None
    is_active: bool = True
    display_order: int = 0


class StateUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None


class StateResponse(BaseModel):
    id: str
    name: str
    code: str
    slug: str
    is_active: bool
    display_order: int
    city_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime
