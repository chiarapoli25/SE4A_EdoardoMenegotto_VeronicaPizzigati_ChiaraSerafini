"""Endpoint delle simulazioni batch effimere e isolate."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.database import get_db
from ..recipes.repository import get_recipe
from .manager import (
    SimulationBusy,
    SimulationMissing,
    SimulationNotReady,
    simulation_manager,
)
from .models import SimulationCreate, SimulationJob, SimulationPreview


router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("", response_model=SimulationJob, status_code=202)
def create_simulation(
    request: SimulationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> SimulationJob:
    recipe = get_recipe(connection, request.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found")
    try:
        return simulation_manager.create(request, recipe)
    except SimulationBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{run_id}", response_model=SimulationJob)
def read_simulation(run_id: str) -> SimulationJob:
    try:
        return simulation_manager.get(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{run_id}/result", response_model=SimulationPreview)
def read_simulation_result(run_id: str) -> SimulationPreview:
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
