import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.main import app, get_db


@pytest.fixture()
def client() -> TestClient:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _login(client: TestClient, username: str, password: str = "pass123") -> str:
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_seeded_admin_can_login(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "pass123"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert body["token"]


def test_seeded_agronomo_can_login(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "agronomo", "password": "pass123"}
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "agronomo"


def test_login_rejects_wrong_password(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "wrong"}
    )

    assert response.status_code == 401


def test_login_rejects_unknown_user(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "ghost", "password": "pass123"}
    )

    assert response.status_code == 401


def test_me_requires_a_token(client: TestClient) -> None:
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_me_returns_the_logged_in_account(client: TestClient) -> None:
    token = _login(client, "admin")

    response = client.get("/auth/me", headers=_auth_headers(token))

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_logout_invalidates_the_token(client: TestClient) -> None:
    token = _login(client, "admin")

    logout_response = client.post("/auth/logout", headers=_auth_headers(token))
    assert logout_response.status_code == 204

    me_response = client.get("/auth/me", headers=_auth_headers(token))
    assert me_response.status_code == 401


def test_admin_can_create_an_agronomo_account(client: TestClient) -> None:
    token = _login(client, "admin")

    response = client.post(
        "/users",
        headers=_auth_headers(token),
        json={
            "username": "nuovo-agronomo",
            "password": "pass123",
            "role": "agronomo",
            "display_name": "Nuovo Agronomo",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "nuovo-agronomo"
    assert body["role"] == "agronomo"
    assert "password" not in body

    login_response = client.post(
        "/auth/login",
        json={"username": "nuovo-agronomo", "password": "pass123"},
    )
    assert login_response.status_code == 200


def test_admin_can_create_another_admin_account(client: TestClient) -> None:
    token = _login(client, "admin")

    response = client.post(
        "/users",
        headers=_auth_headers(token),
        json={
            "username": "secondo-admin",
            "password": "pass123",
            "role": "admin",
        },
    )

    assert response.status_code == 201
    assert response.json()["role"] == "admin"
    # Senza display_name esplicito, l'username resta un'etichetta leggibile.
    assert response.json()["display_name"] == "secondo-admin"


def test_agronomo_cannot_create_users(client: TestClient) -> None:
    token = _login(client, "agronomo")

    response = client.post(
        "/users",
        headers=_auth_headers(token),
        json={"username": "altro", "password": "pass123", "role": "agronomo"},
    )

    assert response.status_code == 403


def test_creating_a_user_requires_a_token(client: TestClient) -> None:
    response = client.post(
        "/users",
        json={"username": "altro", "password": "pass123", "role": "agronomo"},
    )

    assert response.status_code == 401


def test_duplicate_username_is_rejected(client: TestClient) -> None:
    token = _login(client, "admin")

    response = client.post(
        "/users",
        headers=_auth_headers(token),
        json={"username": "admin", "password": "pass123", "role": "agronomo"},
    )

    assert response.status_code == 409


def test_admin_can_list_existing_accounts(client: TestClient) -> None:
    token = _login(client, "admin")

    response = client.get("/users", headers=_auth_headers(token))

    assert response.status_code == 200
    usernames = {user["username"] for user in response.json()}
    assert {"admin", "agronomo"} <= usernames


def test_agronomo_cannot_list_accounts(client: TestClient) -> None:
    token = _login(client, "agronomo")

    response = client.get("/users", headers=_auth_headers(token))

    assert response.status_code == 403
