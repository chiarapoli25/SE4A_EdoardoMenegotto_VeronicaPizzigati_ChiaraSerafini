"""@file dependencies.py
@brief Dipendenze FastAPI per autenticare e autorizzare la dashboard.
"""

import sqlite3
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from ...core.config import auth_secret
from ...core.database import get_db
from .models import User, UserRole
from .repository import get_user_by_username
from .security import TokenError, decode_access_token

## @brief Risposta uniforme per ogni credenziale mancante, invalida o scaduta.
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    connection: sqlite3.Connection = Depends(get_db),
) -> User:
    """@brief Risolve l'utente autenticato dal token `Authorization: Bearer`.

    @details Rilegge sempre l'utente dal database (non si fida soltanto del
    claim nel token): un ruolo cambiato o un account disattivato dopo
    l'emissione del token ha effetto immediato, senza bisogno di revocare i
    token gia emessi.

    @throws HTTPException 401 se l'header manca, il token e malformato,
        scaduto, firmato con una chiave diversa, o non corrisponde piu a un
        utente attivo.
    """
    scheme, separator, token = (authorization or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="missing or malformed bearer token",
            headers=_UNAUTHORIZED_HEADERS,
        )
    try:
        claims = decode_access_token(token, auth_secret())
    except TokenError as error:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired token",
            headers=_UNAUTHORIZED_HEADERS,
        ) from error
    username = claims.get("sub")
    if not isinstance(username, str) or not username:
        raise HTTPException(
            status_code=401,
            detail="invalid token payload",
            headers=_UNAUTHORIZED_HEADERS,
        )
    user = get_user_by_username(connection, username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="user not found or disabled",
            headers=_UNAUTHORIZED_HEADERS,
        )
    return user


def require_roles(*roles: UserRole):
    """@brief Costruisce una dipendenza che ammette solo i ruoli indicati.

    @details Da usare come `Depends(require_roles(UserRole.AGRONOMIST, ...))`
    sugli endpoint che, oltre a richiedere un login valido, limitano
    l'operazione a specifici ruoli (es. aprire o confermare una
    coltivazione).
    """
    allowed = frozenset(roles)

    def _check_role(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"role {current_user.role.value!r} is not allowed to "
                    "perform this operation"
                ),
            )
        return current_user

    return _check_role
