"""@file
@brief Autenticazione opzionale delle API usate dagli Edge.
"""

import os
import secrets
from typing import Annotated

from fastapi import Header, HTTPException


def require_api_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """@brief Verifica il Bearer token quando è configurato sul backend.

    @details Senza `SMARTHYDRO_API_TOKEN` nell'ambiente l'endpoint resta
    aperto (uso locale/demo); se il token è impostato, ogni richiesta deve
    portare `Authorization: Bearer <token>` a match esatto e a tempo
    costante (`secrets.compare_digest`).

    @throws HTTPException 401 se l'header manca, ha schema diverso da
        Bearer o il token non coincide.
    """
    expected = os.environ.get("SMARTHYDRO_API_TOKEN")
    if not expected:
        return

    scheme, separator, token = (authorization or "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(token, expected)
    ):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
