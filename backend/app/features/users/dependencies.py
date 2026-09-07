"""@file dependencies.py
@brief Dipendenze FastAPI per proteggere le rotte che richiedono un account.

@details Distinta da `core.security.require_api_token`: quella verifica il
segreto condiviso fra Edge e backend, questa verifica invece l'identita' di
un utente umano della dashboard tramite un token di sessione.
"""

import sqlite3
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from ...core.database import get_db
from .models import User, UserRole
from .repository import get_session_user


def extract_bearer_token(authorization: str | None) -> str:
    """@brief Isola il token da un header `Authorization: Bearer <token>`.

    @throws HTTPException 401 Se l'header manca o non rispetta lo schema Bearer.
    """
    scheme, separator, token = (authorization or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="missing or invalid session token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    connection: sqlite3.Connection = Depends(get_db),
) -> User:
    """@brief Risolve l'account associato al token di sessione corrente."""
    token = extract_bearer_token(authorization)
    user = get_session_user(connection, token)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="session expired or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    """@brief Richiede che l'account autenticato abbia ruolo amministratore."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="administrator role required")
    return user
