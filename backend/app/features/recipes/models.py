"""@file models.py
@brief Modelli Pydantic delle ricette SmartHydro.

@details I modelli della ricetta rispecchiano il contratto JSON implementato da
`smarthydro::recipe_json` (edge/src/recipe_json.cpp e
edge/src/control_system.cpp): stessi nomi di campo, stessi valori di enum,
stessa struttura annidata e le stesse regole controllate da
`RecipeControlSystem::validate_recipe()` al caricamento. Il backend usa
questi modelli per ricevere una ricetta dall'esterno, validarla e
prepararla per il salvataggio e per l'inoltro all'Edge Controller nello
stesso formato che `recipe_from_json()` si aspetta in lettura.

Non rientrano invece nel caricamento JSON i controlli numerici piu fini
(ordine delle soglie, segno dei guadagni, coerenza dei limiti di comando):
nell'Edge quei controlli avvengono solo quando l'agronomo conferma una
configurazione (`RecipeControlSystem::confirm_configuration()`), non
quando la ricetta viene letta da JSON, e non sono quindi replicati qui.
"""

from enum import Enum

from pydantic import AliasChoices, BaseModel, Field, computed_field, model_validator

from ...greenhouse_layout import PRODUCTION_DEPARTMENT_NAMES, department_name


# --- Enum condivisi dalla ricetta -------------------------------------------


class SoilType(str, Enum):
    """@brief Tipo di substrato, come da `smarthydro::SoilType`."""

    ## @brief Terriccio universale aerato.
    AERATED_UNIVERSAL = "aerated-universal"
    ## @brief Substrato ad alta capacita drenante.
    DRAINING = "draining"
    ## @brief Substrato organico ad alta ritenzione.
    ORGANIC_RETENTIVE = "organic-retentive"


class ControlledVariable(str, Enum):
    """@brief Variabile controllata, come da `smarthydro::ControlledVariable`."""

    ## @brief Umidita percentuale del terriccio.
    SOIL_MOISTURE = "soil_moisture"
    ## @brief Intensita luminosa PPFD.
    LIGHT = "light"
    ## @brief pH dell'acqua presente nei pori del terriccio.
    PH = "ph"
    ## @brief Disponibilita stimata di azoto nella zona radicale.
    NITROGEN = "nitrogen"
    ## @brief Disponibilita stimata di fosforo nella zona radicale.
    PHOSPHORUS = "phosphorus"
    ## @brief Disponibilita stimata di potassio nella zona radicale.
    POTASSIUM = "potassium"


class ControlInputSource(str, Enum):
    """@brief Origine del valore usato dal controllo.

    @details I valori corrispondono a `smarthydro::ControlInputSource`.
    `NITROGEN_MODEL`, `PHOSPHORUS_MODEL` e `POTASSIUM_MODEL` non sono sensori
    fisici: N/P/K non hanno un canale in ``GreenhouseTelemetry`` perche
    l'Edge li stima dal proprio modello di bilancio di massa, non li misura.
    """

    ## @brief Sensore fisico dell'umidita del terriccio.
    SOIL_MOISTURE_SENSOR = "soil_moisture_sensor"
    ## @brief Sensore fisico della luce.
    LIGHT_SENSOR = "light_sensor"
    ## @brief Sensore fisico del pH.
    PH_SENSOR = "ph_sensor"
    ## @brief Stima dell'azoto prodotta dal modello Edge.
    NITROGEN_MODEL = "nitrogen_model"
    ## @brief Stima del fosforo prodotta dal modello Edge.
    PHOSPHORUS_MODEL = "phosphorus_model"
    ## @brief Stima del potassio prodotta dal modello Edge.
    POTASSIUM_MODEL = "potassium_model"


## @brief Alias mantenuto per gli import Python precedenti.
#
# Il contratto JSON canonico usa il nome semanticamente corretto
# `input_source`.
SensorType = ControlInputSource


