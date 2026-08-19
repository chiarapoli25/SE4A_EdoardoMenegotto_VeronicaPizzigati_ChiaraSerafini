import sqlite3
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.auth.models import UserRole
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


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


# --- auth.login -----------------------------------------------------------


def test_successful_login_is_recorded_in_the_audit_log(
    client: TestClient,
    connection: sqlite3.Connection,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    client.post("/auth/login", json={"username": "admin-1", "password": "Test-password-1"})

    response = client.get("/audit-log", headers=auth_headers(admin_token))

    assert response.status_code == 200
    entries = [entry for entry in response.json() if entry["action"] == "auth.login"]
    assert any(
        entry["outcome"] == "success" and entry["actor_username"] == "admin-1"
        for entry in entries
    )


def test_failed_login_is_recorded_in_the_audit_log(
    client: TestClient,
    connection: sqlite3.Connection,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    client.post("/auth/login", json={"username": "admin-1", "password": "wrong"})

    response = client.get("/audit-log", headers=auth_headers(admin_token))

    entries = [entry for entry in response.json() if entry["action"] == "auth.login"]
    assert any(
        entry["outcome"] == "failure" and entry["actor_username"] == "admin-1"
        for entry in entries
    )


# --- cultivation.* ----------------------------------------------------------


def test_cultivation_create_and_confirm_are_recorded(
    client: TestClient,
    connection: sqlite3.Connection,
    example_recipe_data: dict,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    agronomist_token = issue_token(connection, "agro-1", UserRole.AGRONOMIST)
    agronomist_headers = auth_headers(agronomist_token)
    create_zone(client, auth_headers(admin_token))
    assert client.post(
        "/recipes", json={**example_recipe_data, "version": 1}, headers=agronomist_headers
    ).status_code == 201

    created = client.post(
        "/zones/r1-s1/cultivations",
        json={
            "id": "cult-1",
            "plant_species": "Tomato",
            "recipe_id": "tomato_demo_v1",
            "recipe_version": 1,
        },
        headers=agronomist_headers,
    )
    assert created.status_code == 201
    confirmed = client.post(
        "/zones/r1-s1/cultivations/cult-1/confirm", json={}, headers=agronomist_headers
    )
    assert confirmed.status_code == 200

    response = client.get("/audit-log", headers=auth_headers(admin_token))
    actions = {
        (entry["action"], entry["outcome"], entry["resource_id"])
        for entry in response.json()
    }
    assert ("cultivation.create", "success", "cult-1") in actions
    assert ("cultivation.confirm", "success", "cult-1") in actions


def test_cultivation_transition_failure_is_recorded(
    client: TestClient,
    connection: sqlite3.Connection,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    create_zone(client, auth_headers(admin_token))

    response = client.post(
        "/zones/r1-s1/cultivations/does-not-exist/pause",
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 404

    audit = client.get("/audit-log", headers=auth_headers(admin_token))
    actions = {
        (entry["action"], entry["outcome"], entry["resource_id"])
        for entry in audit.json()
    }
    assert ("cultivation.pause", "failure", "does-not-exist") in actions


# --- zone.* / recipe.* -------------------------------------------------------


def test_zone_register_and_modify_are_recorded(
    client: TestClient,
    connection: sqlite3.Connection,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    admin_headers = auth_headers(admin_token)
    create_zone(client, admin_headers)

    modified = client.patch(
        "/zones/r1-s1", json={"administrative_status": "maintenance"}, headers=admin_headers
    )
    assert modified.status_code == 200

    response = client.get("/audit-log", headers=admin_headers)
    actions = {
        (entry["action"], entry["outcome"], entry["resource_id"])
        for entry in response.json()
    }
    assert ("zone.register", "success", "r1-s1") in actions
    assert ("zone.modify", "success", "r1-s1") in actions


def test_recipe_create_is_recorded(
    client: TestClient,
    connection: sqlite3.Connection,
    example_recipe_data: dict,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    agronomist_token = issue_token(connection, "agro-1", UserRole.AGRONOMIST)

    created = client.post(
        "/recipes",
        json={**example_recipe_data, "version": 1},
        headers=auth_headers(agronomist_token),
    )
    assert created.status_code == 201

    response = client.get("/audit-log", headers=auth_headers(admin_token))
    actions = {
        (entry["action"], entry["outcome"], entry["resource_id"])
        for entry in response.json()
    }
    assert ("recipe.create", "success", "tomato_demo_v1") in actions


# --- Autorizzazione e filtri della rotta di lettura -------------------------


def test_reading_audit_log_requires_admin_role(
    client: TestClient,
    connection: sqlite3.Connection,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    operator_token = issue_token(connection, "op-1", UserRole.OPERATOR)
    agronomist_token = issue_token(connection, "agro-1", UserRole.AGRONOMIST)

    assert client.get(
        "/audit-log", headers=auth_headers(operator_token)
    ).status_code == 403
    assert client.get(
        "/audit-log", headers=auth_headers(agronomist_token)
    ).status_code == 403


def test_reading_audit_log_without_a_token_is_rejected(client: TestClient) -> None:
    response = client.get("/audit-log")

    assert response.status_code == 401


def test_audit_log_can_be_filtered_by_action_and_outcome(
    client: TestClient,
    connection: sqlite3.Connection,
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> None:
    admin_token = issue_token(connection, "admin-1", UserRole.ADMIN)
    client.post("/auth/login", json={"username": "admin-1", "password": "wrong"})
    client.post("/auth/login", json={"username": "admin-1", "password": "Test-password-1"})

    response = client.get(
        "/audit-log",
        params={"action": "auth.login", "outcome": "failure"},
        headers=auth_headers(admin_token),
    )

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) >= 1
    assert all(entry["action"] == "auth.login" for entry in entries)
    assert all(entry["outcome"] == "failure" for entry in entries)
