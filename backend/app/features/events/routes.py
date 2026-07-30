"""Endpoint HTTP per eventi e notifiche dell'Edge."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.database import get_db
from ..zones.repository import get_zone
from .models import EdgeEvent, EdgeEventCreate
from .repository import EdgeEventConflict, list_events, save_event

router = APIRouter(prefix="/zones/{zone_id}/events", tags=["events"])


def _require_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")


@router.post("", response_model=EdgeEvent, status_code=201)
def create_event(
    zone_id: str,
    event: EdgeEventCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> EdgeEvent:
    _require_zone(connection, zone_id)
    try:
        return save_event(connection, zone_id, event)
    except EdgeEventConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[EdgeEvent])
def read_events(
    zone_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[EdgeEvent]:
    _require_zone(connection, zone_id)
    return list_events(connection, zone_id, limit)