class ActuatorType(str, Enum):
    """@brief Attuatore comandato, come da `smarthydro::ActuatorType`."""

    ## @brief Pompa comune dell'acqua.
    WATER_PUMP = "water_pump"
    ## @brief Sistema di illuminazione.
    LIGHTING = "lighting"
    ## @brief Coppia di valvole per i correttori pH+ e pH-.
    PH_CORRECTOR_VALVES = "ph_corrector_valves"
    ## @brief Valvola del concentrato di azoto.
    NITROGEN_VALVE = "nitrogen_valve"
    ## @brief Valvola del concentrato di fosforo.
    PHOSPHORUS_VALVE = "phosphorus_valve"
    ## @brief Valvola del concentrato di potassio.
    POTASSIUM_VALVE = "potassium_valve"


class StrategyType(str, Enum):
    """@brief Strategia selezionabile, come da `smarthydro::StrategyType`."""

    ## @brief Controllore a soglia con isteresi.
    THRESHOLD = "Threshold"
    ## @brief Controllore proporzionale-integrale-derivativo.
    PID = "PID"
    ## @brief Controllore predittivo basato sul modello.
    PREDICTIVE = "Predictive"


class ControlDirection(str, Enum):
    """@brief Segno dell'effetto, come da `smarthydro::ControlDirection`."""

    ## @brief Il comando aumenta il valore di processo.
    INCREASES_PROCESS_VALUE = "increases"
    ## @brief Il comando diminuisce il valore di processo.
    DECREASES_PROCESS_VALUE = "decreases"


class ConfirmationState(str, Enum):
    """@brief Stato di approvazione, come da `smarthydro::ConfirmationState`."""

    ## @brief Configurazione in attesa della decisione dell'agronomo.
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    ## @brief Configurazione approvata.
    CONFIRMED = "CONFIRMED"
    ## @brief Configurazione esplicitamente rifiutata.
    REJECTED = "REJECTED"
    ## @brief Configurazione non valida.
    INVALID = "INVALID"


# --- Parametri delle tre Strategy -------------------------------------------
# Ogni classe contiene solo cio che recipe_json.cpp legge dal JSON. L'Edge
# verifica i vincoli numerici piu fini al momento della conferma agronomica.


class ThresholdConfig(BaseModel):
    """@brief Parametri del controllore a soglia.

    @details Il modello corrisponde a `smarthydro::ThresholdConfig`.
    """

    ## @brief Soglia sotto la quale attivare il comando.
    lower_threshold: float
    ## @brief Soglia sopra la quale disattivare o invertire il comando.
    upper_threshold: float
    ## @brief Segno dell'effetto del comando sul processo.
    direction: ControlDirection
    ## @brief Valore emesso quando il controllo e attivo.
    active_command: float
    ## @brief Valore emesso quando il controllo non e attivo.
    inactive_command: float
    ## @brief Abilita un comando con segno opposto oltre la seconda soglia.
    bidirectional: bool = False


class PidConfig(BaseModel):
    """@brief Parametri del controllore PID.

    @details Il modello corrisponde a `smarthydro::PidConfig`.
    `command_minimum` e `command_maximum` sono i due campi di
    `CommandLimits`: nel JSON sono appiattiti dentro ai parametri anziche
    annidati in un oggetto separato.
    """

    ## @brief Valore obiettivo del processo.
    setpoint: float
    ## @brief Guadagno proporzionale.
    proportional_gain: float
    ## @brief Guadagno integrale.
    integral_gain: float
    ## @brief Guadagno derivativo.
    derivative_gain: float
    ## @brief Limite inferiore del comando.
    command_minimum: float
    ## @brief Limite superiore del comando.
    command_maximum: float
    ## @brief Segno dell'effetto del comando sul processo.
    direction: ControlDirection


