"""Endpoint HTTP di autenticazione della dashboard e relative dependency.

Meccanismo di autenticazione SEPARATO dal Bearer SMARTHYDRO_API_TOKEN usato
dall'Edge (vedi `backend/app/core/security.py`): qui il chiamante e' un
operatore umano che ha fatto login, li' e' un processo Edge configurato con
un token condiviso. I due sistemi non si toccano ne' si riusano a vicenda.
"""

from typing import Annotated

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException

from ...core.database import get_db
from .models import AuthenticatedUser, LoginRequest, LoginResponse, Role
from .repository import authenticate, create_session, resolve_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    credentials: LoginRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> LoginResponse:
    """@brief Verifica username+password e rilascia un token di sessione opaco.

    Nessuna registrazione self-service: gli account sono creati solo da
    `demo/seed_users.py` (o da un amministratore del database).
    """
    user = authenticate(connection, credentials.username, credentials.password)
    if user is None:
        raise HTTPException(status_code=401, detail="username o password non validi")
    token = create_session(connection, user.username)
    return LoginResponse(token=token, username=user.username, role=user.role)


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    connection: sqlite3.Connection = Depends(get_db),
) -> AuthenticatedUser:
    """@brief Dependency FastAPI: risolve l'utente dal token opaco in
    `Authorization: Bearer <token>`.

    @throws HTTPException 401 se l'header manca o il token e' invalido/scaduto.
    """
    scheme, separator, token = (authorization or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="autenticazione richiesta: header 'Authorization: Bearer <token>' mancante",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = resolve_session(connection, token)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="token di sessione non valido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(role: Role):
    """@brief Dependency factory costruita sopra `get_current_user()`.

    Restituisce una dependency FastAPI che richiede l'utente autenticato
    abbia esattamente `role`, altrimenti risponde 403.
    """

    def _dependency(
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> AuthenticatedUser:
        if user.role is not role:
            raise HTTPException(
                status_code=403,
                detail=f"operazione riservata al ruolo {role.value!r}",
            )
        return user

    return _dependency
