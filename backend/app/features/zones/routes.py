"""@file routes.py
@brief Endpoint HTTP per reparti e settori della serra.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ...core.database import get_db
from ..recipes.repository import get_recipe
from .models import Zone, ZoneCreate, ZoneUpdate
from .repository import (
    ZoneConflict,
    ZoneUpdateConflict,
    ZoneUpdateInvalid,
    create_zone,
    get_zone,
    list_zones,
    list_zones_for_edge,
    update_zone,
)


## @brief Router delle zone fisiche della serra.
router = APIRouter(prefix="/zones", tags=["zones"])
edge_router = APIRouter(prefix="/edges", tags=["edges"])


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
    if (
        zone.active_recipe_id is not None
        and get_recipe(connection, zone.active_recipe_id) is None
    ):
        raise HTTPException(
            status_code=404,
            detail=f"recipe {zone.active_recipe_id!r} not found",
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

    if (
        "active_recipe_id" in update.model_fields_set
        and update.active_recipe_id is not None
        and get_recipe(connection, update.active_recipe_id) is None
    ):
        raise HTTPException(
            status_code=404,
            detail=f"recipe {update.active_recipe_id!r} not found",
        )

    try:
        return update_zone(connection, zone, update)
    except ZoneUpdateInvalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ZoneUpdateConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
