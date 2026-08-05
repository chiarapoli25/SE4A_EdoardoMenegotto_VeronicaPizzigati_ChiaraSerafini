"""@file main.py
@brief Punto di composizione dell'applicazione FastAPI SmartHydro.

@details Il modulo inizializza l'infrastruttura e registra i router dei domini.
Modelli, query SQLite ed endpoint sono definiti nei rispettivi package.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI

from .core.database import get_connection, get_db, init_db
from .core.config import offline_threshold_seconds
from .core.security import require_api_token
from .features.actuators.routes import router as actuator_router
from .features.commands.routes import router as command_router
from .features.events.routes import router as event_router
from .features.plants.routes import router as plant_router
from .features.recipes.routes import router as recipe_router
from .features.system.routes import router as system_router
from .features.telemetry.routes import router as telemetry_router
from .features.zones.routes import (
    edge_router,
    router as zone_router,
)
from .features.zones.repository import refresh_zone_connectivity


async def _connectivity_sweep() -> None:
    """Persiste periodicamente lo stato offline anche senza richieste API."""
    while True:
        threshold = offline_threshold_seconds()
        await asyncio.sleep(min(max(threshold / 4.0, 0.25), 5.0))
        connection = get_connection()
        try:
            refresh_zone_connectivity(
                connection,
                threshold_seconds=threshold,
            )
        finally:
            connection.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """@brief Inizializza lo schema SQLite all'avvio dell'applicazione."""
    offline_threshold_seconds()
    connection = get_connection()
    try:
        init_db(connection)
    finally:
        connection.close()
    connectivity_task = asyncio.create_task(_connectivity_sweep())
    try:
        yield
    finally:
        connectivity_task.cancel()
        with suppress(asyncio.CancelledError):
            await connectivity_task


## @brief Applicazione ASGI principale esposta al server Uvicorn.
app = FastAPI(title="SmartHydro Backend", version="0.1.0", lifespan=lifespan)

app.include_router(system_router)
app.include_router(zone_router)
app.include_router(edge_router)
app.include_router(telemetry_router)
app.include_router(actuator_router)
app.include_router(recipe_router)
app.include_router(event_router)
app.include_router(command_router)
app.include_router(plant_router)

# Contratto versionato usato dai nuovi client Edge. Gli endpoint storici
# restano disponibili per la dashboard e per i test precedenti.
for versioned_router in (
    zone_router,
    edge_router,
    telemetry_router,
    actuator_router,
    recipe_router,
    event_router,
    command_router,
    plant_router,
):
    app.include_router(
        versioned_router,
        prefix="/api/v1",
        dependencies=[Depends(require_api_token)],
    )


__all__ = ["app", "get_db"]
