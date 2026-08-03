"""@file main.py
@brief Punto di composizione dell'applicazione FastAPI SmartHydro.

@details Il modulo inizializza l'infrastruttura e registra i router dei domini.
I servizi della dashboard sono composti sopra le feature senza duplicarne la
persistenza o i contratti.
"""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .database import (
    RecipeVersionConflict,
    get_recipe,
    list_recipes,
    save_recipe,
)
from .core.database import get_connection, get_db, init_db
from .core.security import require_api_token
from .edge_runner import (
    EdgeExecutionFailed,
    EdgeOutputInvalid,
    EdgeTimedOut,
    EdgeUnavailable,
    configured_edge_executable,
    edge_is_ready,
    run_edge_simulation,
)
from .models import (
    ConfirmationState,
    Recipe,
    RecipeSummary,
    SimulationRequest,
)
from .recipe_export import (
    UnsafeRecipeId,
    assert_safe_recipe_id,
    export_recipe_for_edge,
)
from .features.actuators.routes import router as actuator_router
from .features.commands.routes import router as command_router
from .features.events.routes import router as event_router
from .features.recipes.routes import (
    get_export_directory,
    router as recipe_router,
)
from .features.system.routes import router as system_router
from .features.telemetry.routes import router as telemetry_router
from .features.zones.routes import (
    edge_router,
    router as zone_router,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIRECTORY = PROJECT_ROOT / "dashboard"
EXAMPLE_RECIPE_PATH = PROJECT_ROOT / "config" / "example_recipe.json"
SUPPORTED_PLANT_TYPE = "Tomato"


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
app.include_router(edge_router)
app.include_router(telemetry_router)
app.include_router(actuator_router)
app.include_router(event_router)
app.include_router(command_router)

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
):
    app.include_router(
        versioned_router,
        prefix="/api/v1",
        dependencies=[Depends(require_api_token)],
    )


def get_edge_executable() -> Path:
    """Restituisce il percorso configurato dell'eseguibile Edge."""
    return configured_edge_executable()


def _prepare_recipe_for_save(recipe: Recipe) -> Recipe:
    """Applica i vincoli della demo e invalida conferme precedenti."""
    if recipe.plant_type != SUPPORTED_PLANT_TYPE:
        raise HTTPException(
            status_code=422,
            detail=f"only {SUPPORTED_PLANT_TYPE!r} recipes are supported",
        )
    controllers = [
        controller.model_copy(
            update={
                "confirmation_state": ConfirmationState.PENDING_CONFIRMATION,
                "confirmed_recipe_version": 0,
            },
            deep=True,
        )
        for controller in recipe.controllers
    ]
    return recipe.model_copy(update={"controllers": controllers}, deep=True)


@app.get("/system/status")
def read_system_status(
    connection: sqlite3.Connection = Depends(get_db),
    edge_executable: Path = Depends(get_edge_executable),
) -> dict[str, object]:
    """Verifica i componenti necessari alla demo locale."""
    database_ready = False
    try:
        connection.execute("SELECT 1").fetchone()
        database_ready = True
    except sqlite3.Error:
        database_ready = False
    edge_ready = edge_is_ready(edge_executable)
    return {
        "status": "ready" if database_ready and edge_ready else "degraded",
        "dashboard": "ready",
        "backend": "ready",
        "database": "ready" if database_ready else "unavailable",
        "edge": "ready" if edge_ready else "unavailable",
    }


@app.get("/recipes", response_model=list[RecipeSummary])
def read_recipes(
    connection: sqlite3.Connection = Depends(get_db),
) -> list[RecipeSummary]:
    """Elenca le ricette disponibili per la dashboard."""
    return [
        RecipeSummary(
            id=recipe.id,
            plant_type=recipe.plant_type,
            substrate=recipe.substrate,
            version=recipe.version,
            phase_count=len(recipe.phases),
        )
        for recipe in list_recipes(connection)
        if recipe.plant_type == SUPPORTED_PLANT_TYPE
    ]


@app.get("/recipes/template", response_model=Recipe)
def read_recipe_template() -> Recipe:
    """Restituisce il modello iniziale del pomodoro senza salvarlo."""
    return Recipe.model_validate_json(
        EXAMPLE_RECIPE_PATH.read_text(encoding="utf-8")
    )


@app.post("/recipes", response_model=Recipe, status_code=201)
def create_recipe(
    recipe: Recipe,
    connection: sqlite3.Connection = Depends(get_db),
    export_directory: Path = Depends(get_export_directory),
) -> Recipe:
    """@brief Valida, salva ed esporta una ricetta.

    @details La ricetta e identificata dal proprio campo `id`: un nuovo invio con
    lo stesso id sostituisce quello salvato solo se `version` e
    maggiore, altrimenti la richiesta e rifiutata con 409 e il file
    esportato in precedenza resta invariato.

    @param recipe Ricetta gia validata da Pydantic.
    @param connection Connessione SQLite associata alla richiesta.
    @param export_directory Directory di destinazione del file JSON.
    @return Ricetta salvata, usata da FastAPI come corpo della risposta 201.
    @throws HTTPException Se l'identificativo non e sicuro o la versione non e
        strettamente maggiore di quella memorizzata.
    """
    prepared_recipe = _prepare_recipe_for_save(recipe)
    try:
        assert_safe_recipe_id(prepared_recipe.id, export_directory)
    except UnsafeRecipeId as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        save_recipe(connection, prepared_recipe)
    except RecipeVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    export_recipe_for_edge(prepared_recipe, export_directory)
    return prepared_recipe


@app.get("/recipes/{recipe_id}", response_model=Recipe)
def read_recipe(
    recipe_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """@brief Recupera una ricetta tramite il suo identificativo.

    @details La risposta usa lo stesso formato JSON atteso da
    `recipe_from_json()` lato Edge Controller.

    @param recipe_id Identificativo univoco della ricetta.
    @param connection Connessione SQLite associata alla richiesta.
    @return Ricetta deserializzata dal database.
    @throws HTTPException Se non esiste una ricetta con l'identificativo
        richiesto.
    """
    recipe = get_recipe(connection, recipe_id)
    if recipe is None:
        raise HTTPException(
            status_code=404, detail=f"recipe {recipe_id!r} not found")
    return recipe


@app.post("/simulations")
def create_simulation(
    request: SimulationRequest,
    connection: sqlite3.Connection = Depends(get_db),
    export_directory: Path = Depends(get_export_directory),
    edge_executable: Path = Depends(get_edge_executable),
) -> dict[str, object]:
    """Esegue una simulazione finita usando la ricetta salvata."""
    recipe = get_recipe(connection, request.recipe_id)
    if recipe is None:
        raise HTTPException(
            status_code=404,
            detail=f"recipe {request.recipe_id!r} not found",
        )
    recipe_path = export_recipe_for_edge(recipe, export_directory)
    try:
        return run_edge_simulation(
            edge_executable,
            recipe_path,
            steps=request.steps,
            step_seconds=request.step_seconds,
        )
    except EdgeUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except EdgeTimedOut as error:
        raise HTTPException(status_code=504, detail=str(error)) from error
    except EdgeExecutionFailed as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EdgeOutputInvalid as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


app.mount(
    "/dashboard",
    StaticFiles(directory=DASHBOARD_DIRECTORY, html=True),
    name="dashboard",
)

__all__ = ["app", "get_db", "get_export_directory"]