class PredictiveConfig(BaseModel):
    """@brief Parametri del controllore predittivo.

    @details Il modello corrisponde a `smarthydro::PredictiveConfig`.
    """

    ## @brief Valore obiettivo del processo.
    setpoint: float
    ## @brief Numero di passi temporali dell'orizzonte di previsione.
    prediction_horizon_steps: float
    ## @brief Guadagno della risposta prevista.
    response_gain: float
    ## @brief Comando emesso in assenza di correzioni.
    neutral_command: float
    ## @brief Limite inferiore del comando.
    command_minimum: float
    ## @brief Limite superiore del comando.
    command_maximum: float
    ## @brief Segno dell'effetto del comando sul processo.
    direction: ControlDirection
    ## @brief Guadagno associato alla diluizione prodotta dall'acqua.
    water_dilution_gain: float = 0.0
    ## @brief Guadagno associato alla dose cumulativa.
    cumulative_dose_gain: float = 0.0
    ## @brief Guadagno associato alla risposta del substrato.
    substrate_gain: float = 0.0


## @brief Unione dei parametri ammessi dalle tre strategie.
ControllerParameters = ThresholdConfig | PidConfig | PredictiveConfig

## @brief Associazione fra strategia selezionata e relativo modello Pydantic.
_PARAMETERS_BY_STRATEGY: dict[StrategyType, type[BaseModel]] = {
    StrategyType.THRESHOLD: ThresholdConfig,
    StrategyType.PID: PidConfig,
    StrategyType.PREDICTIVE: PredictiveConfig,
}


# --- Oggetti di valore della ricetta -----------------------------------------


class ValueRange(BaseModel):
    """@brief Intervallo numerico chiuso.

    @details Il modello corrisponde a `smarthydro::ValueRange`. Non impone
    `minimum <= maximum`: l'ordine viene validato da `PhaseVariableTarget`,
    analogamente a `RecipeControlSystem::validate_recipe()` lato Edge.
    """

    ## @brief Estremo inferiore dell'intervallo.
    minimum: float
    ## @brief Estremo superiore dell'intervallo.
    maximum: float


class Photoperiod(BaseModel):
    """@brief Fotoperiodo giornaliero, come da `smarthydro::Photoperiod`."""

    ## @brief Ora di inizio nell'intervallo [0, 24).
    start_hour: float = Field(ge=0.0, lt=24.0)
    ## @brief Durata positiva e non superiore a 24 ore.
    duration_hours: float = Field(gt=0.0, le=24.0)


class OutputSafetyLimits(BaseModel):
    """@brief Limiti prioritari applicati dopo il calcolo Strategy.

    @details Il modello corrisponde a `smarthydro::OutputSafetyLimits`.
    """

    ## @brief Volume massimo d'acqua per comando, in litri.
    maximum_water_volume_liters: float = Field(ge=0.0)
    ## @brief Durata massima di attivazione della pompa, in secondi.
    maximum_pump_duration_seconds: float = Field(ge=0.0)
    ## @brief Portata nominale della pompa, in litri all'ora.
    water_pump_flow_liters_per_hour: float = Field(ge=0.0)
    ## @brief Dose massima per singolo comando, in millilitri.
    maximum_dose_per_command_milliliters: float = Field(ge=0.0)
    ## @brief Dose cumulativa massima giornaliera, in millilitri.
    maximum_daily_dose_milliliters: float = Field(ge=0.0)
    ## @brief Intervallo minimo fra dosaggi consecutivi, in secondi.
    minimum_seconds_between_doses: float = Field(ge=0.0)
    ## @brief Tempo di assestamento del pH, in secondi.
    ph_settling_time_seconds: float = Field(ge=0.0)


