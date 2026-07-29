"""@file database.py
@brief Facciata compatibile per l'accesso SQLite del backend.

@details Il codice nuovo deve importare connessione e schema da `db.py` e le
query dai rispettivi moduli `repositories`. Questa facciata mantiene validi gli
import usati dalle versioni precedenti del progetto e dai test esistenti.
"""

from .db import DEFAULT_DATABASE_PATH, get_connection, init_db
from .repositories.actuators import (
    ActuatorSnapshotConflict,
    get_latest_actuator_snapshot,
    list_actuator_snapshots,
    save_actuator_snapshot,
)
from .repositories.recipes import (
    RecipeVersionConflict,
    get_recipe,
    save_recipe,
)
from .repositories.telemetry import (
    TelemetryConflict,
    get_latest_telemetry,
    list_telemetry,
    save_telemetry,
)
from .repositories.zones import (
    ZoneConflict,
    create_zone,
    get_zone,
    list_zones,
)

__all__ = [
    "ActuatorSnapshotConflict",
    "DEFAULT_DATABASE_PATH",
    "RecipeVersionConflict",
    "TelemetryConflict",
    "ZoneConflict",
    "create_zone",
    "get_connection",
    "get_latest_actuator_snapshot",
    "get_latest_telemetry",
    "get_recipe",
    "get_zone",
    "init_db",
    "list_telemetry",
    "list_actuator_snapshots",
    "list_zones",
    "save_recipe",
    "save_actuator_snapshot",
    "save_telemetry",
]
