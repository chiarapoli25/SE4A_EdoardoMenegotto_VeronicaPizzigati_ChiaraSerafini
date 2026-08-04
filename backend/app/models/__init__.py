"""@file __init__.py
@brief Facciata pubblica dei modelli Pydantic del backend.

@details I modelli reali appartengono alle rispettive feature. Questo package
mantiene valido l'import storico `backend.app.models`.
"""

from ..features.actuators.models import (
    ActuatorCommandState,
    ActuatorPhysicalOutput,
    ActuatorSnapshot,
    ActuatorSnapshotCreate,
    FertilizerQuantities,
    FertilizerValveStates,
)
from ..features.plants.models import (
    Plant,
    PlantCreate,
    PlantMovement,
    PlantQuarantineUpdate,
)
from ..features.recipes.models import (
    ActuatorType,
    ConfirmationState,
    ControlDirection,
    ControlledVariable,
    ControllerConfiguration,
    ControllerParameters,
    OutputSafetyLimits,
    PhaseVariableTarget,
    Photoperiod,
    PidConfig,
    PredictiveConfig,
    Recipe,
    RecipePhase,
    SensorType,
    SoilType,
    StrategyType,
    ThresholdConfig,
    ValueRange,
)
from ..features.telemetry.models import (
    GreenhouseTelemetry,
    TelemetryCreate,
    TelemetrySample,
)
from ..features.zones.models import (
    Zone,
    ZoneAdministrativeStatus,
    ZoneCreate,
    ZoneStatus,
    ZoneUpdate,
)

__all__ = [
    "ActuatorCommandState",
    "ActuatorPhysicalOutput",
    "ActuatorSnapshot",
    "ActuatorSnapshotCreate",
    "ActuatorType",
    "ConfirmationState",
    "ControlDirection",
    "ControlledVariable",
    "ControllerConfiguration",
    "ControllerParameters",
    "FertilizerQuantities",
    "FertilizerValveStates",
    "GreenhouseTelemetry",
    "OutputSafetyLimits",
    "PhaseVariableTarget",
    "Plant",
    "PlantCreate",
    "PlantMovement",
    "PlantQuarantineUpdate",
    "Photoperiod",
    "PidConfig",
    "PredictiveConfig",
    "Recipe",
    "RecipePhase",
    "SensorType",
    "SoilType",
    "StrategyType",
    "TelemetryCreate",
    "TelemetrySample",
    "ThresholdConfig",
    "ValueRange",
    "Zone",
    "ZoneAdministrativeStatus",
    "ZoneCreate",
    "ZoneStatus",
    "ZoneUpdate",
]
