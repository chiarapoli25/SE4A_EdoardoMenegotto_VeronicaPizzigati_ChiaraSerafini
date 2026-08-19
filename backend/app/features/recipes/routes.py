"""@file routes.py
@brief Endpoint HTTP per validazione e distribuzione delle ricette.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.database import get_db
from ..audit.models import AuditOutcome
from ..audit.repository import record_audit_event
from ..auth.dependencies import require_roles
from ..auth.models import CULTIVATION_WRITE_ROLES, User
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
    current_user: Annotated[User, Depends(require_roles(*CULTIVATION_WRITE_ROLES))],
    connection: sqlite3.Connection = Depends(get_db),
) -> Recipe:
    """Valida e salva integralmente la ricetta in SQLite.

    @details Richiede il ruolo `agronomist` o `admin`: pubblicare una nuova
    versione e un'attivita di autoring, non di sola lettura. Le rotte di
    lettura sotto `/recipes` restano invece senza autenticazione utente,
    perche `GET /recipes/{recipe_id}` e usata anche dall'Edge per risolvere
    una ricetta non incorporata per intero nel comando `ActivateCultivation`
    (vedi `http_backend_client.cpp`): un Edge non ha ne puo ottenere un login
    da dashboard.
    """
    try:
        save_recipe(connection, recipe)
    except RecipeVersionConflict as error:
        record_audit_event(
            connection,
            action="recipe.create",
            outcome=AuditOutcome.FAILURE,
            actor_username=current_user.username,
            actor_role=current_user.role.value,
            resource_type="recipe",
            resource_id=recipe.id,
            detail={"version": recipe.version, "reason": str(error)},
        )
        raise HTTPException(status_code=409, detail=str(error)) from error

    record_audit_event(
        connection,
        action="recipe.create",
        outcome=AuditOutcome.SUCCESS,
        actor_username=current_user.username,
        actor_role=current_user.role.value,
        resource_type="recipe",
        resource_id=recipe.id,
        detail={"version": recipe.version},
    )
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
