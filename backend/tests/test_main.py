import sqlite3

from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.main import app, get_db


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_versioned_api_accepts_configured_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("SMARTHYDRO_API_TOKEN", "edge-secret")
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        rejected = client.get("/api/v1/zones")
        accepted = client.get(
            "/api/v1/zones",
            headers={"Authorization": "Bearer edge-secret"},
        )
        legacy = client.get("/zones")
    finally:
        app.dependency_overrides.clear()
        connection.close()

    assert rejected.status_code == 401
    assert rejected.headers["www-authenticate"] == "Bearer"
    assert accepted.status_code == 200
    assert legacy.status_code == 200
