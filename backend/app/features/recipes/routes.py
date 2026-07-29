"""@file routes.py
@brief Endpoint HTTP per validazione e distribuzione delle ricette.
"""

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ...core.database import get_db
from .export import (
    DEFAULT_EXPORT_DIRECTORY,
    UnsafeRecipeId,
    assert_safe_recipe_id,
    export_recipe_for_edge,
)
from .models import Recipe
from .repository import (
    RecipeVersionConflict,
    get_recipe,
    save_recipe,
)


## @brief Router delle ricette versionate.
router = APIRouter(prefix="/recipes", tags=["recipes"])


def get_export_directory() -> Path:
    """@brief Restituisce la directory dei JSON destinati all'Edge."""
    return DEFAULT_EXPORT_DIRECTORY


@router.post("", response_model=Recipe, status_code=201)
def create_recipe(
    recipe: Recipe,
    connection: sqlite3.Connection = Depends(get_db),
    export_directory: Path = Depends(get_export_directory),
) -> Recipe:
    """@brief Valida, salva ed esporta una ricetta."""
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