class PhaseVariableTarget(BaseModel):
    """@brief Target e limiti di una variabile nella fase.

    @details Il modello corrisponde a `smarthydro::PhaseVariableTarget`. La
    catena `safety_range.minimum <= allowed_range.minimum <= setpoint <=
    allowed_range.maximum <= safety_range.maximum`, con i due intervalli
    individualmente non degeneri, e la stessa condizione verificata da
    `RecipeControlSystem::validate_recipe()`.
    """

    ## @brief Variabile alla quale si applica il target.
    variable: ControlledVariable
    ## @brief Valore obiettivo durante la fase.
    setpoint: float
    ## @brief Intervallo ammesso per il funzionamento ordinario.
    allowed_range: ValueRange
    ## @brief Intervallo assoluto imposto dalla sicurezza.
    safety_range: ValueRange
    ## @brief Dose suggerita per la fase, in millilitri.
    suggested_phase_dose_milliliters: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _check_range_chain(self) -> "PhaseVariableTarget":
        """@brief Verifica l'ordine di setpoint e intervalli.

        @return Istanza validata senza modifiche.
        @throws ValueError Se un intervallo e degenere o la catena dei limiti
            non e rispettata.
        """
        if self.safety_range.minimum > self.allowed_range.minimum:
            raise ValueError(
                "safety_range.minimum must not exceed allowed_range.minimum")
        if self.safety_range.minimum >= self.safety_range.maximum:
            raise ValueError(
                "safety_range.minimum must be less than safety_range.maximum")
        if self.allowed_range.minimum >= self.allowed_range.maximum:
            raise ValueError(
                "allowed_range.minimum must be less than allowed_range.maximum")
        if not self.allowed_range.minimum <= self.setpoint <= self.allowed_range.maximum:
            raise ValueError("setpoint must be within allowed_range")
        if self.allowed_range.maximum > self.safety_range.maximum:
            raise ValueError(
                "allowed_range.maximum must not exceed safety_range.maximum")
        return self


def _covers_all_controlled_variables(variables) -> bool:
    """@brief Verifica la copertura esatta delle variabili controllate.

    @param variables Iterabile di valori `ControlledVariable`.
    @return `True` se ogni variabile compare una sola volta nell'insieme.
    """
    return set(variables) == set(ControlledVariable)


class RecipePhase(BaseModel):
    """@brief Fase ordinata, come da `smarthydro::RecipePhase`."""

    ## @brief Nome descrittivo non vuoto della fase.
    name: str = Field(min_length=1)
    ## @brief Durata positiva della fase, in ore.
    duration_hours: float = Field(gt=0.0)
    ## @brief Finestra giornaliera di illuminazione.
    photoperiod: Photoperiod
    ## @brief Target delle sei variabili controllate.
    targets: list[PhaseVariableTarget] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _check_targets_cover_all_variables(self) -> "RecipePhase":
        """@brief Verifica la presenza di un target per ogni variabile.

        @return Fase validata senza modifiche.
        @throws ValueError Se una variabile manca o compare piu volte.
        """
        if not _covers_all_controlled_variables(
                target.variable for target in self.targets):
            raise ValueError(
                "targets must contain exactly one entry per ControlledVariable")
        return self


class RecipeCareProfile(BaseModel):
    """Indicazioni agronomiche originali associate alla ricetta numerica.

    I quattro campi conservano il testo del ricettario. L'Edge usa invece i
    target numerici presenti nelle fasi e nei controllori.
    """

    light: str = Field(min_length=1)
    watering: str = Field(min_length=1)
    temperature: str = Field(min_length=1)
    fertilization: str = Field(min_length=1)


## @brief Sorgente obbligatoria per ogni variabile controllata.
_REQUIRED_INPUT_SOURCE: dict[ControlledVariable, ControlInputSource] = {
    ControlledVariable.SOIL_MOISTURE: ControlInputSource.SOIL_MOISTURE_SENSOR,
    ControlledVariable.LIGHT: ControlInputSource.LIGHT_SENSOR,
    ControlledVariable.PH: ControlInputSource.PH_SENSOR,
    ControlledVariable.NITROGEN: ControlInputSource.NITROGEN_MODEL,
    ControlledVariable.PHOSPHORUS: ControlInputSource.PHOSPHORUS_MODEL,
    ControlledVariable.POTASSIUM: ControlInputSource.POTASSIUM_MODEL,
}

