"""@file routes.py
@brief Endpoint HTTP del workflow di coltivazione: bozza, conferma, lettura,
pausa e conclusione.
"""

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.database import get_db
from ..zones.repository import get_zone
from .models import (
    Cultivation,
    CultivationConfirm,
    CultivationCreate,
    CultivationProgress,
)
from .repository import (
    CultivationCompatibilityError,
    CultivationConflict,
    CultivationStateError,
    complete_cultivation,
    confirm_cultivation,
    create_cultivation,
    get_cultivation,
    list_cultivations,
    pause_cultivation,
    resume_cultivation,
)


## @brief Router del workflow di coltivazione.
router = APIRouter(prefix="/cultivations", tags=["cultivations"])


def _require_cultivation(
    connection: sqlite3.Connection, cultivation_id: str
) -> Cultivation:
    cultivation = get_cultivation(connection, cultivation_id)
    if cultivation is None:
        raise HTTPException(
            status_code=404,
            detail=f"cultivation {cultivation_id!r} not found",
        )
    return cultivation


def _run_transition(
    operation: Callable[[], Cultivation | None], cultivation_id: str
) -> Cultivation:
    """@brief Esegue una transizione di stato mappando gli errori di dominio.

    @details Le funzioni di transizione di `repository.py` verificano gia da
    sole l'esistenza della coltivazione (restituendo `None` se assente): usare
    direttamente il loro risultato evita la doppia lettura che si avrebbe
    interrogando prima `_require_cultivation` e poi la funzione stessa.

    @throws HTTPException 404 se la coltivazione non esiste, 409 se lo stato
        corrente o la compatibilita non ammettono la transizione richiesta.
    """
    try:
        result = operation()
    except (CultivationStateError, CultivationCompatibilityError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"cultivation {cultivation_id!r} not found",
        )
    return result


# --- Bozza -------------------------------------------------------------


@router.post("", response_model=Cultivation, status_code=201)
def open_cultivation(
    cultivation: CultivationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Apre una bozza di coltivazione su un settore libero.

    @throws HTTPException 404 se il settore non esiste, 409 se l'id e gia
        usato o il settore ha gia una coltivazione non conclusa.
    """
    if get_zone(connection, cultivation.zone_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"zone {cultivation.zone_id!r} not found",
        )
    try:
        return create_cultivation(connection, cultivation)
    except CultivationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# --- Conferma ------------------------------------------------------------


@router.post("/{cultivation_id}/confirm", response_model=Cultivation)
def confirm(
    cultivation_id: str,
    confirmation: CultivationConfirm,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Conferma una bozza, fissa la versione della ricetta e accoda
    l'attivazione sulla coda comandi dell'Edge.

    @details Non attiva subito la coltivazione: accoda un comando
    `ActivateCultivation` per il settore (vedi `POST /zones/{zone_id}/commands`)
    e resta in stato `confirmed` finche l'Edge non riporta l'esito tramite
    `POST /zones/{zone_id}/commands/{command_id}/result`, lo stesso endpoint
    gia usato per tutti gli altri comandi runtime.

    @throws HTTPException 404 se la coltivazione non esiste, 409 se non e in
        stato bozza o se settore, specie, ricetta o substrato non sono
        compatibili.
    """
    return _run_transition(
        lambda: confirm_cultivation(connection, cultivation_id, confirmation),
        cultivation_id,
    )


# --- Lettura ---------------------------------------------------------------


@router.get("", response_model=list[Cultivation])
def read_cultivations(
    zone_id: str | None = Query(default=None),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Cultivation]:
    """@brief Elenca le coltivazioni, opzionalmente filtrate per settore."""
    return list_cultivations(connection, zone_id)


@router.get("/{cultivation_id}", response_model=Cultivation)
def read_cultivation(
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Recupera una coltivazione tramite identificativo."""
    return _require_cultivation(connection, cultivation_id)


# --- Pausa -------------------------------------------------------------


@router.post("/{cultivation_id}/pause", response_model=Cultivation)
def pause(
    cultivation_id: str,
    progress: CultivationProgress = CultivationProgress(),
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Sospende una coltivazione attiva senza concluderla.

    @throws HTTPException 404 se non esiste, 409 se non e in stato `active`.
    """
    return _run_transition(
        lambda: pause_cultivation(connection, cultivation_id, progress),
        cultivation_id,
    )


@router.post("/{cultivation_id}/resume", response_model=Cultivation)
def resume(
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Riprende una coltivazione sospesa.

    @throws HTTPException 404 se non esiste, 409 se non e in stato `paused`.
    """
    return _run_transition(
        lambda: resume_cultivation(connection, cultivation_id),
        cultivation_id,
    )


# --- Conclusione -------------------------------------------------------


@router.post("/{cultivation_id}/complete", response_model=Cultivation)
def complete(
    cultivation_id: str,
    progress: CultivationProgress = CultivationProgress(),
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Conclude regolarmente una coltivazione e libera il settore.

    @throws HTTPException 404 se non esiste, 409 se non e `active` o `paused`.
    """
    return _run_transition(
        lambda: complete_cultivation(connection, cultivation_id, progress),
        cultivation_id,
    )
