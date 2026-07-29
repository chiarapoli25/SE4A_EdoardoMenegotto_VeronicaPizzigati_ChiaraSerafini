"""@file __init__.py
@brief Facciata pubblica dei modelli Pydantic del backend.

@details Mantiene valido l'import `backend.app.models` e raccoglie i modelli
separati per dominio nei moduli `recipe`, `zone`, `telemetry` e `actuator`.
"""

from .actuator import (
    ActuatorCommandState,
    ActuatorPhysicalOutput,
    ActuatorSnapshot,
    ActuatorSnapshotCreate,
    FertilizerQuantities,
    FertilizerValveStates,
)
from .recipe import (
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
from .telemetry import GreenhouseTelemetry, TelemetryCreate, TelemetrySample
from .zone import Zone, ZoneCreate, ZoneStatus

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
    "SensorType",
    "SoilType",
    "StrategyType",
    "TelemetryCreate",
    "TelemetrySample",
    "ThresholdConfig",
    "ValueRange",
    "Zone",
    "ZoneCreate",
    "ZoneStatus",
]
