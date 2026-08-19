import sqlite3
from typing import Callable

from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.auth.models import UserRole
from backend.app.main import app, get_db


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_versioned_api_accepts_configured_bearer_token(
    monkeypatch, issue_token: Callable[[sqlite3.Connection, str, UserRole], str]
) -> None:
    """@brief Verifica il token tecnico Edge su una rotta genuinamente Edge.

    @details `GET /api/v1/zones` non e piu un buon endpoint di prova per
    questa verifica: essendo una rotta della dashboard (vedi
    `features.zones.routes.router`), oggi richiede *anche* un login utente,
    che userebbe lo stesso header `Authorization` con un formato diverso
    (JWT anziche il token tecnico condiviso) e i due controlli non possono
    essere entrambi soddisfatti dalla stessa richiesta. `GET
    /api/v1/edges/{edge_id}/zones` (`edge_router`) resta invece gestita solo
    dal token Edge, esattamente come prima: e l'endpoint che gli Edge
    interrogano davvero per sincronizzare il proprio manifesto di settori.
    """
    monkeypatch.setenv("SMARTHYDRO_API_TOKEN", "edge-secret")
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        rejected = client.get("/api/v1/edges/edge-1/zones")
        accepted = client.get(
            "/api/v1/edges/edge-1/zones",
            headers={"Authorization": "Bearer edge-secret"},
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()

    assert rejected.status_code == 401
    assert rejected.headers["www-authenticate"] == "Bearer"
    assert accepted.status_code == 200


def test_legacy_zones_endpoint_now_requires_a_dashboard_login(
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str]
) -> None:
    """@brief `GET /zones` (dashboard) richiede ora un login utente valido.

    @details Prima dell'introduzione di `features.auth` questa rotta era
    liberamente accessibile; il comportamento e cambiato intenzionalmente.
    """
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        without_login = client.get("/zones")
        token = issue_token(connection, "admin-1", UserRole.ADMIN)
        with_login = client.get(
            "/zones", headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()

    assert without_login.status_code == 401
    assert with_login.status_code == 200
