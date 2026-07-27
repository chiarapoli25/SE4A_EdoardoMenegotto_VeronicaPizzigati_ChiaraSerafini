"""Modelli Pydantic della telemetria e della ricetta di SmartHydro.

I modelli della ricetta rispecchiano esattamente il contratto JSON
implementato da ``smarthydro::recipe_json`` (edge/src/recipe_json.cpp e
edge/src/control_system.cpp): stessi nomi di campo, stessi valori di enum,
stessa struttura annidata e le stesse regole controllate da
``RecipeControlSystem::validate_recipe`` al caricamento. Il backend usa
questi modelli per ricevere una ricetta dall'esterno, validarla e
prepararla per il salvataggio e per l'inoltro all'Edge Controller nello
stesso formato che ``recipe_from_json`` si aspetta in lettura.

Non rientrano invece nel caricamento JSON i controlli numerici piu fini
(ordine delle soglie, segno dei guadagni, coerenza dei limiti di comando):
nell'Edge quei controlli avvengono solo quando l'agronomo conferma una
configurazione (``RecipeControlSystem::confirm_configuration``), non
quando la ricetta viene letta da JSON, e non sono quindi replicati qui.
"""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


# --- Telemetria dei sensori ------------------------------------------------


class GreenhouseTelemetry(BaseModel):
    """Campione sincronizzato dei sensori inviato dalla serra.

    Il timestamp e sempre presente; ciascun canale e invece
    indipendentemente opzionale, per rappresentare un dropout strumentale
    del sensore corrispondente.
    """

    timestamp_seconds: float = Field(
        ge=0.0,
        description="Timestamp della misura, in secondi simulati.",
    )
    temperature_c: float | None = Field(
        default=None,
        ge=-50.0,
        le=80.0,
        description="Temperatura dell'aria in gradi Celsius.",
    )
    air_humidity_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Umidita relativa dell'aria, in percentuale.",
    )
    soil_moisture_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Umidita del terriccio, in percentuale.",
    )
    ph: float | None = Field(
        default=None,
        ge=0.0,
        le=14.0,
        description="pH della soluzione nei pori del terriccio.",
    )
    light_ppfd_umol_m2_s: float | None = Field(
        default=None,
        ge=0.0,
        le=3000.0,
        description="PPFD della luce, in umol/(m2 s).",
    )


# --- Enum condivisi dalla ricetta -------------------------------------------


class SoilType(str, Enum):
    """Tipo di substrato, come da ``smarthydro::SoilType``."""

    AERATED_UNIVERSAL = "aerated-universal"
    DRAINING = "draining"
    ORGANIC_RETENTIVE = "organic-retentive"


class ControlledVariable(str, Enum):
    """Variabile controllata da una ricetta, come da ``smarthydro::ControlledVariable``."""

    SOIL_MOISTURE = "soil_moisture"
    LIGHT = "light"
    PH = "ph"
    NITROGEN = "nitrogen"
    PHOSPHORUS = "phosphorus"
    POTASSIUM = "potassium"


class SensorType(str, Enum):
    """Origine del valore usato dal controllo, come da ``smarthydro::SensorType``.

    NITROGEN_MODEL, PHOSPHORUS_MODEL e POTASSIUM_MODEL non sono sensori
    fisici: N/P/K non hanno un canale in ``GreenhouseTelemetry`` perche
    l'Edge li stima dal proprio modello di bilancio di massa, non li misura.
    """

    SOIL_MOISTURE_SENSOR = "soil_moisture_sensor"
    LIGHT_SENSOR = "light_sensor"
    PH_SENSOR = "ph_sensor"
    NITROGEN_MODEL = "nitrogen_model"
    PHOSPHORUS_MODEL = "phosphorus_model"
    POTASSIUM_MODEL = "potassium_model"


class ActuatorType(str, Enum):
    """Attuatore comandato dalla configurazione, come da ``smarthydro::ActuatorType``."""

    WATER_PUMP = "water_pump"
    LIGHTING = "lighting"
    PH_CORRECTOR_VALVES = "ph_corrector_valves"
    NITROGEN_VALVE = "nitrogen_valve"
    PHOSPHORUS_VALVE = "phosphorus_valve"
    POTASSIUM_VALVE = "potassium_valve"


class StrategyType(str, Enum):
    """Strategia di controllo selezionabile, come da ``smarthydro::StrategyType``."""

    THRESHOLD = "Threshold"
    PID = "PID"
    PREDICTIVE = "Predictive"


class ControlDirection(str, Enum):
    """Segno dell'effetto dell'attuatore, come da ``smarthydro::ControlDirection``."""

    INCREASES_PROCESS_VALUE = "increases"
    DECREASES_PROCESS_VALUE = "decreases"


