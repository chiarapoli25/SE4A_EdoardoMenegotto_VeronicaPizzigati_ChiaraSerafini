"""Gate amministratore-only su ChangeStrategy/ConfirmConfiguration.

Copre solo l'autorizzazione dei command_type riservati (vedi
ADMINISTRATOR_ONLY_COMMAND_TYPES in backend/app/features/commands/routes.py),
contro il sistema di account in backend/app/features/users/. Login/logout/me
e la gestione degli account (creazione, conflitti, permessi su POST/GET
/users) sono gia' coperti a fondo da backend/tests/test_users.py — non
duplicati qui.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.users.models import UserRole
from backend.app.features.users.repository import create_user
from backend.app.main import app, get_db


@pytest.fixture()
def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(conn)
    # Account creati esplicitamente qui (non tramite seed_default_users):
    # questo file non deve dipendere dagli account dimostrativi admin/
    # agronomo, solo dal ruolo.
    create_user(conn, username="admin-1", password="admin-secret", role=UserRole.ADMIN, display_name=None)
    create_user(conn, username="agro-1", password="agro-secret", role=UserRole.AGRONOMO, display_name=None)
    return conn


@pytest.fixture()
def client(connection: sqlite3.Connection) -> TestClient:
    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.post(
        "/zones",
        json={
            "id": "zone-1",
            "name": "Zona 1",
            "department_number": 1,
            "sector_number": 1,
            "plant_species": "Pomodoro",
        },
    )
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_change_strategy_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/zones/zone-1/commands",
        json={"command_id": "c1", "command_type": "ChangeStrategy", "payload": {}},
    )
    assert response.status_code == 401


def test_change_strategy_rejects_agronomo(client: TestClient) -> None:
    token = _login(client, "agro-1", "agro-secret")
    response = client.post(
        "/zones/zone-1/commands",
        json={"command_id": "c2", "command_type": "ChangeStrategy", "payload": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_confirm_configuration_rejects_agronomo(client: TestClient) -> None:
    token = _login(client, "agro-1", "agro-secret")
    response = client.post(
        "/zones/zone-1/commands",
        json={"command_id": "c3", "command_type": "ConfirmConfiguration", "payload": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_change_strategy_accepts_admin(client: TestClient) -> None:
    token = _login(client, "admin-1", "admin-secret")
    response = client.post(
        "/zones/zone-1/commands",
        json={"command_id": "c4", "command_type": "ChangeStrategy", "payload": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "pending"


def test_change_strategy_rejects_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/zones/zone-1/commands",
        json={"command_id": "c5", "command_type": "ChangeStrategy", "payload": {}},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "command_type,payload",
    [
        ("SetSimulationSpeed", {"time_scale": 60}),
        ("InjectFault", {"fault_type": "sensor_dropout", "target_variable": "soil_moisture"}),
        ("ResetFault", {}),
        ("ResetEmergency", {}),
        ("AdvanceRecipePhase", {}),
    ],
)
def test_other_command_types_stay_unprotected(
    client: TestClient, command_type: str, payload: dict
) -> None:
    """Scelta di scope deliberata: SOLO ChangeStrategy/ConfirmConfiguration
    richiedono il ruolo amministratore. Ogni altro command_type sullo stesso
    endpoint resta accessibile senza autenticazione, come da specifica."""
    response = client.post(
        "/zones/zone-1/commands",
        json={
            "command_id": f"unprotected-{command_type}",
            "command_type": command_type,
            "payload": payload,
        },
    )
    assert response.status_code == 201


def test_change_strategy_rejects_malformed_authorization_header(client: TestClient) -> None:
    response = client.post(
        "/zones/zone-1/commands",
        json={"command_id": "c6", "command_type": "ChangeStrategy", "payload": {}},
        headers={"Authorization": "not-bearer-scheme"},
    )
    assert response.status_code == 401
