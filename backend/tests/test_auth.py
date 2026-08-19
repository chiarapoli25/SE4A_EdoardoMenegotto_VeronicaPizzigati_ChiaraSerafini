import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import auth_secret
from backend.app.database import init_db
from backend.app.features.auth.models import UserCreate, UserRole
from backend.app.features.auth.repository import create_user
from backend.app.features.auth.security import create_access_token, hash_password, verify_password
from backend.app.main import app, get_db


@pytest.fixture()
def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def client(connection: sqlite3.Connection) -> TestClient:
    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def create_zone(client: TestClient, headers: dict, zone_id: str = "r1-s1") -> None:
    response = client.post(
        "/zones",
        json={
            "id": zone_id,
            "name": "Reparto 1 - Settore 1",
            "department_number": 1,
            "sector_number": 1,
            "plant_species": "Tomato",
        },
        headers=headers,
    )
    assert response.status_code == 201


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def admin_headers(connection: sqlite3.Connection, username: str = "bootstrap-admin") -> dict:
    """Crea (se serve) un admin e restituisce i suoi header di autenticazione.

    @details Usata quando un test deve registrare un settore
    (`POST /zones` richiede il ruolo `admin`) prima di esercitare un ruolo
    diverso su un'altra rotta.
    """
    user = create_user(
        connection,
        UserCreate(username=username, password="password123", role=UserRole.ADMIN),
    )
    token, _ = create_access_token(
        {"sub": user.username, "role": user.role.value}, auth_secret(), 3600
    )
    return auth_headers(token)


# --- Hashing password -------------------------------------------------------


def test_password_hash_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_hash_is_salted_differently_each_time() -> None:
    first = hash_password("same-password")
    second = hash_password("same-password")

    assert first != second
    assert verify_password("same-password", first)
    assert verify_password("same-password", second)


def test_verify_password_rejects_malformed_stored_hash() -> None:
    assert not verify_password("anything", "not-a-valid-hash")


# --- Login --------------------------------------------------------------