class ConfirmationState(str, Enum):
    """Stato di approvazione agronomica, come da ``smarthydro::ConfirmationState``."""

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    INVALID = "INVALID"


# --- Parametri delle tre Strategy -------------------------------------------
#
# Ogni classe contiene solo cio che recipe_json.cpp legge dal JSON. Ordine
# delle soglie, segno dei guadagni e coerenza dei limiti di comando non sono
# verificati qui: lato Edge lo fa ControllerFactory::create, invocata solo
# alla conferma agronomica, non al caricamento della ricetta.


class ThresholdConfig(BaseModel):
    """Parametri del controllore a soglia, come da ``smarthydro::ThresholdConfig``."""

    lower_threshold: float
    upper_threshold: float
    direction: ControlDirection
    active_command: float
    inactive_command: float
    bidirectional: bool = False


class PidConfig(BaseModel):
    """Parametri del controllore PID, come da ``smarthydro::PidConfig``.

    ``command_minimum``/``command_maximum`` sono i due campi di
    ``CommandLimits``: nel JSON sono appiattiti dentro ai parametri anziche
    annidati in un oggetto separato.
    """

    setpoint: float
    proportional_gain: float
    integral_gain: float
    derivative_gain: float
    command_minimum: float
    command_maximum: float
    direction: ControlDirection


class PredictiveConfig(BaseModel):
    """Parametri del controllore predittivo, come da ``smarthydro::PredictiveConfig``."""

    setpoint: float
    prediction_horizon_steps: float
    response_gain: float
    neutral_command: float
    command_minimum: float
    command_maximum: float
    direction: ControlDirection
    water_dilution_gain: float = 0.0
    cumulative_dose_gain: float = 0.0
    substrate_gain: float = 0.0


ControllerParameters = ThresholdConfig | PidConfig | PredictiveConfig

_PARAMETERS_BY_STRATEGY: dict[StrategyType, type[BaseModel]] = {
    StrategyType.THRESHOLD: ThresholdConfig,
    StrategyType.PID: PidConfig,
    StrategyType.PREDICTIVE: PredictiveConfig,
}


# --- Oggetti di valore della ricetta -----------------------------------------


class ValueRange(BaseModel):
    """Intervallo chiuso, come da ``smarthydro::ValueRange``.

    Non impone minimum <= maximum: nemmeno lo struct C++ lo fa. L'ordine e
    invece imposto da ``PhaseVariableTarget``, dove ``validate_recipe`` lo
    controlla davvero.
    """

    minimum: float
    maximum: float


class Photoperiod(BaseModel):
    """Fotoperiodo giornaliero della fase, come da ``smarthydro::Photoperiod``."""

    start_hour: float = Field(ge=0.0, lt=24.0)
    duration_hours: float = Field(gt=0.0, le=24.0)


class OutputSafetyLimits(BaseModel):
    """Limiti prioritari applicati dopo il calcolo Strategy.

    Come da ``smarthydro::OutputSafetyLimits``.
    """

    maximum_water_volume_liters: float = Field(ge=0.0)
    maximum_pump_duration_seconds: float = Field(ge=0.0)
    water_pump_flow_liters_per_hour: float = Field(ge=0.0)
    maximum_dose_per_command_milliliters: float = Field(ge=0.0)
    maximum_daily_dose_milliliters: float = Field(ge=0.0)
    minimum_seconds_between_doses: float = Field(ge=0.0)
    ph_settling_time_seconds: float = Field(ge=0.0)


class PhaseVariableTarget(BaseModel):
    """Target e limiti di una variabile nella fase.

    Come da ``smarthydro::PhaseVariableTarget``. La catena
    ``safety_range.minimum <= allowed_range.minimum <= setpoint <=
    allowed_range.maximum <= safety_range.maximum``, con i due intervalli
    individualmente non degeneri, e la stessa condizione verificata da
    ``RecipeControlSystem::validate_recipe``.
    """

    variable: ControlledVariable
    setpoint: float
    allowed_range: ValueRange
    safety_range: ValueRange
    suggested_phase_dose_milliliters: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _check_range_chain(self) -> "PhaseVariableTarget":
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
    return set(variables) == set(ControlledVariable)


class RecipePhase(BaseModel):
    """Fase ordinata della ricetta di coltivazione, come da ``smarthydro::RecipePhase``."""

    name: str = Field(min_length=1)
    duration_hours: float = Field(gt=0.0)
    photoperiod: Photoperiod
    targets: list[PhaseVariableTarget] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _check_targets_cover_all_variables(self) -> "RecipePhase":
        if not _covers_all_controlled_variables(
                target.variable for target in self.targets):
            raise ValueError(
                "targets must contain exactly one entry per ControlledVariable")
        return self


