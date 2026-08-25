"""@file routes.py
@brief Endpoint HTTP per reparti e settori della serra.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.config import default_edge_id
from ...core.database import get_db
from ..recipes.models import Recipe
from ..recipes.repository import get_recipe
from .models import Zone, ZoneCreate, ZoneUpdate
from .repository import (
    ZoneConflict,
    ZoneDeletionConflict,
    ZoneUpdateConflict,
    ZoneUpdateInvalid,
    create_zone,
    delete_zone,
    get_zone,
    list_zones,
    list_zones_for_edge,
    update_zone,
)


## @brief Router delle zone fisiche della serra.
router = APIRouter(prefix="/zones", tags=["zones"])
edge_router = APIRouter(prefix="/edges", tags=["edges"])


def _assert_recipe_matches_zone(
    recipe: Recipe,
    department_number: int,
    plant_species: str | None,
) -> None:
    """Impedisce di assegnare una ricetta catalogata al reparto sbagliato."""
    if (
        recipe.department_number is not None
        and recipe.department_number != department_number
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"recipe {recipe.id!r} belongs to department "
                f"{recipe.department_number}, not {department_number}"
            ),
        )
    if (
        recipe.department_number is not None
        and recipe.plant_type != plant_species
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"recipe {recipe.id!r} is for {recipe.plant_type!r}, "
                f"not {plant_species!r}"
            ),
        )


@router.post("", response_model=Zone, status_code=201)
def register_zone(
    zone: ZoneCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Zone:
    """@brief Registra uno dei settori fisici della serra.

    @param zone Identita, posizione e specie del settore.
    @param connection Connessione SQLite associata alla richiesta.
    @return Zona creata con stato iniziale `offline`.
    @throws HTTPException Se id o posizione sono gia occupati.
    """
    if zone.department_number < 5 and zone.assigned_edge_id is None:
        zone = zone.model_copy(update={"assigned_edge_id": default_edge_id()})
    if zone.active_recipe_id is not None:
        recipe = get_recipe(connection, zone.active_recipe_id)
        if recipe is None:
            raise HTTPException(
                status_code=404,
                detail=f"recipe {zone.active_recipe_id!r} not found",
            )
        _assert_recipe_matches_zone(
            recipe,
            zone.department_number,
            zone.plant_species,
        )
    try:
        return create_zone(connection, zone)
    except ZoneConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[Zone])
def read_zones(
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Zone]:
    """@brief Elenca i settori ordinati per reparto e numero."""
    return list_zones(connection)


@edge_router.get("/{edge_id}/zones", response_model=list[Zone])
def read_edge_zones(
    edge_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Zone]:
    """@brief Restituisce il manifesto dei settori assegnati all'Edge."""
    return list_zones_for_edge(connection, edge_id)


@router.get("/{zone_id}", response_model=Zone)
def read_zone(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Zone:
    """@brief Recupera un settore tramite identificativo."""
    zone = get_zone(connection, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")
    return zone


@router.patch("/{zone_id}", response_model=Zone)
def modify_zone(
    zone_id: str,
    update: ZoneUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Zone:
    """@brief Modifica i dati configurabili di un settore esistente."""
    zone = get_zone(connection, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")

    candidate_recipe_id = (
        update.active_recipe_id
        if "active_recipe_id" in update.model_fields_set
        else zone.active_recipe_id
    )
    candidate_species = (
        update.plant_species
        if "plant_species" in update.model_fields_set
        else zone.plant_species
    )
    if candidate_recipe_id is not None:
        recipe = get_recipe(connection, candidate_recipe_id)
        if recipe is None:
            raise HTTPException(
                status_code=404,
                detail=f"recipe {candidate_recipe_id!r} not found",
            )
        _assert_recipe_matches_zone(
            recipe,
            zone.department_number,
            candidate_species,
        )

    try:
        return update_zone(connection, zone, update)
    except ZoneUpdateInvalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ZoneUpdateConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{zone_id}", status_code=204)
def remove_zone(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Response:
    """@brief Cancella un settore inattivo e il relativo storico derivato.

    @param zone_id Identificativo del settore da rimuovere.
    @param connection Connessione SQLite associata alla richiesta.
    @return Nessun contenuto in caso di cancellazione avvenuta.
    @throws HTTPException 404 Se il settore non esiste.
    @throws HTTPException 409 Se il settore ha una coltivazione attiva o
        contiene ancora piante da spostare altrove.
    """
    zone = get_zone(connection, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")
    try:
        delete_zone(connection, zone)
    except ZoneDeletionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(status_code=204)
