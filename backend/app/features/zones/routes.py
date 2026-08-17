"""@file routes.py
@brief Endpoint HTTP per reparti e settori della serra.
"""

import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ...core.database import get_db
from ..commands.models import CommandType, RuntimeCommand, RuntimeCommandCreate
from ..commands.repository import RuntimeCommandConflict, create_command
from ..recipes.models import Recipe
from ..recipes.repository import get_recipe
from .models import SimulationSpeedRequest, Zone, ZoneCreate, ZoneRuntime, ZoneUpdate
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


@router.patch(
    "/{zone_id}/simulation-speed", response_model=RuntimeCommand, status_code=202
)
def set_simulation_speed(
    zone_id: str,
    request: SimulationSpeedRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> RuntimeCommand:
    """@brief Imposta la velocita di simulazione richiesta dalla dashboard.

    @details Come `ActivateCultivation`, non scrive subito `time_scale`
    sulla zona: accoda un comando `SetSimulationSpeed` sulla coda dell'Edge
    (`POST /zones/{zone_id}/commands`) e restituisce il comando accodato
    (`202 Accepted`, non ancora applicato). La zona riflette la nuova
    velocita solo quando l'Edge riporta l'esito tramite
    `POST /zones/{zone_id}/commands/{command_id}/result`.

    @throws HTTPException 404 se la zona non esiste.
    """
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")
    command_id = f"{zone_id}-simulation-speed-{uuid.uuid4().hex[:8]}"
    try:
        return create_command(
            connection,
            zone_id,
            RuntimeCommandCreate(
                command_id=command_id,
                command_type=CommandType.SET_SIMULATION_SPEED,
                payload={"time_scale": request.time_scale},
            ),
        )
    except RuntimeCommandConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{zone_id}/runtime", response_model=ZoneRuntime)
def read_zone_runtime(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> ZoneRuntime:
    """@brief Espone stato, fase, tempi e velocita correnti del settore.

    @throws HTTPException 404 se la zona non esiste.
    """
    zone = get_zone(connection, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")
    return ZoneRuntime(
        zone_id=zone.id,
        lifecycle_state=zone.lifecycle_state,
        operational_state=zone.operational_state,
        current_phase=zone.current_phase,
        active_recipe_id=zone.active_recipe_id,
        active_recipe_version=zone.active_recipe_version,
        current_strategies=zone.current_strategies,
        current_setpoints=zone.current_setpoints,
        time_scale=zone.time_scale,
        last_edge_contact=zone.last_edge_contact,
    )
