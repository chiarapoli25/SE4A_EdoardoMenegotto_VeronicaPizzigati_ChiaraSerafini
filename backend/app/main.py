"""@file main.py
@brief Punto di composizione dell'applicazione FastAPI SmartHydro.

@details Il modulo inizializza l'infrastruttura e registra i router dei domini.
Modelli, query SQLite ed endpoint sono definiti nei rispettivi package.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import get_connection, get_db, init_db
from .routes.recipes import get_export_directory, router as recipe_router
from .routes.system import router as system_router
from .routes.telemetry import router as telemetry_router
from .routes.zones import router as zone_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """@brief Inizializza lo schema SQLite all'avvio dell'applicazione."""
    connection = get_connection()
    try:
        init_db(connection)
    finally:
        connection.close()
    yield


## @brief Applicazione ASGI principale esposta al server Uvicorn.
app = FastAPI(title="SmartHydro Backend", version="0.1.0", lifespan=lifespan)

app.include_router(system_router)
app.include_router(zone_router)
app.include_router(telemetry_router)
app.include_router(recipe_router)


__all__ = ["app", "get_db", "get_export_directory"]