def test_login_succeeds_with_correct_credentials(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    create_user(
        connection,
        UserCreate(username="mario.rossi", password="agronomo-123", role=UserRole.AGRONOMIST),
    )

    response = client.post(
        "/auth/login", json={"username": "mario.rossi", "password": "agronomo-123"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "mario.rossi"
    assert body["user"]["role"] == "agronomist"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


def test_login_rejects_wrong_password(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    create_user(
        connection,
        UserCreate(username="mario.rossi", password="agronomo-123", role=UserRole.AGRONOMIST),
    )

    response = client.post(
        "/auth/login", json={"username": "mario.rossi", "password": "wrong"}
    )

    assert response.status_code == 401


def test_login_rejects_unknown_username(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "does-not-exist", "password": "whatever"}
    )

    assert response.status_code == 401


def test_login_rejects_disabled_account(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    create_user(
        connection,
        UserCreate(username="disabled-user", password="password123", role=UserRole.OPERATOR),
    )
    connection.execute(
        "UPDATE users SET is_active = 0 WHERE username = 'disabled-user'"
    )
    connection.commit()

    response = client.post(
        "/auth/login", json={"username": "disabled-user", "password": "password123"}
    )

    assert response.status_code == 401


def test_me_returns_the_authenticated_user(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    create_user(
        connection,
        UserCreate(username="admin-1", password="password123", role=UserRole.ADMIN),
    )
    token = client.post(
        "/auth/login", json={"username": "admin-1", "password": "password123"}
    ).json()["access_token"]

    response = client.get("/auth/me", headers=auth_headers(token))

    assert response.status_code == 200
    assert response.json()["username"] == "admin-1"
    assert response.json()["role"] == "admin"


# --- Token invalido/mancante --------------------------------------------


def test_protected_endpoint_without_token_is_rejected(client: TestClient) -> None:
    response = client.get("/zones/r1-s1/cultivations")

    assert response.status_code == 401


def test_protected_endpoint_with_malformed_header_is_rejected(
    client: TestClient,
) -> None:
    response = client.get(
        "/zones/r1-s1/cultivations", headers={"Authorization": "not-a-bearer-token"}
    )

    assert response.status_code == 401


def test_protected_endpoint_with_garbage_token_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/zones/r1-s1/cultivations", headers=auth_headers("this.is.garbage")
    )

    assert response.status_code == 401


def test_protected_endpoint_with_expired_token_is_rejected(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    user = create_user(
        connection,
        UserCreate(username="soon-expired", password="password123", role=UserRole.AGRONOMIST),
    )
    token, _ = create_access_token(
        {"sub": user.username, "role": user.role.value}, auth_secret(), expires_in_seconds=1
    )
    time.sleep(1.2)

    response = client.get("/zones/r1-s1/cultivations", headers=auth_headers(token))

    assert response.status_code == 401


def test_protected_endpoint_with_wrong_signature_is_rejected(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    user = create_user(
        connection,
        UserCreate(username="forged", password="password123", role=UserRole.AGRONOMIST),
    )
    token, _ = create_access_token(
        {"sub": user.username, "role": user.role.value}, "a-different-secret", 3600
    )

    response = client.get("/zones/r1-s1/cultivations", headers=auth_headers(token))

    assert response.status_code == 401


def test_token_for_since_deleted_user_is_rejected(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    token, _ = create_access_token(
        {"sub": "ghost-user", "role": "agronomist"}, auth_secret(), 3600
    )

    response = client.get("/zones/r1-s1/cultivations", headers=auth_headers(token))

    assert response.status_code == 401


# --- Ruoli sulle rotte di coltivazione -----------------------------------


def test_operator_cannot_open_a_cultivation_draft(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    operator = create_user(
        connection,
        UserCreate(username="op-1", password="password123", role=UserRole.OPERATOR),
    )
    create_zone(client, admin_headers(connection))
    operator_token, _ = create_access_token(
        {"sub": operator.username, "role": operator.role.value}, auth_secret(), 3600
    )

    response = client.post(
        "/zones/r1-s1/cultivations",
        json={
            "id": "cult-1",
            "plant_species": "Tomato",
            "recipe_id": "tomato_demo_v1",
            "recipe_version": 1,
        },
        headers=auth_headers(operator_token),
    )

    assert response.status_code == 403


def test_operator_can_pause_an_active_cultivation(
    client: TestClient, connection: sqlite3.Connection, example_recipe_data: dict
) -> None:
    agronomist = create_user(
        connection,
        UserCreate(username="agro-1", password="password123", role=UserRole.AGRONOMIST),
    )
    operator = create_user(
        connection,
        UserCreate(username="op-2", password="password123", role=UserRole.OPERATOR),
    )
    agronomist_token, _ = create_access_token(
        {"sub": agronomist.username, "role": agronomist.role.value}, auth_secret(), 3600
    )
    operator_token, _ = create_access_token(
        {"sub": operator.username, "role": operator.role.value}, auth_secret(), 3600
    )
    agronomist_headers = auth_headers(agronomist_token)
    create_zone(client, admin_headers(connection))
    assert client.post(
        "/recipes", json={**example_recipe_data, "version": 1}, headers=agronomist_headers
    ).status_code == 201
    assert client.post(
        "/zones/r1-s1/cultivations",
        json={
            "id": "cult-1",
            "plant_species": "Tomato",
            "recipe_id": "tomato_demo_v1",
            "recipe_version": 1,
        },
        headers=agronomist_headers,
    ).status_code == 201
    confirmed = client.post(
        "/zones/r1-s1/cultivations/cult-1/confirm", json={}, headers=agronomist_headers
    )
    assert confirmed.status_code == 200
    command_id = confirmed.json()["activation_command_id"]
    assert client.post(
        f"/zones/r1-s1/commands/{command_id}/result",
        json={"status": "succeeded", "message": "ok", "replayed": False},
        headers=agronomist_headers,
    ).status_code == 200

    response = client.post(
        "/zones/r1-s1/cultivations/cult-1/pause",
        headers=auth_headers(operator_token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paused"


# --- created_by/confirmed_by ricavati dall'utente autenticato -----------


def test_created_by_and_confirmed_by_come_from_the_authenticated_user(
    client: TestClient, connection: sqlite3.Connection, example_recipe_data: dict
) -> None:
    creator = create_user(
        connection,
        UserCreate(username="creator", password="password123", role=UserRole.AGRONOMIST),
    )
    confirmer = create_user(
        connection,
        UserCreate(username="confirmer", password="password123", role=UserRole.ADMIN),
    )
    creator_token, _ = create_access_token(
        {"sub": creator.username, "role": creator.role.value}, auth_secret(), 3600
    )
    confirmer_token, _ = create_access_token(
        {"sub": confirmer.username, "role": confirmer.role.value}, auth_secret(), 3600
    )
    creator_headers = auth_headers(creator_token)
    create_zone(client, admin_headers(connection))
    assert client.post(
        "/recipes", json={**example_recipe_data, "version": 1}, headers=creator_headers
    ).status_code == 201

    created = client.post(
        "/zones/r1-s1/cultivations",
        json={
            "id": "cult-1",
            "plant_species": "Tomato",
            "recipe_id": "tomato_demo_v1",
            "recipe_version": 1,
            # Un client malevolo prova a falsificare l'autore: deve essere
            # ignorato in favore dell'utente autenticato.
            "created_by": "someone-else",
        },
        headers=creator_headers,
    )
    assert created.status_code == 201
    assert created.json()["created_by"] == "creator"

    confirmed = client.post(
        "/zones/r1-s1/cultivations/cult-1/confirm",
        json={},
        headers=auth_headers(confirmer_token),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_by"] == "confirmer"

    stored = client.get(
        "/zones/r1-s1/cultivations/cult-1", headers=creator_headers
    ).json()
    assert stored["created_by"] == "creator"
    assert stored["confirmed_by"] == "confirmer"
