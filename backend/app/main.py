"""@file main.py
@brief Applicazione FastAPI e relativi endpoint HTTP di SmartHydro.

@details Il modulo costruisce l'oggetto ASGI importato dal server, serve la
dashboard, mantiene le ricette in SQLite, le esporta come JSON e orchestra
simulazioni finite tramite il processo Edge Controller.
"""

import sqlite3
from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .database import (
    RecipeVersionConflict,
    get_connection,
    get_recipe,
    init_db,
    list_recipes,
    save_recipe,
)
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
    DEFAULT_EXPORT_DIRECTORY,
    UnsafeRecipeId,
    assert_safe_recipe_id,
    export_recipe_for_edge,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIRECTORY = PROJECT_ROOT / "dashboard"
EXAMPLE_RECIPE_PATH = PROJECT_ROOT / "config" / "example_recipe.json"
SUPPORTED_PLANT_TYPE = "Tomato"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """@brief Inizializza il database durante l'avvio dell'applicazione.

    @param app Applicazione FastAPI gestita dal contesto di vita.
    @return Generatore asincrono che cede il controllo dopo l'inizializzazione.
    """
    connection = get_connection()
    try:
        init_db(connection)
    finally:
        connection.close()
    yield


## @brief Applicazione ASGI principale esposta al server Uvicorn.
app = FastAPI(title="SmartHydro Backend", version="0.1.0", lifespan=lifespan)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """@brief Fornisce una connessione SQLite per una singola richiesta.

    @return Generatore che espone la connessione e la chiude al termine della
        richiesta.
    """
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


def get_export_directory() -> Path:
    """@brief Restituisce la cartella di esportazione delle ricette.

    @return Percorso predefinito dei file JSON destinati all'Edge Controller.
    """
    return DEFAULT_EXPORT_DIRECTORY


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


@app.get("/")
def read_root() -> dict[str, str]:
    """@brief Restituisce l'identita pubblica del servizio.

    @details Restituisce un dizionario JSON-serializzabile con nome del backend e
    versione applicativa. La funzione non modifica stato e non accede a
    risorse esterne.

    @return Nome e versione del backend.
    """
    return {"name": "SmartHydro Backend", "version": "0.1.0"}


@app.get("/health")
def read_health() -> dict[str, str]:
    """@brief Segnala che il processo HTTP e in esecuzione.

    @details Restituisce un dizionario JSON-serializzabile con `status` uguale a
    `"healthy"`. Il risultato e statico: non verifica database, sensori,
    attuatori o collegamento con l'Edge Controller.

    @return Stato statico di disponibilita del processo.
    """
    return {"status": "healthy"}


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
