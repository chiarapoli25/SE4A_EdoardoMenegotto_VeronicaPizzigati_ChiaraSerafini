"""Endpoint HTTP della coda comandi Edge."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.database import get_db
from ..cultivations.repository import apply_activation_command_result
from ..zones.repository import get_zone
from .models import CommandType, RuntimeCommand, RuntimeCommandCreate, RuntimeCommandResultCreate
from .repository import (
    RuntimeCommandConflict,
    complete_command,
    create_command,
    list_pending_commands,
)

router = APIRouter(prefix="/zones/{zone_id}/commands", tags=["commands"])


def _require_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")


@router.post("", response_model=RuntimeCommand, status_code=201)
def enqueue_command(
    zone_id: str,
    command: RuntimeCommandCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> RuntimeCommand:
    _require_zone(connection, zone_id)
    try:
        return create_command(connection, zone_id, command)
    except RuntimeCommandConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[RuntimeCommand])
def read_pending_commands(
    zone_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[RuntimeCommand]:
    _require_zone(connection, zone_id)
    return list_pending_commands(connection, zone_id, limit)


@router.post("/{command_id}/result", response_model=RuntimeCommand)
def report_command_result(
    zone_id: str,
    command_id: str,
    result: RuntimeCommandResultCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> RuntimeCommand:
    _require_zone(connection, zone_id)
    try:
        stored = complete_command(connection, zone_id, command_id, result)
    except RuntimeCommandConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if stored is None:
        raise HTTPException(status_code=404, detail=f"command {command_id!r} not found")
    if stored.command_type is CommandType.ACTIVATE_CULTIVATION:
        # Le coltivazioni non hanno un endpoint di esito proprio: riusano
        # questo stesso report per far avanzare cultivations.status.
        apply_activation_command_result(
            connection, command_id, stored.status, stored.result_message,
            stored.result,
        )
    return stored
