"""Endpoint HTTP dell'anagrafica piante e della quarantena."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ...core.database import get_db
from ..zones.repository import get_zone
from .models import Plant, PlantCreate, PlantMovement, PlantQuarantineUpdate
from .repository import (
    PlantConflict,
    create_plant,
    delete_plant,
    get_plant,
    list_plant_movements,
    list_plants,
    set_quarantine_state,
)


router = APIRouter(prefix="/plants", tags=["plants"])


@router.post("", response_model=Plant, status_code=201)
def register_plant(
    plant: PlantCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Plant:
    """Registra una pianta nel relativo settore produttivo di origine."""
    home_zone = get_zone(connection, plant.home_zone_id)
    if home_zone is None:
        raise HTTPException(
            status_code=404,
            detail=f"home zone {plant.home_zone_id!r} not found",
        )
    if home_zone.department_number == 5:
        raise HTTPException(
            status_code=400,
            detail="a plant home zone must be a cultivation zone",
        )
    if home_zone.plant_species != plant.species:
        raise HTTPException(
            status_code=400,
            detail="plant species must match the home zone species",
        )
    try:
        return create_plant(connection, plant)
    except PlantConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[Plant])
def read_plants(
    zone_id: str | None = None,
    is_quarantined: bool | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Plant]:
    """Elenca le piante filtrandole per posizione o flag di quarantena."""
    return list_plants(
        connection,
        zone_id=zone_id,
        is_quarantined=is_quarantined,
        limit=limit,
    )


@router.get("/{plant_id}", response_model=Plant)
def read_plant(
    plant_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Plant:
    plant = get_plant(connection, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail=f"plant {plant_id!r} not found")
    return plant


@router.patch("/{plant_id}/quarantine", response_model=Plant)
def update_plant_quarantine(
    plant_id: str,
    update: PlantQuarantineUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Plant:
    """Imposta il flag e sposta la pianta in quarantena o nel reparto origine."""
    plant = get_plant(connection, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail=f"plant {plant_id!r} not found")

    if update.is_quarantined:
        assert update.quarantine_zone_id is not None
        destination = get_zone(connection, update.quarantine_zone_id)
        if destination is None:
            raise HTTPException(
                status_code=404,
                detail=f"zone {update.quarantine_zone_id!r} not found",
            )
        if destination.department_number != 5:
            raise HTTPException(
                status_code=400,
                detail="a quarantined plant must be moved to department 5",
            )
        destination_zone_id = destination.id
        reason = update.reason
    else:
        home_zone = get_zone(connection, plant.home_zone_id)
        if home_zone is None:
            raise HTTPException(
                status_code=409,
                detail=f"home zone {plant.home_zone_id!r} no longer exists",
            )
        destination_zone_id = home_zone.id
        reason = update.reason

    return set_quarantine_state(
        connection,
        plant,
        is_quarantined=update.is_quarantined,
        destination_zone_id=destination_zone_id,
        reason=reason,
    )


@router.get("/{plant_id}/movements", response_model=list[PlantMovement])
def read_plant_movements(
    plant_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[PlantMovement]:
    if get_plant(connection, plant_id) is None:
        raise HTTPException(status_code=404, detail=f"plant {plant_id!r} not found")
    return list_plant_movements(connection, plant_id, limit)


@router.delete("/{plant_id}", status_code=204)
def remove_plant(
    plant_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Response:
    """@brief Cancella definitivamente una pianta e il suo storico spostamenti.

    @details Nessuna restrizione di stato: a differenza di un settore, una
    pianta non ha un "processo attivo" legato a se' che renda pericolosa
    una cancellazione immediata, quindi e' cancellabile sia normale sia in
    quarantena. Lo storico in `plant_movements` viene cancellato insieme
    alla pianta (cascade), perche' senza la pianta quei record non hanno
    piu' un soggetto a cui riferirsi.
    """
    plant = get_plant(connection, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail=f"plant {plant_id!r} not found")
    delete_plant(connection, plant)
    return Response(status_code=204)
