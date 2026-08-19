import json
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from backend.app.core.config import auth_secret, auth_token_ttl_seconds
from backend.app.features.auth.models import UserCreate, UserRole
from backend.app.features.auth.repository import create_user
from backend.app.features.auth.security import create_access_token
from backend.app.models import Recipe

EXAMPLE_RECIPE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "example_recipe.json"
)


@pytest.fixture()
def example_recipe_data() -> dict:
    return json.loads(EXAMPLE_RECIPE_PATH.read_text())


@pytest.fixture()
def example_recipe(example_recipe_data: dict) -> Recipe:
    return Recipe.model_validate(example_recipe_data)


@pytest.fixture()
def issue_token() -> Callable[[sqlite3.Connection, str, UserRole], str]:
    """@brief Fabbrica di token: crea un utente e restituisce un bearer valido.

    @details Condivisa da tutti i test che devono autenticarsi verso le
    rotte protette da `features.auth` (cultivations, zones, recipes,
    telemetry), senza passare da `POST /auth/login` ogni volta.
    """

    def _issue(connection: sqlite3.Connection, username: str, role: UserRole) -> str:
        user = create_user(
            connection,
            UserCreate(username=username, password="Test-password-1", role=role),
        )
        token, _ = create_access_token(
            {"sub": user.username, "role": user.role.value},
            auth_secret(),
            auth_token_ttl_seconds(),
        )
        return token

    return _issue
