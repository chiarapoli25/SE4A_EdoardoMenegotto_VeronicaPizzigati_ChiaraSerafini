"""Contratti HTTP delle anteprime di simulazione in blocco."""

from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


STEP_SECONDS = 900
MAX_DURATION_SECONDS = 360 * 24 * 60 * 60


class SimulationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SimulationCreate(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=64)
    duration_seconds: int = Field(
        ge=STEP_SECONDS,
        le=MAX_DURATION_SECONDS,
        multiple_of=STEP_SECONDS,
    )


class SimulationJob(BaseModel):
    id: str
    recipe_id: str
    duration_seconds: int
    step_seconds: int = STEP_SECONDS
    total_steps: int = Field(ge=1)
    completed_steps: int = Field(ge=0)
    progress_percent: float = Field(ge=0.0, le=100.0)
    status: SimulationStatus
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    error: str | None = None


class SimulationPreview(BaseModel):
    job_id: str
    source_label: str = "Scenario simulato — non operativo"
    non_operational: bool = True
    recipe: dict[str, Any]
    duration_seconds: int
    step_seconds: int = STEP_SECONDS
    series: list[dict[str, Any]]
    actuator_intervals: list[dict[str, Any]]
    summary: dict[str, Any]
    phases: list[dict[str, Any]]
