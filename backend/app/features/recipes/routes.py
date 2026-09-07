"""@file routes.py
@brief Endpoint HTTP per validazione e distribuzione delle ricette.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

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
    """Valida e salva integralmente la ricetta in SQLite.

    @details Restituisce la ricetta ricaricata da `get_recipe`, non
    l'oggetto ricevuto in ingresso: da quando Strategy e parametri di
    controllo sono un'impostazione globale (vedi
    ../control_strategy/, repository.py::_stamp_global_strategy), il
    payload inviato dal client per `controllers[*].selected_strategy` e'
    solo un placeholder che soddisfa lo schema — la risposta deve riflettere
    subito lo stesso valore che una `GET` successiva restituirebbe.
    """
    try:
        save_recipe(connection, recipe)
    except RecipeVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    stored = get_recipe(connection, recipe.id)
    assert stored is not None
    return stored


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
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """@brief Recupera una ricetta tramite identificativo."""
    recipe = get_recipe(connection, recipe_id)
    if recipe is None:
        raise HTTPException(
            status_code=404,
            detail=f"recipe {recipe_id!r} not found",
        )
    return recipe
