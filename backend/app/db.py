"""@file db.py
@brief Facciata compatibile dell'infrastruttura SQLite.

@details Il codice nuovo importa da `backend.app.core.database`.
"""

from .core.database import (
    DEFAULT_DATABASE_PATH,
    get_connection,
    get_db,
    init_db,
)

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "get_connection",
    "get_db",
    "init_db",
]
