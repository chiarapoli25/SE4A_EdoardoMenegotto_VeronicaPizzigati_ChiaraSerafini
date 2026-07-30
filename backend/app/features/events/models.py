"""Contratti HTTP degli eventi Edge."""

from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class EdgeEventCreate(BaseModel):
    """Evento idempotente prodotto da un processo Edge."""

    event_id: str = Field(min_length=1, max_length=128)
    edge_id: str = Field(min_length=1, max_length=64)
    boot_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    timestamp_seconds: float = Field(ge=0.0)
    recorded_at: AwareDatetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EdgeEvent(EdgeEventCreate):
    """Evento persistito e associato a una zona."""

    zone_id: str
    received_at: AwareDatetime
