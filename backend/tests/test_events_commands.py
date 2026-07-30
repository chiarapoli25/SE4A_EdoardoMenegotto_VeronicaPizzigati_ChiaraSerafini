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
    client = TestClient(app)
    client.post(
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
        yield client
    finally:
        app.dependency_overrides.clear()
        connection.close()


def test_event_is_idempotent_and_available_on_versioned_api(
    client: TestClient,
) -> None:
    payload = {
        "event_id": "event-1",
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "event_type": "StateChanged",
        "timestamp_seconds": 60,
        "recorded_at": "2026-07-30T12:00:00Z",
        "payload": {"current_state": "Degraded"},
    }

    first = client.post("/api/v1/zones/zone-1/events", json=payload)
    replay = client.post("/api/v1/zones/zone-1/events", json=payload)
    events = client.get("/api/v1/zones/zone-1/events")

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert [event["event_id"] for event in events.json()] == ["event-1"]


def test_recipe_phase_event_updates_zone_projection(client: TestClient) -> None:
    payload = {
        "event_id": "phase-event-1",
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "event_type": "RecipePhaseChanged",
        "timestamp_seconds": 3600,
        "recorded_at": "2026-07-30T12:30:00Z",
        "payload": {
            "previous_phase": "Germinazione",
            "current_phase": "Crescita vegetativa",
        },
    }

    first = client.post("/api/v1/zones/zone-1/events", json=payload)
    replay = client.post("/api/v1/zones/zone-1/events", json=payload)
    zone = client.get("/zones/zone-1")

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert zone.json()["current_phase"] == "Crescita vegetativa"


def test_non_phase_event_does_not_overwrite_current_phase(
    client: TestClient,
) -> None:
    phase_event = {
        "event_id": "phase-event-2",
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "event_type": "RecipePhaseChanged",
        "timestamp_seconds": 3600,
        "recorded_at": "2026-07-30T12:30:00Z",
        "payload": {
            "previous_phase": "Germinazione",
            "current_phase": "Fioritura",
        },
    }
    state_event = {
        "event_id": "state-event-2",
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "event_type": "StateChanged",
        "timestamp_seconds": 3660,
        "recorded_at": "2026-07-30T12:31:00Z",
        "payload": {
            "previous_state": "Nominal",
            "current_state": "Degraded",
        },
    }

    assert client.post("/api/v1/zones/zone-1/events", json=phase_event).status_code == 201
    assert client.post("/api/v1/zones/zone-1/events", json=state_event).status_code == 201

    zone = client.get("/zones/zone-1")
    assert zone.json()["current_phase"] == "Fioritura"


def test_command_round_trip_is_idempotent(client: TestClient) -> None:
    command = {
        "command_id": "command-1",
        "command_type": "EmergencyStop",
        "payload": {"reason": "test"},
    }
    created = client.post("/api/v1/zones/zone-1/commands", json=command)
    pending = client.get("/api/v1/zones/zone-1/commands")
    result_payload = {
        "status": "succeeded",
        "message": "emergency stop applied",
        "replayed": False,
    }
    result = client.post(
        "/api/v1/zones/zone-1/commands/command-1/result",
        json=result_payload,
    )
    replay = client.post(
        "/api/v1/zones/zone-1/commands/command-1/result",
        json=result_payload,
    )

    assert created.status_code == 201
    assert [item["command_id"] for item in pending.json()] == ["command-1"]
    assert result.status_code == 200
    assert result.json()["status"] == "succeeded"
    assert replay.json() == result.json()
    assert client.get("/api/v1/zones/zone-1/commands").json() == []
