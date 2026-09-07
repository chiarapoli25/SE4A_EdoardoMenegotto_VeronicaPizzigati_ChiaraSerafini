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
    # None richiede "simula l'intera serra": ogni settore produttivo attivo
    # con una ricetta assegnata, invece della singola recipe_id scelta a
    # mano — vedi POST /simulations in routes.py.
    recipe_id: str | None = Field(default=None, min_length=1, max_length=64)
    duration_seconds: int = Field(
        ge=STEP_SECONDS,
        le=MAX_DURATION_SECONDS,
        multiple_of=STEP_SECONDS,
    )


class SimulationJob(BaseModel):
    id: str
    # Esattamente uno fra recipe_id (singolo settore/ricetta isolata) e
    # zone_ids (intera serra) e' valorizzato — mai entrambi.
    recipe_id: str | None = None
    zone_ids: list[str] | None = None
    duration_seconds: int
    step_seconds: int = STEP_SECONDS
    # Somma degli step di OGNI target: in modalita' serra intera e' gia'
    # steps-per-settore moltiplicato per il numero di settori, cosi'
    # progress_percent riflette l'avanzamento complessivo del job, non solo
    # del singolo settore attualmente in esecuzione.
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
    # Valorizzato solo in modalita' serra intera, per abbinare ogni
    # elemento della lista risultato al settore reale che rappresenta (la
    # dashboard risolve nome/reparto localmente da questo id, vedi
    # zoneLabel() in script.js). None per la modalita' storica a settore
    # singolo, dove il risultato non e' mai legato a un settore reale.
    zone_id: str | None = None
    source_label: str = "Scenario simulato — non operativo"
    non_operational: bool = True
    recipe: dict[str, Any]
    duration_seconds: int
    step_seconds: int = STEP_SECONDS
    series: list[dict[str, Any]]
    actuator_intervals: list[dict[str, Any]]
    summary: dict[str, Any]
    phases: list[dict[str, Any]]
