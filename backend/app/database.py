"""@file database.py
@brief Facciata compatibile per l'accesso SQLite del backend.

@details Il codice nuovo usa `core.database` e i `repository.py` contenuti
nelle singole feature. Questa facciata mantiene validi gli import precedenti.
"""

from .core.database import DEFAULT_DATABASE_PATH, get_connection, init_db
from .features.actuators.repository import (
    ActuatorSnapshotConflict,
    get_latest_actuator_snapshot,
    list_actuator_snapshots,
    save_actuator_snapshot,
)
from .features.cultivations.repository import (
    CultivationCompatibilityError,
    CultivationConflict,
    CultivationStateError,
    complete_cultivation,
    confirm_cultivation,
    create_cultivation,
    get_active_cultivation_for_zone,
    get_cultivation,
    list_cultivations,
    pause_cultivation,
    record_activation_result,
    resume_cultivation,
)
from .features.recipes.repository import (
    RecipeVersionConflict,
    get_recipe,
    list_recipes,
    save_recipe,
)
from .features.telemetry.repository import (
    TelemetryConflict,
    get_latest_telemetry,
    list_telemetry,
    save_telemetry,
)
from .features.zones.repository import (
    ZoneConflict,
    create_zone,
    get_zone,
    list_zones,
)

__all__ = [
    "ActuatorSnapshotConflict",
    "CultivationCompatibilityError",
    "CultivationConflict",
    "CultivationStateError",
    "DEFAULT_DATABASE_PATH",
    "RecipeVersionConflict",
    "TelemetryConflict",
    "ZoneConflict",
    "complete_cultivation",
    "confirm_cultivation",
    "create_cultivation",
    "create_zone",
    "get_active_cultivation_for_zone",
    "get_connection",
    "get_cultivation",
    "get_latest_actuator_snapshot",
    "get_latest_telemetry",
    "get_recipe",
    "get_zone",
    "init_db",
    "list_cultivations",
    "list_telemetry",
    "list_actuator_snapshots",
    "list_recipes",
    "list_zones",
    "pause_cultivation",
    "record_activation_result",
    "resume_cultivation",
    "save_recipe",
    "save_actuator_snapshot",
    "save_telemetry",
]