## @brief Attuatore obbligatorio per ogni variabile controllata.
_REQUIRED_ACTUATOR: dict[ControlledVariable, ActuatorType] = {
    ControlledVariable.SOIL_MOISTURE: ActuatorType.WATER_PUMP,
    ControlledVariable.LIGHT: ActuatorType.LIGHTING,
    ControlledVariable.PH: ActuatorType.PH_CORRECTOR_VALVES,
    ControlledVariable.NITROGEN: ActuatorType.NITROGEN_VALVE,
    ControlledVariable.PHOSPHORUS: ActuatorType.PHOSPHORUS_VALVE,
    ControlledVariable.POTASSIUM: ActuatorType.POTASSIUM_VALVE,
}

## @brief Insieme immutabile delle variabili nutritive N/P/K.
_NUTRIENT_VARIABLES = frozenset({
    ControlledVariable.NITROGEN,
    ControlledVariable.PHOSPHORUS,
    ControlledVariable.POTASSIUM,
})


def _required_default_strategy(variable: ControlledVariable) -> StrategyType:
    """@brief Determina la strategia predefinita di una variabile.

    @param variable Variabile controllata da classificare.
    @return `Predictive` per N/P/K, `PID` per il pH e `Threshold` negli altri
        casi.
    """
    if variable in _NUTRIENT_VARIABLES:
        return StrategyType.PREDICTIVE
    if variable is ControlledVariable.PH:
        return StrategyType.PID
    return StrategyType.THRESHOLD


class ControllerConfiguration(BaseModel):
    """@brief Configurazione completa di un controllore.

    @details Il modello corrisponde a `smarthydro::ControllerConfiguration`.
    `input_source`, `actuator` e `default_strategy` sono vincolati da `variable`
    come imposto da `RecipeControlSystem::validate_recipe()`. La forma di
    `parameters` dipende da `selected_strategy`, come nella funzione
    `parameters_from_json()` lato Edge.
    """

    ## @brief Variabile regolata dal controllore.
    variable: ControlledVariable
    ## @brief Sensore fisico o modello che fornisce il valore di processo.
    input_source: ControlInputSource = Field(
        validation_alias=AliasChoices("input_source", "sensor")
    )
    ## @brief Attuatore comandato dal controllore.
    actuator: ActuatorType
    ## @brief Strategia predefinita imposta per la variabile.
    default_strategy: StrategyType
    ## @brief Strategia attualmente selezionata.
    selected_strategy: StrategyType
    ## @brief Parametri specifici della strategia selezionata.
    parameters: ControllerParameters
    ## @brief Unita di misura del valore controllato.
    unit: str = Field(min_length=1)
    ## @brief Limiti di sicurezza applicati al comando calcolato.
    output_limits: OutputSafetyLimits
    ## @brief Stato della conferma agronomica.
    confirmation_state: ConfirmationState = ConfirmationState.PENDING_CONFIRMATION
    ## @brief Versione positiva della configurazione.
    version: int = Field(default=1, ge=1)
    ## @brief Versione della ricetta alla quale si riferisce la conferma.
    confirmed_recipe_version: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _parse_parameters_for_strategy(cls, data):
        """@brief Converte i parametri nel modello della strategia selezionata.

        @details `selected_strategy` e un discriminatore esterno all'oggetto
        `parameters` e non puo usare direttamente il discriminatore nativo di
        Pydantic. La conversione replica `parameters_from_json()` lato Edge.

        @param data Dati grezzi ricevuti da Pydantic prima della validazione.
        @return Copia dei dati con i parametri tipizzati, oppure il valore
            originale quando non e un dizionario convertibile.
        @throws ValueError Se `selected_strategy` non identifica una strategia.
        """
        if not isinstance(data, dict):
            return data
        if (
            "input_source" in data
            and "sensor" in data
            and data["input_source"] != data["sensor"]
        ):
            raise ValueError(
                "input_source conflicts with legacy sensor"
            )
        strategy = data.get("selected_strategy")
        parameters = data.get("parameters")
        if isinstance(parameters, dict) and strategy is not None:
            model = _PARAMETERS_BY_STRATEGY.get(StrategyType(strategy))
            if model is not None:
                data = {**data, "parameters": model.model_validate(parameters)}
        return data

    @model_validator(mode="after")
    def _check_variable_associations(self) -> "ControllerConfiguration":
        """@brief Verifica le associazioni fra variabile, sorgente e attuatore.

        @return Configurazione validata senza modifiche.
        @throws ValueError Se sensore, attuatore o strategia predefinita non
            corrispondono alla variabile.
        """
        if self.input_source != _REQUIRED_INPUT_SOURCE[self.variable]:
            raise ValueError(
                "input_source must be "
                f"{_REQUIRED_INPUT_SOURCE[self.variable].value} "
                f"for variable {self.variable.value}")
        if self.actuator != _REQUIRED_ACTUATOR[self.variable]:
            raise ValueError(
                f"actuator must be {_REQUIRED_ACTUATOR[self.variable].value} "
                f"for variable {self.variable.value}")
        required_strategy = _required_default_strategy(self.variable)
        if self.default_strategy != required_strategy:
            raise ValueError(
                f"default_strategy must be {required_strategy.value} "
                f"for variable {self.variable.value}")
        return self


