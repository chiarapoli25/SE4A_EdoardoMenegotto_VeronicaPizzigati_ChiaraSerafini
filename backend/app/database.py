"""@file database.py
@brief Facciata compatibile per l'accesso SQLite del backend.

@details Il codice nuovo deve importare connessione e schema da `db.py` e le
query dai rispettivi moduli `repositories`. Questa facciata mantiene validi gli
import usati dalle versioni precedenti del progetto e dai test esistenti.
"""

from .db import DEFAULT_DATABASE_PATH, get_connection, init_db
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
    "DEFAULT_DATABASE_PATH",
    "RecipeVersionConflict",
    "TelemetryConflict",
    "ZoneConflict",
    "create_zone",
    "get_connection",
    "get_latest_telemetry",
    "get_recipe",
    "get_zone",
    "init_db",
    "list_telemetry",
    "list_zones",
    "save_recipe",
    "save_telemetry",
]
