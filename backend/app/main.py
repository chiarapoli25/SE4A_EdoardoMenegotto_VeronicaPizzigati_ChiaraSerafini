"""Applicazione FastAPI e relativi endpoint HTTP di SmartHydro.

Il modulo costruisce l'oggetto ASGI importato dal server e definisce gli
endpoint pubblici disponibili nella fase corrente. Mantiene le ricette in
SQLite e le esporta come file JSON a ogni salvataggio; non esiste ancora
un canale diretto verso il processo dell'Edge Controller.
"""

import sqlite3
from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from app.database import (
    RecipeVersionConflict,
    get_connection,
    get_recipe,
    init_db,
    save_recipe,
)
from app.models import Recipe
from app.recipe_export import (
    DEFAULT_EXPORT_DIRECTORY,
    UnsafeRecipeId,
    assert_safe_recipe_id,
    export_recipe_for_edge,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Crea la tabella delle ricette all'avvio, se non esiste gia."""
    connection = get_connection()
    try:
        init_db(connection)
    finally:
        connection.close()
    yield


## Applicazione ASGI principale, con titolo e versione esposti da OpenAPI.
app = FastAPI(title="SmartHydro Backend", version="0.1.0", lifespan=lifespan)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Apre una connessione SQLite per la durata di una singola richiesta."""
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


def get_export_directory() -> Path:
    """Cartella in cui viene scritto il file JSON per l'Edge Controller."""
    return DEFAULT_EXPORT_DIRECTORY


@app.get("/")
def read_root() -> dict[str, str]:
    """Restituisce l'identita pubblica del servizio.

    Restituisce un dizionario JSON-serializzabile con nome del backend e
    versione applicativa. La funzione non modifica stato e non accede a
    risorse esterne.
    """
    return {"name": "SmartHydro Backend", "version": "0.1.0"}


@app.get("/health")
def read_health() -> dict[str, str]:
    """Segnala che il processo HTTP e in esecuzione.

    Restituisce un dizionario JSON-serializzabile con ``status`` uguale a
    ``"healthy"``. Il risultato e statico: non verifica database, sensori,
    attuatori o collegamento con l'Edge Controller.
    """
    return {"status": "healthy"}


@app.post("/recipes", response_model=Recipe, status_code=201)
def create_recipe(
    recipe: Recipe,
    connection: sqlite3.Connection = Depends(get_db),
    export_directory: Path = Depends(get_export_directory),
) -> Recipe:
    """Riceve una ricetta, la valida via Pydantic, la salva in SQLite e la
    esporta come file JSON per l'Edge Controller.

    La ricetta e identificata dal proprio campo ``id``: un nuovo invio con
    lo stesso id sostituisce quello salvato solo se ``version`` e
    maggiore, altrimenti la richiesta e rifiutata con 409 e il file
    esportato in precedenza resta invariato.
    """
    try:
        assert_safe_recipe_id(recipe.id, export_directory)
    except UnsafeRecipeId as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        save_recipe(connection, recipe)
    except RecipeVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    export_recipe_for_edge(recipe, export_directory)
    return recipe


@app.get("/recipes/{recipe_id}", response_model=Recipe)
def read_recipe(
    recipe_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """Restituisce la ricetta salvata, nello stesso formato JSON atteso
    da ``recipe_from_json`` lato Edge Controller.
    """
    recipe = get_recipe(connection, recipe_id)
    if recipe is None:
        raise HTTPException(
            status_code=404, detail=f"recipe {recipe_id!r} not found")
    return recipe
