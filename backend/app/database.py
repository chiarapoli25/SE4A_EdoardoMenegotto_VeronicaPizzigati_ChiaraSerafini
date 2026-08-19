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
from .features.audit.repository import list_audit_events, record_audit_event
from .features.auth.repository import (
    UsernameConflict,
    authenticate_user,
    create_user,
    get_user,
    get_user_by_username,
    list_users,
)
from .features.cultivations.repository import (
    CultivationCompatibilityError,
    CultivationConflict,
    CultivationStateError,
    apply_activation_command_result,
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
    list_recipe_versions,
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
    ZoneUpdateConflict,
    ZoneUpdateInvalid,
    create_zone,
    get_zone,
    list_zones,
    update_zone,
)

__all__ = [
    "ActuatorSnapshotConflict",
    "CultivationCompatibilityError",
    "CultivationConflict",
    "CultivationStateError",
    "DEFAULT_DATABASE_PATH",
    "RecipeVersionConflict",
    "TelemetryConflict",
    "UsernameConflict",
    "ZoneConflict",
    "ZoneUpdateConflict",
    "ZoneUpdateInvalid",
    "apply_activation_command_result",
    "authenticate_user",
    "complete_cultivation",
    "confirm_cultivation",
    "create_cultivation",
    "create_user",
    "create_zone",
    "get_active_cultivation_for_zone",
    "get_connection",
    "get_cultivation",
    "get_latest_actuator_snapshot",
    "get_latest_telemetry",
    "get_recipe",
    "get_user",
    "get_user_by_username",
    "get_zone",
    "init_db",
    "list_cultivations",
    "list_telemetry",
    "list_actuator_snapshots",
    "list_recipe_versions",
    "list_audit_events",
    "list_recipes",
    "list_users",
    "list_zones",
    "pause_cultivation",
    "record_activation_result",
    "record_audit_event",
    "resume_cultivation",
    "save_recipe",
    "save_actuator_snapshot",
    "save_telemetry",
    "update_zone",
]
