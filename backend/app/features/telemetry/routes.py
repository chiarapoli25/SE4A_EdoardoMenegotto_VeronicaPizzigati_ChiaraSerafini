"""@file routes.py
@brief Endpoint HTTP per acquisizione e lettura dei sensori.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from ...core.database import get_db
from ..zones.repository import get_zone
from .models import TelemetryCreate, TelemetrySample
from .repository import (
    TelemetryConflict,
    get_latest_telemetry,
    list_telemetry,
    save_telemetry,
)


## @brief Router della telemetria associata alle zone.
router = APIRouter(prefix="/zones/{zone_id}/telemetry", tags=["telemetry"])


def _require_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    """@brief Verifica che una zona esista.

    @throws HTTPException Se la zona non e registrata.
    """
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")


@router.post("", response_model=TelemetrySample, status_code=201)
def create_telemetry(
    zone_id: str,
    telemetry: TelemetryCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> TelemetrySample:
    """@brief Riceve e salva un campione inviato dall'Edge."""
    _require_zone(connection, zone_id)
    try:
        return save_telemetry(connection, zone_id, telemetry)
    except TelemetryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/latest", response_model=TelemetrySample)
def read_latest_telemetry(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> TelemetrySample:
    """@brief Restituisce l'ultima misura disponibile per una zona."""
    _require_zone(connection, zone_id)
    telemetry = get_latest_telemetry(connection, zone_id)
    if telemetry is None:
        raise HTTPException(
            status_code=404,
            detail=f"telemetry for zone {zone_id!r} not found",
        )
    return telemetry


@router.get("", response_model=list[TelemetrySample])
def read_telemetry_history(
    zone_id: str,
    recorded_from: AwareDatetime | None = Query(default=None, alias="from"),
    recorded_to: AwareDatetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[TelemetrySample]:
    """@brief Restituisce lo storico filtrabile dei sensori."""
    _require_zone(connection, zone_id)
    if (
        recorded_from is not None
        and recorded_to is not None
        and recorded_from > recorded_to
    ):
        raise HTTPException(status_code=400, detail="'from' must not be after 'to'")
    return list_telemetry(
        connection,
        zone_id,
        recorded_from=recorded_from,
        recorded_to=recorded_to,
        limit=limit,
    )
