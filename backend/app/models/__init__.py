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
<<<<<<< HEAD
from ..features.cultivations.models import (
    Cultivation,
    CultivationActivationResult,
    CultivationConfirm,
    CultivationCreate,
    CultivationProgress,
    CultivationStatus,
=======
from ..features.plants.models import (
    Plant,
    PlantCreate,
    PlantMovement,
    PlantQuarantineUpdate,
>>>>>>> 9739fd89a2d793974daf9df49e585172e2fac6ec
)
from ..features.recipes.models import (
    ActuatorType,
    ConfirmationState,
    ControlInputSource,
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
    RecipeCareProfile,
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
    ControlSetpoints,
    ControlStrategies,
    OperationalState,
    StrategyName,
    Zone,
    ZoneAdministrativeStatus,
    ZoneCreate,
    ZoneLifecycleState,
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
<<<<<<< HEAD
    "Cultivation",
    "CultivationActivationResult",
    "CultivationConfirm",
    "CultivationCreate",
    "CultivationProgress",
    "CultivationStatus",
=======
    "ControlInputSource",
>>>>>>> 9739fd89a2d793974daf9df49e585172e2fac6ec
    "ControlDirection",
    "ControlledVariable",
    "ControllerConfiguration",
    "ControllerParameters",
    "ControlSetpoints",
    "ControlStrategies",
    "FertilizerQuantities",
    "FertilizerValveStates",
    "GreenhouseTelemetry",
    "OutputSafetyLimits",
    "OperationalState",
    "PhaseVariableTarget",
    "Plant",
    "PlantCreate",
    "PlantMovement",
    "PlantQuarantineUpdate",
    "Photoperiod",
    "PidConfig",
    "PredictiveConfig",
    "Recipe",
    "RecipeCareProfile",
    "RecipePhase",
    "SensorType",
    "SoilType",
    "StrategyType",
    "StrategyName",
    "TelemetryCreate",
    "TelemetrySample",
    "ThresholdConfig",
    "ValueRange",
    "Zone",
    "ZoneAdministrativeStatus",
    "ZoneCreate",
    "ZoneLifecycleState",
    "ZoneStatus",
    "ZoneUpdate",
]
