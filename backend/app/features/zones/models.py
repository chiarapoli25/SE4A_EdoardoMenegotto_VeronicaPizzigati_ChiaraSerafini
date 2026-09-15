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
    """@brief Stato applicativo corrente pubblicato dall'Edge."""

    IDLE = "Idle"  ##< Zona configurata ma senza controllo attivo.
    RUNNING = "Running"  ##< Ciclo di controllo in esecuzione.
    PAUSED = "Paused"  ##< Ciclo temporaneamente sospeso.
    ERROR = "Error"  ##< Runtime non in grado di continuare normalmente.


class OperationalState(str, Enum):
    """@brief Stato corrente della FSM di sicurezza dell'Edge."""

    NOMINAL = "Nominal"  ##< Tutti i controlli possono operare.
    DEGRADED = "Degraded"  ##< Controlli dipendenti dal guasto isolati.
    EMERGENCY_LOCKDOWN = "EmergencyLockdown"  ##< Tutte le uscite in stato sicuro.


class StrategyName(str, Enum):
    """@brief Nomi stabili delle Strategy disponibili sull'Edge."""

    THRESHOLD = "Threshold"  ##< Controllo a soglia con isteresi.
    PID = "PID"  ##< Controllo proporzionale, integrale e derivativo.
    PREDICTIVE = "Predictive"  ##< Controllo anticipativo basato sul modello.


class ControlStrategies(BaseModel):
    """@brief Strategy selezionata per ognuna delle sei variabili controllate."""

    ## @brief Rifiuta variabili di controllo non previste dal contratto Edge.
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
    """@brief Setpoint correnti della fase, indicizzati per variabile."""

    ## @brief Rifiuta variabili non previste dal contratto Edge.
    model_config = ConfigDict(extra="forbid")

    ## @brief Umidita target del terriccio in percentuale.
    soil_moisture: float = Field(allow_inf_nan=False)
    ## @brief Luce target secondo il contratto della ricetta.
    light: float = Field(allow_inf_nan=False)
    ## @brief pH target dell'acqua interstiziale.
    ph: float = Field(allow_inf_nan=False)
    ## @brief Concentrazione target di azoto.
    nitrogen: float = Field(allow_inf_nan=False)
    ## @brief Concentrazione target di fosforo.
    phosphorus: float = Field(allow_inf_nan=False)
    ## @brief Concentrazione target di potassio.
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
    e il reparto 5 destinato alle piante in quarantena. Ogni reparto
    produttivo contiene al massimo due settori, numerati da 1 a 2; il
    reparto 5 ha invece un solo settore, sempre numerato 1 (vincolo imposto
    esplicitamente dalla route di creazione, non solo da convenzione). I
    settori produttivi ospitano una sola specie; quello del quinto reparto
    e' misto e non dichiara `plant_species`. Lo stato di quarantena
    appartiene alla singola pianta.
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
    ## @brief fault_id dell'ultimo InjectFault riuscito su questa zona non
    ## ancora seguito da un ResetFault riuscito, oppure `None`. Popolato/
    ## azzerato in backend/app/features/commands/repository.py::
    ## complete_command() — mai scritto altrove. Esiste per permettere a un
    ## client (dashboard) di costruire il payload di un ResetFault reale
    ## senza dover già conoscere l'id scelto da chi ha iniettato il guasto:
    ## edge/src/faults/fault_detector.cpp osserva solo le EVIDENZE di un
    ## guasto (component/rule/severity), mai l'id con cui è stato iniettato
    ## (netta separazione FaultInjector/FaultDetector, vedi le istruzioni di
    ## progetto), quindi quell'id non è altrimenti recuperabile dopo
    ## l'iniezione.
    active_fault_id: str | None = None


class ZoneUpdate(BaseModel):
    """@brief Modifiche parziali ammesse per un settore esistente.

    @details Posizione fisica e identificativo non sono modificabili. I campi
    nullable distinguono il valore JSON `null` da un campo omesso tramite
    `model_fields_set`.
    """

    ## @brief Rifiuta campi non modificabili o sconosciuti.
    model_config = ConfigDict(extra="forbid")

    ## @brief Nuovo nome leggibile, se specificato.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    ## @brief Nuova specie del settore produttivo, se specificata.
    plant_species: str | None = Field(default=None, min_length=1, max_length=100)
    ## @brief Nuovo Edge assegnato; `null` rimuove l'assegnazione.
    assigned_edge_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Nuova ricetta assegnata; `null` rimuove l'assegnazione.
    active_recipe_id: str | None = Field(default=None, max_length=64)
    ## @brief Nuova disponibilita amministrativa del settore.
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
