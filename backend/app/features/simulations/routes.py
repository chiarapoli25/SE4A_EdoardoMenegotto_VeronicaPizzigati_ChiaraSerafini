"""Endpoint delle simulazioni batch effimere e isolate."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.database import get_db
from ..recipes.models import Recipe
from ..recipes.repository import get_recipe
from ..zones.models import ZoneAdministrativeStatus
from ..zones.repository import list_zones
from .live_manager import live_simulation_manager
from .manager import (
    SimulationBusy,
    SimulationInvalid,
    SimulationMissing,
    SimulationNotReady,
    simulation_manager,
)
from .models import (
    LiveSimulationControl,
    LiveSimulationCreate,
    LiveSimulationJob,
    SimulationCreate,
    SimulationJob,
    SimulationPreview,
)


router = APIRouter(prefix="/simulations", tags=["simulations"])

# Reparto 5 e' la quarantena (nessuna specie unica, mai simulabile — vedi
# ZoneCreate._validate_department_role); solo i settori produttivi attivi
# con una ricetta assegnata partecipano a "simula l'intera serra" (batch e
# live).
_QUARANTINE_DEPARTMENT = 5


def _greenhouse_targets(connection: sqlite3.Connection) -> list[tuple[str, Recipe]]:
    """Ogni settore produttivo attivo con una ricetta assegnata — stessa
    selezione per il batch e per la riproduzione live, vedi commento sopra
    _QUARANTINE_DEPARTMENT. get_recipe puo' restituire None solo se il
    catalogo e il riferimento del settore sono disallineati (mai in
    condizioni normali): quei settori vengono scartati piuttosto che far
    fallire l'intera simulazione per un singolo riferimento orfano."""
    targets = [
        (zone.id, get_recipe(connection, zone.active_recipe_id))
        for zone in list_zones(connection)
        if zone.department_number != _QUARANTINE_DEPARTMENT
        and zone.administrative_status == ZoneAdministrativeStatus.ACTIVE
        and zone.active_recipe_id is not None
    ]
    return [(zone_id, recipe) for zone_id, recipe in targets if recipe is not None]


@router.post("", response_model=SimulationJob, status_code=202)
def create_simulation(
    request: SimulationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> SimulationJob:
    try:
        if request.recipe_id is None:
            # "Simula l'intera serra": ogni settore produttivo attivo con
            # una ricetta assegnata, tutti sullo stesso arco temporale.
            return simulation_manager.create_greenhouse(request, _greenhouse_targets(connection))
        recipe = get_recipe(connection, request.recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="recipe not found")
        return simulation_manager.create(request, recipe)
    except SimulationBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SimulationInvalid as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# --- Riproduzione live dell'intera serra --------------------------------
#
# Route dedicate sotto /simulations/live/..., dichiarate PRIMA di
# /{run_id} qui sotto cosi' un run_id live (mai letterale "live") non ha
# alcuna ambiguita' di matching con le route batch generiche.


@router.post("/live", response_model=LiveSimulationJob, status_code=202)
def create_live_simulation(
    request: LiveSimulationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> LiveSimulationJob:
    try:
        return live_simulation_manager.create_greenhouse(request, _greenhouse_targets(connection))
    except SimulationBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SimulationInvalid as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/live/{run_id}", response_model=LiveSimulationJob)
def read_live_simulation(run_id: str) -> LiveSimulationJob:
    try:
        return live_simulation_manager.get(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/live/{run_id}/result", response_model=list[SimulationPreview])
def read_live_simulation_result(run_id: str) -> list[SimulationPreview]:
    try:
        return live_simulation_manager.result(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/live/{run_id}/control", response_model=LiveSimulationJob)
def control_live_simulation(run_id: str, request: LiveSimulationControl) -> LiveSimulationJob:
    try:
        return live_simulation_manager.control(run_id, request.action, request.speed_multiplier)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/live/{run_id}", status_code=204)
def delete_live_simulation(run_id: str) -> Response:
    try:
        live_simulation_manager.cancel_or_discard(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)


@router.get("/{run_id}", response_model=SimulationJob)
def read_simulation(run_id: str) -> SimulationJob:
    try:
        return simulation_manager.get(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{run_id}/result", response_model=SimulationPreview | list[SimulationPreview])
def read_simulation_result(run_id: str) -> SimulationPreview | list[SimulationPreview]:
    try:
        return simulation_manager.result(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{run_id}", status_code=204)
def delete_simulation(run_id: str) -> Response:
    try:
        simulation_manager.cancel_or_discard(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)
