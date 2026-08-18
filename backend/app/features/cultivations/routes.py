"""@file routes.py
@brief Endpoint HTTP del workflow di coltivazione: bozza, conferma, lettura,
pausa e conclusione.

@details Tutte le rotte sono annidate sotto `/zones/{zone_id}/cultivations`,
lo stesso schema gia usato da `features.commands` per la coda comandi: il
settore identifica sempre lo scope della richiesta, quindi non compare nel
corpo di `CultivationCreate` ne viene mai riletto due volte per la stessa
richiesta (le funzioni di transizione della repository verificano da sole
che la coltivazione appartenga a `zone_id`).
"""

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from ...core.database import get_db
from ..zones.repository import get_zone
from .models import (
    Cultivation,
    CultivationConfirm,
    CultivationCreate,
    CultivationProgress,
    CultivationResumeRequest,
    CultivationStopRequest,
)
from .repository import (
    CultivationCompatibilityError,
    CultivationConflict,
    CultivationStateError,
    complete_cultivation,
    confirm_cultivation,
    create_cultivation,
    get_active_cultivation_for_zone,
    get_cultivation,
    list_cultivations,
    pause_cultivation,
    resume_cultivation,
)


## @brief Router del workflow di coltivazione, annidato sotto una zona.
router = APIRouter(prefix="/zones/{zone_id}/cultivations", tags=["cultivations"])


def _require_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    if get_zone(connection, zone_id) is None:
        raise HTTPException(status_code=404, detail=f"zone {zone_id!r} not found")


def _require_cultivation_in_zone(
    connection: sqlite3.Connection, zone_id: str, cultivation_id: str
) -> Cultivation:
    cultivation = get_cultivation(connection, cultivation_id)
    if cultivation is None or cultivation.zone_id != zone_id:
        raise HTTPException(
            status_code=404,
            detail=f"cultivation {cultivation_id!r} not found in zone {zone_id!r}",
        )
    return cultivation


def _run_transition(
    operation: Callable[[], Cultivation | None], cultivation_id: str
) -> Cultivation:
    """@brief Esegue una transizione di stato mappando gli errori di dominio.

    @details Le funzioni di transizione di `repository.py` verificano gia da
    sole l'esistenza della coltivazione e la sua appartenenza a `zone_id`
    (restituendo `None` in entrambi i casi): usare direttamente il loro
    risultato evita una doppia lettura.

    @throws HTTPException 404 se la coltivazione non esiste nella zona
        indicata, 409 se lo stato corrente o la compatibilita non ammettono
        la transizione richiesta.
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
    zone_id: str,
    cultivation: CultivationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Apre una bozza di coltivazione su un settore libero.

    @throws HTTPException 404 se il settore non esiste, 409 se l'id e gia
        usato o il settore ha gia una coltivazione non conclusa.
    """
    _require_zone(connection, zone_id)
    try:
        return create_cultivation(connection, zone_id, cultivation)
    except CultivationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# --- Lettura ---------------------------------------------------------------


@router.get("", response_model=list[Cultivation])
def read_cultivations(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> list[Cultivation]:
    """@brief Elenca lo storico delle coltivazioni del settore.

    @throws HTTPException 404 se il settore non esiste.
    """
    _require_zone(connection, zone_id)
    return list_cultivations(connection, zone_id)


@router.get("/active", response_model=Cultivation)
def read_active_cultivation(
    zone_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Restituisce la coltivazione corrente (non conclusa) del settore.

    @throws HTTPException 404 se il settore non esiste o non ha una
        coltivazione non conclusa in questo momento.
    """
    _require_zone(connection, zone_id)
    active = get_active_cultivation_for_zone(connection, zone_id)
    if active is None:
        raise HTTPException(
            status_code=404,
            detail=f"zone {zone_id!r} has no active cultivation",
        )
    return active


@router.get("/{cultivation_id}", response_model=Cultivation)
def read_cultivation(
    zone_id: str,
    cultivation_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Recupera una coltivazione del settore tramite identificativo."""
    return _require_cultivation_in_zone(connection, zone_id, cultivation_id)


# --- Conferma ------------------------------------------------------------


@router.post("/{cultivation_id}/confirm", response_model=Cultivation)
def confirm(
    zone_id: str,
    cultivation_id: str,
    confirmation: CultivationConfirm,
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Conferma una bozza, fissa la versione della ricetta e accoda
    l'attivazione sulla coda comandi dell'Edge.

    @details Verifica che il settore esista e non abbia gia una coltivazione
    attiva, che la ricetta e la versione esistano, e che specie e substrato
    siano compatibili; salva quindi la coltivazione in stato `starting` e
    accoda un unico comando `ActivateCultivation` idempotente (vedi
    `POST /zones/{zone_id}/commands`). Resta `starting` finche l'Edge non
    riporta l'esito tramite `POST /zones/{zone_id}/commands/{command_id}/result`,
    lo stesso endpoint gia usato per tutti gli altri comandi runtime.

    @throws HTTPException 404 se la coltivazione non esiste nella zona, 409
        se non e in stato bozza o se settore, specie, ricetta o substrato non
        sono compatibili.
    """
    return _run_transition(
        lambda: confirm_cultivation(connection, zone_id, cultivation_id, confirmation),
        cultivation_id,
    )


# --- Pausa -------------------------------------------------------------


@router.post("/{cultivation_id}/pause", response_model=Cultivation)
def pause(
    zone_id: str,
    cultivation_id: str,
    progress: CultivationProgress = CultivationProgress(),
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Sospende una coltivazione attiva senza concluderla.

    @throws HTTPException 404 se non esiste nella zona, 409 se non e in
        stato `active`.
    """
    return _run_transition(
        lambda: pause_cultivation(connection, zone_id, cultivation_id, progress),
        cultivation_id,
    )


@router.post("/{cultivation_id}/resume", response_model=Cultivation)
def resume(
    zone_id: str,
    cultivation_id: str,
    request: CultivationResumeRequest = CultivationResumeRequest(),
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Riprende una coltivazione sospesa.

    @throws HTTPException 404 se non esiste nella zona, 409 se non e in
        stato `paused`.
    """
    return _run_transition(
        lambda: resume_cultivation(
            connection, zone_id, cultivation_id, request.time_scale
        ),
        cultivation_id,
    )


# --- Conclusione -------------------------------------------------------


@router.post("/{cultivation_id}/complete", response_model=Cultivation)
def complete(
    zone_id: str,
    cultivation_id: str,
    request: CultivationStopRequest = CultivationStopRequest(),
    connection: sqlite3.Connection = Depends(get_db),
) -> Cultivation:
    """@brief Conclude regolarmente una coltivazione e libera il settore.

    @throws HTTPException 404 se non esiste nella zona, 409 se non e
        `active` o `paused`.
    """
    return _run_transition(
        lambda: complete_cultivation(
            connection, zone_id, cultivation_id, request, request.reason
        ),
        cultivation_id,
    )
