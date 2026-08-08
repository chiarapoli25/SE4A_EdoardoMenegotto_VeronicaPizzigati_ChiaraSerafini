"""@file models.py
@brief Modelli Pydantic dei reparti e dei settori della serra.
"""

from enum import Enum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from ...greenhouse_layout import department_name


class ZoneStatus(str, Enum):
    """@brief Stato del collegamento fra un settore e il relativo Edge."""

    ## @brief Nessun aggiornamento recente e disponibile.
    OFFLINE = "offline"
    ## @brief Il settore comunica regolarmente con il backend.
    ONLINE = "online"


class ZoneLifecycleState(str, Enum):
    """Stato applicativo corrente pubblicato dall'Edge."""

    IDLE = "Idle"
    RUNNING = "Running"
    PAUSED = "Paused"
    ERROR = "Error"


class OperationalState(str, Enum):
    """Stato corrente della FSM di sicurezza dell'Edge."""

    NOMINAL = "Nominal"
    DEGRADED = "Degraded"
    EMERGENCY_LOCKDOWN = "EmergencyLockdown"


class StrategyName(str, Enum):
    """Nomi stabili delle Strategy disponibili sull'Edge."""

    THRESHOLD = "Threshold"
    PID = "PID"
    PREDICTIVE = "Predictive"


class ControlStrategies(BaseModel):
    """Strategia selezionata per ognuna delle sei variabili controllate."""

    model_config = ConfigDict(extra="forbid")

    soil_moisture: StrategyName
    light: StrategyName
    ph: StrategyName
    nitrogen: StrategyName
    phosphorus: StrategyName
    potassium: StrategyName

    @classmethod
    def defaults(cls) -> "ControlStrategies":
        """Configurazione iniziale prima del primo snapshot Edge."""
        return cls(
            soil_moisture=StrategyName.THRESHOLD,
            light=StrategyName.THRESHOLD,
            ph=StrategyName.PID,
            nitrogen=StrategyName.PREDICTIVE,
            phosphorus=StrategyName.PREDICTIVE,
            potassium=StrategyName.PREDICTIVE,
        )


class ControlSetpoints(BaseModel):
    """Setpoint correnti della fase, indicizzati per variabile."""

    model_config = ConfigDict(extra="forbid")

    soil_moisture: float = Field(allow_inf_nan=False)
    light: float = Field(allow_inf_nan=False)
    ph: float = Field(allow_inf_nan=False)
    nitrogen: float = Field(allow_inf_nan=False)
    phosphorus: float = Field(allow_inf_nan=False)
    potassium: float = Field(allow_inf_nan=False)

    @classmethod
    def zeros(cls) -> "ControlSetpoints":
        """Valori neutri usati finche l'Edge non invia la prima fase."""
        return cls(
            soil_moisture=0.0,
            light=0.0,
            ph=0.0,
            nitrogen=0.0,
            phosphorus=0.0,
            potassium=0.0,
        )


class ZoneAdministrativeStatus(str, Enum):
    """@brief Disponibilita amministrativa di un settore."""

    ## @brief Settore disponibile per il normale utilizzo.
    ACTIVE = "active"
    ## @brief Settore disabilitato dall'amministratore.
    INACTIVE = "inactive"
    ## @brief Settore temporaneamente riservato alla manutenzione.
    MAINTENANCE = "maintenance"


