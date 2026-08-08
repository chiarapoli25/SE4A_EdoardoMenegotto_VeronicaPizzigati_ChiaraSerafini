"""Endpoint agronomici per creare, controllare e archiviare coltivazioni."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.database import get_db
from .models import Cultivation, CultivationAction, CultivationCreate
from .repository import (
    CultivationConflict,
    CultivationInvalid,
    create_and_activate,
    get_cultivation,
    list_cultivations,
    pause_cultivation,
    resume_cultivation,
    terminate_cultivation,
)


router = APIRouter(prefix="/cultivations", tags=["cultivations"])


def _translate(operation):
    try:
        return operation()
    except CultivationInvalid as error:
        status = 404 if "not found" in str(error) else 422
        raise HTTPException(status_code=status, detail=str(error)) from error
    except CultivationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("", response_model=CultivationAction, status_code=201)
def start_cultivation(
    request: CultivationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> CultivationAction:
    return _translate(lambda: create_and_activate(connection, request))


@router.get("", response_model=list[Cultivation])
def read_cultivations(
    zone_id: str | None = None,
    include_archived: bool = Query(default=True),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Cultivation]:
    return list_cultivations(
        connection,
        zone_id=zone_id,
        include_archived=include_archived,
    )


@router.get("/{cultivation_id}", response_model=Cultivation)
def read_cultivation(
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    cultivation = get_cultivation(connection, cultivation_id)
    if cultivation is None:
        raise HTTPException(status_code=404, detail="cultivation not found")
    return cultivation


@router.post("/{cultivation_id}/pause", response_model=CultivationAction, status_code=202)
def pause(
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> CultivationAction:
    return _translate(lambda: pause_cultivation(connection, cultivation_id))


@router.post("/{cultivation_id}/resume", response_model=CultivationAction, status_code=202)
def resume(
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> CultivationAction:
    return _translate(lambda: resume_cultivation(connection, cultivation_id))


@router.post("/{cultivation_id}/terminate", response_model=CultivationAction, status_code=202)
def terminate(
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> CultivationAction:
    return _translate(lambda: terminate_cultivation(connection, cultivation_id))
