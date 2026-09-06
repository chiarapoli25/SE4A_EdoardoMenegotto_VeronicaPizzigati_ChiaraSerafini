"""@file main.py
@brief Punto di composizione dell'applicazione FastAPI SmartHydro.

@details Il modulo inizializza l'infrastruttura e registra i router dei domini.
Modelli, query SQLite ed endpoint sono definiti nei rispettivi package.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.database import get_connection, get_db, init_db
from .core.config import default_edge_id, offline_threshold_seconds
from .core.security import require_api_token
from .features.actuators.routes import router as actuator_router
from .features.commands.routes import router as command_router
from .features.cultivations.routes import router as cultivation_router
from .features.events.routes import router as event_router
from .features.plants.routes import router as plant_router
from .features.recipes.routes import router as recipe_router
from .features.simulations.routes import router as simulation_router
from .features.system.routes import router as system_router
from .features.telemetry.routes import router as telemetry_router
from .features.users.routes import auth_router, router as user_router
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
    default_edge_id()
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

# Consente al bundle statico aperto direttamente da file:// di usare il
# backend locale documentato. I client serviti da /dashboard restano
# same-origin e non dipendono da questa eccezione di sviluppo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # Content-Type per i body JSON, Authorization per il token di sessione
    # che apiRequest() allega dopo il login (vedi features/users).
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(system_router)
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(zone_router)
app.include_router(edge_router)
app.include_router(telemetry_router)
app.include_router(actuator_router)
app.include_router(recipe_router)
app.include_router(event_router)
app.include_router(command_router)
app.include_router(plant_router)
app.include_router(cultivation_router)
app.include_router(simulation_router)

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
    cultivation_router,
    simulation_router,
):
    app.include_router(
        versioned_router,
        prefix="/api/v1",
        dependencies=[Depends(require_api_token)],
    )


# La control room usa intenzionalmente gli endpoint senza prefisso, mantenuti
# per i client browser same-origin. Il mount resta dopo i router API, cosi la
# directory statica non puo intercettare i relativi path.
DASHBOARD_DIRECTORY = Path(__file__).resolve().parents[2] / "dashboard"
app.mount(
    "/dashboard",
    StaticFiles(directory=DASHBOARD_DIRECTORY, html=True),
    name="dashboard",
)


__all__ = ["app", "get_db"]