class ZoneCreate(BaseModel):
    """@brief Dati necessari per registrare un settore della serra.

    @details La serra possiede quattro reparti produttivi, numerati da 1 a 4,
    e il reparto 5 destinato alle piante in quarantena. Ogni reparto contiene
    al massimo due settori, numerati da 1 a 2. I settori produttivi ospitano
    una sola specie; quelli del quinto reparto sono misti e non dichiarano
    `plant_species`. Lo stato di quarantena appartiene alla singola pianta.
    """

    ## @brief Identificativo univoco usato negli endpoint HTTP.
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Nome leggibile mostrato nella dashboard.
    name: str = Field(min_length=1, max_length=100)
    ## @brief Numero del reparto fisico: 1-4 produttivi, 5 quarantena.
    department_number: int = Field(ge=1, le=5)
    ## @brief Numero del settore nel reparto, compreso fra 1 e 2.
    sector_number: int = Field(ge=1, le=2)
    ## @brief Specie unica del settore produttivo; assente in quarantena.
    plant_species: str | None = Field(default=None, min_length=1, max_length=100)
    ## @brief Edge incaricato di gestire fisicamente il settore.
    assigned_edge_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Ricetta assegnata al settore, se presente.
    active_recipe_id: str | None = Field(default=None, max_length=64)
    ## @brief Nome della fase di coltivazione corrente, se presente.
    current_phase: str | None = Field(default=None, max_length=100)
    ## @brief True quando la ricetta ha terminato anche la propria ultima fase.
    cultivation_completed: bool = False
    ## @brief Disponibilita configurata dall'amministratore.
    administrative_status: ZoneAdministrativeStatus = (
        ZoneAdministrativeStatus.ACTIVE
    )

    @computed_field
    @property
    def department_name(self) -> str:
        """Nome canonico del reparto fisico mostrato ai client."""
        return department_name(self.department_number)

    @model_validator(mode="after")
    def _validate_department_role(self) -> "ZoneCreate":
        """Mantiene coerenti reparto, funzione e specie vegetale."""
        if self.department_number == 5:
            if self.plant_species is not None:
                raise ValueError(
                    "a zone in department 5 cannot have one plant_species"
                )
        else:
            if self.plant_species is None:
                raise ValueError(
                    "a cultivation zone requires plant_species"
                )
        return self


class Zone(ZoneCreate):
    """@brief Stato persistente di un settore della serra."""

    ## @brief Stato corrente del collegamento Edge.
    status: ZoneStatus = ZoneStatus.OFFLINE
    ## @brief Timestamp UTC dell'ultimo contatto Edge, oppure `None`.
    last_edge_contact: AwareDatetime | None = None
    ## @brief Lifecycle applicativo corrente, persistito senza rileggere eventi.
    lifecycle_state: ZoneLifecycleState = ZoneLifecycleState.IDLE
    ## @brief Stato corrente della FSM di sicurezza.
    operational_state: OperationalState = OperationalState.NOMINAL
    ## @brief Versione della ricetta attiva osservata dall'Edge.
    active_recipe_version: int | None = Field(default=None, ge=1)
    ## @brief Strategy correnti per tutte le variabili controllate.
    current_strategies: ControlStrategies = Field(
        default_factory=ControlStrategies.defaults
    )
    ## @brief Setpoint correnti della fase attiva.
    current_setpoints: ControlSetpoints = Field(
        default_factory=ControlSetpoints.zeros
    )
    ## @brief Rapporto corrente fra tempo simulato e reale.
    time_scale: float = Field(default=1.0, ge=1.0, le=60.0)
    ## @brief Ciclo colturale non archiviato attualmente assegnato.
    active_cultivation_id: str | None = None


class ZoneUpdate(BaseModel):
    """@brief Modifiche parziali ammesse per un settore esistente.

    @details Posizione fisica e identificativo non sono modificabili. I campi
    nullable distinguono il valore JSON `null` da un campo omesso tramite
    `model_fields_set`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    plant_species: str | None = Field(default=None, min_length=1, max_length=100)
    assigned_edge_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    active_recipe_id: str | None = Field(default=None, max_length=64)
    administrative_status: ZoneAdministrativeStatus | None = None

    @model_validator(mode="after")
    def _reject_null_required_fields(self) -> "ZoneUpdate":
        """Impedisce di azzerare campi che devono sempre avere un valore."""
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if (
            "administrative_status" in self.model_fields_set
            and self.administrative_status is None
        ):
            raise ValueError("administrative_status cannot be null")
        return self
