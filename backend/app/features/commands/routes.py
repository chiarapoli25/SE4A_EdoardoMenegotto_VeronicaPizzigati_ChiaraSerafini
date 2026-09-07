"""Endpoint HTTP della coda comandi Edge."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ...core.database import get_db
from ..users.dependencies import get_current_user
from ..users.models import UserRole
from ..zones.repository import get_zone
from .models import CommandType, RuntimeCommand, RuntimeCommandCreate, RuntimeCommandResultCreate
from .repository import (
    RuntimeCommandConflict,
    complete_command,
    create_command,
    list_pending_commands,
)

router = APIRouter(prefix="/zones/{zone_id}/commands", tags=["commands"])

## @brief command_type per cui il pannello Strategy invia oggi comandi che
## modificano la configurazione di controllo dell'impianto: SOLO questi due
## richiedono il ruolo amministratore. Scelta di scope deliberata — non
## estendere ad altri command_type senza una decisione esplicita.
ADMINISTRATOR_ONLY_COMMAND_TYPES = {
    CommandType.CHANGE_STRATEGY,
    CommandType.CONFIRM_CONFIGURATION,
}


def _require_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")


def _require_administrator_if_strategy_command(
    command_type: CommandType,
    connection: sqlite3.Connection,
    authorization: str | None,
) -> None:
    """@brief Applica require_role('amministratore') solo a ChangeStrategy e
    ConfirmConfiguration; ogni altro command_type su questo stesso endpoint
    (usato da script/demo) resta libero, come da scelta di scope esplicita.
    """
    if command_type not in ADMINISTRATOR_ONLY_COMMAND_TYPES:
        return
    user = get_current_user(authorization=authorization, connection=connection)
    if user.role is not UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=(
                f"il comando {command_type.value!r} e' riservato agli "
                "amministratori"
            ),
        )


@router.post("", response_model=RuntimeCommand, status_code=201)
def enqueue_command(
    zone_id: str,
    command: RuntimeCommandCreate,
    connection: sqlite3.Connection = Depends(get_db),
    authorization: Annotated[str | None, Header()] = None,
) -> RuntimeCommand:
    _require_zone(connection, zone_id)
    _require_administrator_if_strategy_command(
        command.command_type, connection, authorization
    )
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
    return stored
