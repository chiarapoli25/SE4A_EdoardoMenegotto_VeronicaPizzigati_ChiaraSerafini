"""Endpoint delle simulazioni batch effimere e isolate."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.database import get_db
from ..recipes.repository import get_recipe
from ..zones.models import ZoneAdministrativeStatus
from ..zones.repository import list_zones
from .manager import (
    SimulationBusy,
    SimulationInvalid,
    SimulationMissing,
    SimulationNotReady,
    simulation_manager,
)
from .models import SimulationCreate, SimulationJob, SimulationPreview


router = APIRouter(prefix="/simulations", tags=["simulations"])

# Reparto 5 e' la quarantena (nessuna specie unica, mai simulabile — vedi
# ZoneCreate._validate_department_role); solo i settori produttivi attivi
# con una ricetta assegnata partecipano a "simula l'intera serra".
_QUARANTINE_DEPARTMENT = 5


@router.post("", response_model=SimulationJob, status_code=202)
def create_simulation(
    request: SimulationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> SimulationJob:
    try:
        if request.recipe_id is None:
            # "Simula l'intera serra": ogni settore produttivo attivo con
            # una ricetta assegnata, tutti sullo stesso arco temporale.
            targets = [
                (zone.id, get_recipe(connection, zone.active_recipe_id))
                for zone in list_zones(connection)
                if zone.department_number != _QUARANTINE_DEPARTMENT
                and zone.administrative_status == ZoneAdministrativeStatus.ACTIVE
                and zone.active_recipe_id is not None
            ]
            # get_recipe puo' restituire None solo se il catalogo e il
            # riferimento del settore sono disallineati (mai in condizioni
            # normali, vedi il vincolo di integrita' su active_recipe_id) —
            # quei settori vengono scartati piuttosto che far fallire
            # l'intera simulazione della serra per un singolo riferimento
            # orfano.
            valid_targets = [
                (zone_id, recipe) for zone_id, recipe in targets if recipe is not None
            ]
            return simulation_manager.create_greenhouse(request, valid_targets)
        recipe = get_recipe(connection, request.recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="recipe not found")
        return simulation_manager.create(request, recipe)
    except SimulationBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SimulationInvalid as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
