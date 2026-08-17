"""@file routes.py
@brief Endpoint HTTP per validazione e distribuzione delle ricette.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.database import get_db
from .models import Recipe
from .repository import (
    RecipeVersionConflict,
    get_recipe,
    list_recipe_versions,
    list_recipes,
    save_recipe,
)


## @brief Router delle ricette versionate.
router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.post("", response_model=Recipe, status_code=201)
def create_recipe(
    recipe: Recipe,
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """Valida e salva integralmente la ricetta in SQLite."""
    try:
        save_recipe(connection, recipe)
    except RecipeVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return recipe


@router.get("", response_model=list[Recipe])
def read_recipes(
    department_number: int | None = None,
    plant_species: str | None = Query(default=None, min_length=1),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Recipe]:
    """Elenca le ricette, eventualmente filtrate per reparto o specie.

    @param plant_species Se presente, filtra per `Recipe.plant_type`
        (confronto case-insensitive).
    """
    recipes = list_recipes(connection)
    if department_number is not None:
        if department_number not in range(1, 5):
            raise HTTPException(
                status_code=422,
                detail="department_number must be between 1 and 4",
            )
        recipes = [
            recipe for recipe in recipes
            if recipe.department_number == department_number
        ]
    if plant_species is not None:
        needle = plant_species.casefold()
        recipes = [
            recipe for recipe in recipes
            if recipe.plant_type.casefold() == needle
        ]
    return recipes


@router.get("/{recipe_id}/versions", response_model=list[Recipe])
def read_recipe_versions(
    recipe_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Recipe]:
    """@brief Elenca tutte le versioni salvate di una ricetta.

    @throws HTTPException 404 se non esiste nessuna versione di `recipe_id`.
    """
    versions = list_recipe_versions(connection, recipe_id)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail=f"recipe {recipe_id!r} not found",
        )
    return versions


@router.get("/{recipe_id}/versions/{version}", response_model=Recipe)
def read_recipe_version(
    recipe_id: str,
    version: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """@brief Recupera una versione precisa di una ricetta."""
    recipe = get_recipe(connection, recipe_id, version)
    if recipe is None:
        raise HTTPException(
            status_code=404,
            detail=f"recipe {recipe_id!r} version {version} not found",
        )
    return recipe


@router.get("/{recipe_id}", response_model=Recipe)
def read_recipe(
    recipe_id: str,
    version: int | None = Query(default=None, ge=1),
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """@brief Recupera una ricetta tramite identificativo.

    @param version Versione esatta richiesta, oppure `None` per l'ultima
        versione disponibile. Mantenuto per compatibilita: l'equivalente
        esplicito e `GET /recipes/{recipe_id}/versions/{version}`.
    """
    recipe = get_recipe(connection, recipe_id, version)
    if recipe is None:
        raise HTTPException(
            status_code=404,
            detail=f"recipe {recipe_id!r} not found",
        )
    return recipe
