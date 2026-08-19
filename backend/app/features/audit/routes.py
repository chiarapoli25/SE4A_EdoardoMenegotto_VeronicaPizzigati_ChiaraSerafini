"""@file routes.py
@brief Endpoint HTTP di sola lettura del log di audit.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...core.database import get_db
from ..auth.dependencies import require_roles
from ..auth.models import User, UserRole
from .models import AuditLogEntry, AuditOutcome
from .repository import list_audit_events

## @brief Router del log di audit, riservato agli amministratori.
router = APIRouter(prefix="/audit-log", tags=["audit"])


@router.get("", response_model=list[AuditLogEntry])
def read_audit_log(
    current_user: Annotated[User, Depends(require_roles(UserRole.ADMIN))],
    actor_username: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    outcome: AuditOutcome | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[AuditLogEntry]:
    """@brief Elenca le voci di audit, piu recenti prima.

    @details Richiede il ruolo `admin`: il log puo contenere dettagli
    operativi (motivi di rifiuto, identificativi di settori/coltivazioni)
    non destinati a tutti gli utenti autenticati.
    """
    del current_user
    return list_audit_events(
        connection,
        actor_username=actor_username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        limit=limit,
    )
