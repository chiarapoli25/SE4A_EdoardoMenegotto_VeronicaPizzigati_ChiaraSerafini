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
    RecipeSummary,
    SensorType,
    SoilType,
    StrategyType,
    SimulationRequest,
    ThresholdConfig,
    ValueRange,
)
from ..features.telemetry.models import (
    GreenhouseTelemetry,
    TelemetryCreate,
    TelemetrySample,
)
from ..features.zones.models import Zone, ZoneCreate, ZoneStatus

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
    "Photoperiod",
    "PidConfig",
    "PredictiveConfig",
    "Recipe",
    "RecipePhase",
    "RecipeSummary",
    "SensorType",
    "SoilType",
    "StrategyType",
    "SimulationRequest",
    "TelemetryCreate",
    "TelemetrySample",
    "ThresholdConfig",
    "ValueRange",
    "Zone",
    "ZoneCreate",
    "ZoneStatus",
]