class Recipe(BaseModel):
    """@brief Ricetta completa associata a pianta e substrato.

    @details Il modello corrisponde a `smarthydro::Recipe` ed e usato dal backend
    come ingresso e uscita per ricevere una
    ricetta dall'esterno, salvarla e inoltrarla all'Edge Controller nello
    stesso formato JSON atteso da `recipe_from_json()`.
    """

    ## @brief Identificativo univoco non vuoto della ricetta.
    id: str = Field(min_length=1)
    ## @brief Specie o tipologia della pianta.
    plant_type: str = Field(min_length=1)
    ## @brief Substrato impiegato dalla coltivazione.
    substrate: SoilType
    ## @brief Versione positiva della ricetta.
    version: int = Field(ge=1)
    ## @brief Reparto produttivo al quale appartiene la specie, se catalogata.
    department_number: int | None = Field(default=None, ge=1, le=4)
    ## @brief Indicazioni qualitative riportate dal ricettario della serra.
    care_profile: RecipeCareProfile | None = None
    ## @brief Sequenza non vuota delle fasi di coltivazione.
    phases: list[RecipePhase] = Field(min_length=1)
    ## @brief Configurazioni delle sei variabili controllate.
    controllers: list[ControllerConfiguration] = Field(min_length=6, max_length=6)

    @computed_field
    @property
    def department_name(self) -> str | None:
        """Nome canonico del reparto delle ricette catalogate."""
        if self.department_number is None:
            return None
        return department_name(self.department_number)

    @model_validator(mode="after")
    def _check_controllers_cover_all_variables(self) -> "Recipe":
        """@brief Verifica la presenza di un controllore per ogni variabile.

        @return Ricetta validata senza modifiche.
        @throws ValueError Se una variabile manca o compare piu volte.
        """
        if not _covers_all_controlled_variables(
                controller.variable for controller in self.controllers):
            raise ValueError(
                "controllers must contain exactly one entry per ControlledVariable")
        if (
            self.department_number is not None
            and self.department_number not in PRODUCTION_DEPARTMENT_NAMES
        ):
            raise ValueError("recipes can belong only to departments 1-4")
        return self


class RecipeSummary(BaseModel):
    """Dati essenziali usati dal selettore ricette della dashboard."""

    id: str
    plant_type: str
    substrate: SoilType
    version: int
    phase_count: int = Field(ge=1)


class SimulationRequest(BaseModel):
    """Parametri di una simulazione Edge finita e riproducibile."""

    recipe_id: str = Field(min_length=1)
    steps: int = Field(default=96, ge=1, le=672)
    step_seconds: float = Field(default=900.0, ge=60.0, le=3600.0)
