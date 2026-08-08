"""Contratti HTTP dei cicli colturali orientati all'agronomo."""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field

from ..commands.models import RuntimeCommand


class CultivationState(str, Enum):
    ACTIVATING = "activating"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    STOPPING = "stopping"
    ERROR = "error"
    ARCHIVED = "archived"


class CultivationCreate(BaseModel):
    """Scelta minima per creare e avviare un ciclo in un solo passaggio."""

    zone_id: str = Field(min_length=1, max_length=64)
    recipe_id: str = Field(min_length=1, max_length=64)


class Cultivation(BaseModel):
    id: str
    zone_id: str
    plant_species: str
    recipe_id: str
    recipe_version: int = Field(ge=1)
    state: CultivationState
    recipe_completed: bool = False
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    archived_at: AwareDatetime | None = None
    last_command_id: str | None = None


class CultivationAction(BaseModel):
    cultivation: Cultivation
    command: RuntimeCommand
