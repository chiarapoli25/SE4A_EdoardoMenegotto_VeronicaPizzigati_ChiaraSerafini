"""@file main.py
@brief Applicazione FastAPI e relativi endpoint HTTP di SmartHydro.

@details Il modulo costruisce l'oggetto ASGI importato dal server e definisce
gli endpoint pubblici disponibili nella fase corrente. Mantiene in SQLite le
zone fisiche della serra e le ricette, che esporta come file JSON a ogni
salvataggio; non esiste ancora un canale diretto verso il processo dell'Edge
Controller.
"""

import sqlite3
from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from .database import (
    RecipeVersionConflict,
    ZoneConflict,
    create_zone,
    get_connection,
    get_recipe,
    get_zone,
    init_db,
    list_zones,
    save_recipe,
)
from .models import Recipe, Zone, ZoneCreate
from .recipe_export import (
    DEFAULT_EXPORT_DIRECTORY,
    UnsafeRecipeId,
    assert_safe_recipe_id,
    export_recipe_for_edge,
)


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


@app.post("/zones", response_model=Zone, status_code=201)
def register_zone(
    zone: ZoneCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Zone:
    """@brief Registra uno dei settori fisici della serra.

    @details Sono ammessi quattro reparti con due settori ciascuno. Il vincolo
    univoco `(department_number, sector_number)` impedisce di assegnare due
    zone allo stesso settore. Ogni zona contiene una sola specie vegetale.

    @param zone Identita, posizione e specie del settore.
    @param connection Connessione SQLite associata alla richiesta.
    @return Zona creata con stato iniziale `offline`.
    @throws HTTPException Se id o posizione fisica sono gia occupati.
    """
    try:
        return create_zone(connection, zone)
    except ZoneConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/zones", response_model=list[Zone])
def read_zones(
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Zone]:
    """@brief Elenca tutti i settori registrati.

    @param connection Connessione SQLite associata alla richiesta.
    @return Zone ordinate prima per reparto e poi per settore.
    """
    return list_zones(connection)


@app.get("/zones/{zone_id}", response_model=Zone)
def read_zone(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Zone:
    """@brief Recupera un settore tramite il suo identificativo.

    @param zone_id Identificativo della zona richiesta.
    @param connection Connessione SQLite associata alla richiesta.
    @return Zona trovata.
    @throws HTTPException Se la zona non esiste.
    """
    zone = get_zone(connection, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")
    return zone


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
