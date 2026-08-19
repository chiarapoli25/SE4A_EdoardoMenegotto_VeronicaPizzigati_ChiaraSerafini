"""@file routes.py
@brief Endpoint HTTP di login della dashboard.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ...core.config import auth_secret, auth_token_ttl_seconds
from ...core.database import get_db
from ..audit.models import AuditOutcome
from ..audit.repository import record_audit_event
from .dependencies import get_current_user
from .models import LoginRequest, TokenResponse, User
from .repository import authenticate_user
from .security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    credentials: LoginRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> TokenResponse:
    """@brief Verifica le credenziali ed emette un token di accesso.

    @details Restituisce sempre lo stesso messaggio generico sia per uno
    `username` inesistente sia per una password errata, per non rivelare
    quali account esistono. Ogni tentativo (riuscito o no) viene comunque
    registrato nel log di audit (`auth.login`), con lo `username` tentato:
    a differenza della risposta HTTP, il log e' consultabile solo dagli
    `admin` ed e' pensato anche per individuare tentativi ripetuti falliti.

    @throws HTTPException 401 se le credenziali non sono valide o l'account
        e disattivato.
    """
    user = authenticate_user(connection, credentials.username, credentials.password)
    if user is None:
        record_audit_event(
            connection,
            action="auth.login",
            outcome=AuditOutcome.FAILURE,
            actor_username=credentials.username,
            resource_type="user",
            resource_id=credentials.username,
        )
        raise HTTPException(status_code=401, detail="invalid username or password")
    token, expires_at = create_access_token(
        {"sub": user.username, "role": user.role.value},
        auth_secret(),
        auth_token_ttl_seconds(),
    )
    record_audit_event(
        connection,
        action="auth.login",
        outcome=AuditOutcome.SUCCESS,
        actor_username=user.username,
        actor_role=user.role.value,
        resource_type="user",
        resource_id=user.username,
    )
    return TokenResponse(access_token=token, expires_at=expires_at, user=user)


@router.get("/me", response_model=User)
def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """@brief Restituisce l'utente associato al token corrente."""
    return current_user
