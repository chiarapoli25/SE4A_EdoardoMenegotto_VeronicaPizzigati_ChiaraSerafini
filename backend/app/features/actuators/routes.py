"""@file routes.py
@brief Endpoint HTTP per acquisizione e lettura degli attuatori.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from ...core.database import get_db
from ..zones.repository import get_zone
from .models import ActuatorSnapshot, ActuatorSnapshotCreate
from .repository import (
    ActuatorSnapshotConflict,
    get_latest_actuator_snapshot,
    list_actuator_snapshots,
    save_actuator_snapshot,
)


## @brief Router degli attuatori associati alle zone.
router = APIRouter(prefix="/zones/{zone_id}/actuators", tags=["actuators"])


def _require_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    """@brief Verifica che una zona esista.

    @throws HTTPException Se la zona non e registrata.
    """
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")


@router.post("", response_model=ActuatorSnapshot, status_code=201)
def create_actuator_snapshot(
    zone_id: str,
    snapshot: ActuatorSnapshotCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> ActuatorSnapshot:
    """@brief Riceve e salva comando e uscita fisica inviati dall'Edge."""
    _require_zone(connection, zone_id)
    try:
        return save_actuator_snapshot(connection, zone_id, snapshot)
    except ActuatorSnapshotConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/latest", response_model=ActuatorSnapshot)
def read_latest_actuator_snapshot(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> ActuatorSnapshot:
    """@brief Restituisce l'ultimo stato disponibile degli attuatori."""
    _require_zone(connection, zone_id)
    snapshot = get_latest_actuator_snapshot(connection, zone_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"actuator snapshot for zone {zone_id!r} not found",
        )
    return snapshot


@router.get("", response_model=list[ActuatorSnapshot])
def read_actuator_history(
    zone_id: str,
    recorded_from: AwareDatetime | None = Query(default=None, alias="from"),
    recorded_to: AwareDatetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[ActuatorSnapshot]:
    """@brief Restituisce lo storico filtrabile degli attuatori."""
    _require_zone(connection, zone_id)
    if (
        recorded_from is not None
        and recorded_to is not None
        and recorded_from > recorded_to
    ):
        raise HTTPException(status_code=400, detail="'from' must not be after 'to'")
    return list_actuator_snapshots(
        connection,
        zone_id,
        recorded_from=recorded_from,
        recorded_to=recorded_to,
        limit=limit,
    )