_REQUIRED_SENSOR: dict[ControlledVariable, SensorType] = {
    ControlledVariable.SOIL_MOISTURE: SensorType.SOIL_MOISTURE_SENSOR,
    ControlledVariable.LIGHT: SensorType.LIGHT_SENSOR,
    ControlledVariable.PH: SensorType.PH_SENSOR,
    ControlledVariable.NITROGEN: SensorType.NITROGEN_MODEL,
    ControlledVariable.PHOSPHORUS: SensorType.PHOSPHORUS_MODEL,
    ControlledVariable.POTASSIUM: SensorType.POTASSIUM_MODEL,
}

_REQUIRED_ACTUATOR: dict[ControlledVariable, ActuatorType] = {
    ControlledVariable.SOIL_MOISTURE: ActuatorType.WATER_PUMP,
    ControlledVariable.LIGHT: ActuatorType.LIGHTING,
    ControlledVariable.PH: ActuatorType.PH_CORRECTOR_VALVES,
    ControlledVariable.NITROGEN: ActuatorType.NITROGEN_VALVE,
    ControlledVariable.PHOSPHORUS: ActuatorType.PHOSPHORUS_VALVE,
    ControlledVariable.POTASSIUM: ActuatorType.POTASSIUM_VALVE,
}

_NUTRIENT_VARIABLES = frozenset({
    ControlledVariable.NITROGEN,
    ControlledVariable.PHOSPHORUS,
    ControlledVariable.POTASSIUM,
})


def _required_default_strategy(variable: ControlledVariable) -> StrategyType:
    if variable in _NUTRIENT_VARIABLES:
        return StrategyType.PREDICTIVE
    if variable is ControlledVariable.PH:
        return StrategyType.PID
    return StrategyType.THRESHOLD


class ControllerConfiguration(BaseModel):
    """Strategia scelta, associazioni hardware e stato di conferma.

    Come da ``smarthydro::ControllerConfiguration``. ``sensor``,
    ``actuator`` e ``default_strategy`` sono vincolati da ``variable``
    esattamente come impone ``RecipeControlSystem::validate_recipe``. La
    forma di ``parameters`` dipende da ``selected_strategy``, esattamente
    come la sceglie ``recipe_json.cpp::parameters_from_json`` lato Edge.
    """

    variable: ControlledVariable
    sensor: SensorType
    actuator: ActuatorType
    default_strategy: StrategyType
    selected_strategy: StrategyType
    parameters: ControllerParameters
    unit: str = Field(min_length=1)
    output_limits: OutputSafetyLimits
    confirmation_state: ConfirmationState = ConfirmationState.PENDING_CONFIRMATION
    version: int = Field(default=1, ge=1)
    confirmed_recipe_version: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _parse_parameters_for_strategy(cls, data):
        # selected_strategy vive sull'oggetto genitore, non dentro
        # "parameters": e un discriminatore esterno, come lato Edge, quindi
        # non e esprimibile con il discriminator nativo di Pydantic (che
        # richiede il tag dentro l'oggetto stesso). Lo risolviamo qui,
        # rispecchiando recipe_json.cpp::parameters_from_json(strategy, json).
        if not isinstance(data, dict):
            return data
        strategy = data.get("selected_strategy")
        parameters = data.get("parameters")
        if isinstance(parameters, dict) and strategy is not None:
            model = _PARAMETERS_BY_STRATEGY.get(StrategyType(strategy))
            if model is not None:
                data = {**data, "parameters": model.model_validate(parameters)}
        return data

    @model_validator(mode="after")
    def _check_variable_associations(self) -> "ControllerConfiguration":
        if self.sensor != _REQUIRED_SENSOR[self.variable]:
            raise ValueError(
                f"sensor must be {_REQUIRED_SENSOR[self.variable].value} "
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
    """Ricetta completa associata a pianta e substrato, come da ``smarthydro::Recipe``.

    E' il modello di ingresso e di uscita usato dal backend per ricevere una
    ricetta dall'esterno, salvarla e inoltrarla all'Edge Controller nello
    stesso formato JSON che ``recipe_from_json`` si aspetta in lettura.
    """

    id: str = Field(min_length=1)
    plant_type: str = Field(min_length=1)
    substrate: SoilType
    version: int = Field(ge=1)
    phases: list[RecipePhase] = Field(min_length=1)
    controllers: list[ControllerConfiguration] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _check_controllers_cover_all_variables(self) -> "Recipe":
        if not _covers_all_controlled_variables(
                controller.variable for controller in self.controllers):
            raise ValueError(
                "controllers must contain exactly one entry per ControlledVariable")
        return self
