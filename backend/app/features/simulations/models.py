"""@file
@brief Contratti HTTP delle simulazioni batch e live.

@details Le due modalita usano la stessa fisica Edge. La simulazione batch
restituisce l'anteprima completa; quella live rivela gli stessi dati al ritmo
scelto dall'utente.
"""

from enum import Enum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field


## @brief Durata di un singolo step Edge nelle simulazioni.
STEP_SECONDS = 900
## @brief Massimo orizzonte accettato da un job batch.
MAX_DURATION_SECONDS = 360 * 24 * 60 * 60

# Velocita' di riproduzione live: secondi simulati che passano per ogni
# secondo reale. Default 600.0 = "un secondo reale sono 10 minuti
# simulati", la richiesta originale del tasto play. Il tetto (36000.0 = 10
# ore simulate al secondo) evita che una velocita' assurda faccia avanzare
# l'intera durata in meno di un tick di polling del dashboard, rendendo la
# riproduzione "live" solo sulla carta.
## @brief Velocita iniziale della riproduzione live.
DEFAULT_LIVE_SPEED_MULTIPLIER = 600.0
## @brief Limite superiore imposto alla velocita live.
MAX_LIVE_SPEED_MULTIPLIER = 36000.0


class SimulationStatus(str, Enum):
    """@brief Stato di avanzamento di un job batch."""

    QUEUED = "queued"  ##< In coda sull'unico worker batch.
    RUNNING = "running"  ##< Edge in esecuzione.
    SUCCEEDED = "succeeded"  ##< Anteprima completa disponibile.
    FAILED = "failed"  ##< Esecuzione terminata con errore.
    CANCELLED = "cancelled"  ##< Esecuzione annullata dal client.


class SimulationCreate(BaseModel):
    """@brief Parametri per avviare un'anteprima batch."""

    ## @brief Ricetta isolata; `None` richiede l'intera serra produttiva.
    recipe_id: str | None = Field(default=None, min_length=1, max_length=64)
    ## @brief Orizzonte simulato, multiplo dello step Edge.
    duration_seconds: int = Field(
        ge=STEP_SECONDS,
        le=MAX_DURATION_SECONDS,
        multiple_of=STEP_SECONDS,
    )


class SimulationJob(BaseModel):
    """@brief Stato osservabile di un job di simulazione batch."""

    ## @brief Identificatore del job.
    id: str
    ## @brief Ricetta della modalita isolata; mutuamente esclusiva con `zone_ids`.
    recipe_id: str | None = None
    ## @brief Zone della modalita serra; mutuamente esclusive con `recipe_id`.
    zone_ids: list[str] | None = None
    ## @brief Durata richiesta per ciascun target.
    duration_seconds: int
    ## @brief Ampiezza temporale di un campione Edge.
    step_seconds: int = STEP_SECONDS
    ## @brief Somma degli step di tutti i target del job.
    total_steps: int = Field(ge=1)
    ## @brief Numero complessivo di step gia elaborati.
    completed_steps: int = Field(ge=0)
    ## @brief Avanzamento normalizzato tra 0 e 100.
    progress_percent: float = Field(ge=0.0, le=100.0)
    ## @brief Stato corrente del worker.
    status: SimulationStatus
    ## @brief Istante di creazione.
    created_at: AwareDatetime
    ## @brief Istante di avvio del processo Edge.
    started_at: AwareDatetime | None = None
    ## @brief Istante di completamento o errore.
    completed_at: AwareDatetime | None = None
    ## @brief Scadenza dopo la quale il risultato puo essere eliminato.
    expires_at: AwareDatetime | None = None
    ## @brief Diagnostica limitata esposta in caso di errore.
    error: str | None = None


class SimulationPreview(BaseModel):
    """@brief Serie, intervalli e riepilogo prodotti da un target simulato."""

    ## @brief Job dal quale deriva l'anteprima.
    job_id: str
    ## @brief Zona reale rappresentata; `None` nella modalita a ricetta isolata.
    zone_id: str | None = None
    ## @brief Etichetta che distingue esplicitamente l'anteprima dai dati operativi.
    source_label: str = "Scenario simulato — non operativo"
    ## @brief Guardia machine-readable contro l'uso come telemetria reale.
    non_operational: bool = True
    ## @brief Identita e metadati sintetici della ricetta.
    recipe: dict[str, Any]
    ## @brief Durata simulata del target.
    duration_seconds: int
    ## @brief Ampiezza temporale di un campione.
    step_seconds: int = STEP_SECONDS
    ## @brief Serie temporale ridotta per i grafici.
    series: list[dict[str, Any]]
    ## @brief Intervalli di attivazione ricostruiti dagli step.
    actuator_intervals: list[dict[str, Any]]
    ## @brief Aggregati finali della simulazione.
    summary: dict[str, Any]
    ## @brief Fasi e target necessari a disegnare bande e setpoint.
    phases: list[dict[str, Any]]


# --- Riproduzione live --------------------------------------------------


