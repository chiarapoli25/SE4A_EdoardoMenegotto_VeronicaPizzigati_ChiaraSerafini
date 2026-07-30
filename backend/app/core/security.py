"""Autenticazione opzionale delle API usate dagli Edge."""

import os
import secrets
from typing import Annotated

from fastapi import Header, HTTPException


def require_api_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Verifica il Bearer token quando è configurato sul backend."""
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
