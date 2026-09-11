"""Contratti HTTP delle anteprime di simulazione in blocco e della
riproduzione live (stessa fisica, esposta a un ritmo scelto invece che
tutta insieme — vedi live_manager.py)."""

from enum import Enum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field


STEP_SECONDS = 900
MAX_DURATION_SECONDS = 360 * 24 * 60 * 60

# Velocita' di riproduzione live: secondi simulati che passano per ogni
# secondo reale. Default 600.0 = "un secondo reale sono 10 minuti
# simulati", la richiesta originale del tasto play. Il tetto (36000.0 = 10
# ore simulate al secondo) evita che una velocita' assurda faccia avanzare
# l'intera durata in meno di un tick di polling del dashboard, rendendo la
# riproduzione "live" solo sulla carta.
DEFAULT_LIVE_SPEED_MULTIPLIER = 600.0
MAX_LIVE_SPEED_MULTIPLIER = 36000.0


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


# --- Riproduzione live --------------------------------------------------


class LiveSimulationStatus(str, Enum):
    COMPUTING = "computing"  # Edge in esecuzione: nulla ancora da mostrare
    PLAYING = "playing"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LiveSimulationCreate(BaseModel):
    # Sempre "l'intera serra" (ogni settore produttivo attivo con una
    # ricetta assegnata) — non esiste una modalita' a singola ricetta per
    # la riproduzione live, a differenza del percorso storico batch. Niente
    # duration_seconds: l'orizzonte calcolato cresce da solo man mano che la
    # riproduzione lo raggiunge (vedi LiveSimulationManager._maybe_extend in
    # live_manager.py) — non e' piu' una scelta dell'utente quanto deve
    # durare, e la riproduzione non si ferma mai da sola.
    speed_multiplier: float = Field(
        default=DEFAULT_LIVE_SPEED_MULTIPLIER,
        gt=0.0,
        le=MAX_LIVE_SPEED_MULTIPLIER,
    )


class LiveSimulationControl(BaseModel):
    action: Literal["play", "pause"]
    speed_multiplier: float | None = Field(
        default=None, gt=0.0, le=MAX_LIVE_SPEED_MULTIPLIER
    )


class LiveZoneSnapshot(BaseModel):
    """Ultimo step rivelato per un singolo settore — quanto serve alla
    griglia del Simulatore per mostrare la serra "in funzione" senza dover
    rileggere l'intera serie ridotta (vedi LiveSimulationJob.zones)."""

    zone_id: str
    recipe: dict[str, Any]
    phase_name: str | None
    sensors: dict[str, Any]
    models: dict[str, Any]
    active_actuators: dict[str, bool]
    timestamp_seconds: float


class LiveSimulationJob(BaseModel):
    id: str
    zone_ids: list[str]
    # Fin dove e' stato calcolato l'Edge finora — CRESCE da solo man mano
    # che elapsed_seconds gli si avvicina (raddoppia, vedi
    # LiveSimulationManager._maybe_extend in live_manager.py), non e' un
    # tetto ne' una scelta dell'utente: la riproduzione non finisce mai da
    # sola, vedi LiveSimulationCreate. elapsed_seconds stesso non e' mai
    # avvolto/azzerato — puo' superare orizzonte solo per una finestra
    # breve (il tempo di un'estensione in background).
    horizon_seconds: int
    step_seconds: int = STEP_SECONDS
    status: LiveSimulationStatus
    speed_multiplier: float
    elapsed_seconds: float = 0.0
    # Avanzamento del calcolo Edge (fase "computing", prima che la
    # riproduzione possa cominciare) — stesso significato di
    # SimulationJob.progress_percent.
    computing_progress_percent: float = 0.0
    zones: list[LiveZoneSnapshot] = Field(default_factory=list)
    created_at: AwareDatetime
    started_playing_at: AwareDatetime | None = None
    # Quando lo status e' passato a FAILED/CANCELLED — mai valorizzato per
    # PLAYING/PAUSED/COMPUTING, visto che la riproduzione non finisce mai
    # da sola (vedi LiveSimulationCreate).
    stopped_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    error: str | None = None