class LiveSimulationStatus(str, Enum):
    """@brief Stato della preparazione o riproduzione live."""

    COMPUTING = "computing"  ##< Edge in esecuzione; dati non ancora rivelabili.
    PLAYING = "playing"  ##< Tempo simulato in avanzamento.
    PAUSED = "paused"  ##< Posizione temporale congelata.
    FAILED = "failed"  ##< Calcolo iniziale non riuscito.
    CANCELLED = "cancelled"  ##< Riproduzione arrestata dal client.


class LiveSimulationCreate(BaseModel):
    """@brief Parametri per riprodurre dal vivo l'intera serra produttiva.

    @details Non possiede una durata finale: l'orizzonte calcolato viene esteso
    automaticamente mentre il tempo simulato avanza.
    """

    ## @brief Secondi simulati trascorsi per ogni secondo reale.
    speed_multiplier: float = Field(
        default=DEFAULT_LIVE_SPEED_MULTIPLIER,
        gt=0.0,
        le=MAX_LIVE_SPEED_MULTIPLIER,
    )


class LiveSimulationControl(BaseModel):
    """@brief Comando di play, pausa o cambio velocita della riproduzione."""

    ## @brief Azione temporale richiesta.
    action: Literal["play", "pause"]
    ## @brief Nuova velocita; `None` conserva quella corrente.
    speed_multiplier: float | None = Field(
        default=None, gt=0.0, le=MAX_LIVE_SPEED_MULTIPLIER
    )


class LiveSimulationQuarantineUpdate(BaseModel):
    """@brief Sposta o richiama virtualmente una pianta in quarantena.

    @details Vale SOLO per la riproduzione live indicata nell'URL — non
    tocca mai `plants.is_quarantined` reale ne' `plant_movements` (vedi
    LiveSimulationManager.set_simulated_quarantine). Il numero di piante
    non influenza la fisica calcolata (nessun parametro dell'Edge ne
    dipende): lo spostamento cambia solo il conteggio e l'elenco esposti
    da LiveZoneSnapshot.quarantined_plant_ids per il settore, mai la sua
    serie simulata, che resta quella dell'intero settore."""

    ## @brief Settore della riproduzione a cui la pianta appartiene ora.
    zone_id: str = Field(min_length=1, max_length=64)
    ## @brief Pianta reale (anagrafica) da spostare o richiamare.
    plant_id: str = Field(min_length=1, max_length=64)
    ## @brief `True` per spostarla in quarantena simulata, `False` per richiamarla.
    quarantined: bool


class LiveZoneSnapshot(BaseModel):
    """@brief Ultimo step rivelato per un singolo settore.

    @details Contiene quanto serve alla
    griglia del Simulatore per mostrare la serra "in funzione" senza dover
    rileggere l'intera serie ridotta (vedi LiveSimulationJob.zones)."""

    ## @brief Settore rappresentato.
    zone_id: str
    ## @brief Identita e metadati sintetici della ricetta.
    recipe: dict[str, Any]
    ## @brief Fase attiva nell'ultimo step rivelato.
    phase_name: str | None
    ## @brief Letture simulate dell'ultimo step.
    sensors: dict[str, Any]
    ## @brief Stime simulate dell'ultimo step.
    models: dict[str, Any]
    ## @brief Attuatori intervenuti durante lo step.
    active_actuators: dict[str, bool]
    ## @brief Posizione temporale dello step nella simulazione.
    timestamp_seconds: float
    ## @brief Piante di questo settore virtualmente in quarantena SOLO per
    ## questa riproduzione — non tocca mai la quarantena reale, vedi
    ## LiveSimulationQuarantineUpdate. Non influenza la serie simulata
    ## (nessun parametro fisico dipende dal conteggio piante): serve solo
    ## a mostrare correttamente "quante piante" restano attive in questo
    ## settore dentro la riproduzione.
    quarantined_plant_ids: list[str] = Field(default_factory=list)


class LiveSimulationJob(BaseModel):
    """@brief Stato osservabile e snapshot correnti della riproduzione live."""

    ## @brief Identificatore del job live.
    id: str
    ## @brief Settori produttivi inclusi nella riproduzione.
    zone_ids: list[str]
    ## @brief Orizzonte gia calcolato dall'Edge, esteso automaticamente.
    horizon_seconds: int
    ## @brief Ampiezza temporale di un campione.
    step_seconds: int = STEP_SECONDS
    ## @brief Stato corrente della riproduzione.
    status: LiveSimulationStatus
    ## @brief Secondi simulati trascorsi per ogni secondo reale.
    speed_multiplier: float
    ## @brief Posizione temporale simulata, mai riavvolta automaticamente.
    elapsed_seconds: float = 0.0
    ## @brief Avanzamento del primo calcolo Edge tra 0 e 100.
    computing_progress_percent: float = 0.0
    ## @brief Ultimo snapshot rivelato per ciascuna zona.
    zones: list[LiveZoneSnapshot] = Field(default_factory=list)
    ## @brief Istante di creazione.
    created_at: AwareDatetime
    ## @brief Primo istante di passaggio allo stato playing.
    started_playing_at: AwareDatetime | None = None
    ## @brief Istante di errore o annullamento; assente per gli stati attivi.
    stopped_at: AwareDatetime | None = None
    ## @brief Scadenza del record concluso.
    expires_at: AwareDatetime | None = None
    ## @brief Diagnostica limitata esposta in caso di errore.
    error: str | None = None
