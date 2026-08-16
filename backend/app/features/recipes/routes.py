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
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Recipe]:
    """Elenca le ricette, eventualmente filtrate per reparto produttivo."""
    recipes = list_recipes(connection)
    if department_number is None:
        return recipes
    if department_number not in range(1, 5):
        raise HTTPException(
            status_code=422,
            detail="department_number must be between 1 and 4",
        )
    return [
        recipe for recipe in recipes
        if recipe.department_number == department_number
    ]


@router.get("/{recipe_id}", response_model=Recipe)
def read_recipe(
    recipe_id: str,
    version: int | None = Query(default=None, ge=1),
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """@brief Recupera una ricetta tramite identificativo.

    @param version Versione esatta richiesta, oppure `None` per l'ultima
        versione disponibile.
    """
    recipe = get_recipe(connection, recipe_id, version)
    if recipe is None:
        raise HTTPException(
            status_code=404,
            detail=f"recipe {recipe_id!r} not found",
        )
    return recipe
