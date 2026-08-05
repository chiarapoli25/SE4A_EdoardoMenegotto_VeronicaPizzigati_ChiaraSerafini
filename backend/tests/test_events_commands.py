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
    assert client.get("/zones/zone-1").json()["operational_state"] == "Degraded"


def test_operational_events_update_projection_without_history_rebuild(
    client: TestClient,
) -> None:
    base = {
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "timestamp_seconds": 60,
        "recorded_at": "2026-07-30T12:00:00Z",
    }
    events = (
        (
            "lifecycle-paused",
            "ZoneLifecycleChanged",
            {
                "previous_state": "Running",
                "current_state": "Paused",
                "reason": "operator request",
            },
        ),
        (
            "speed-changed",
            "SimulationSpeedChanged",
            {"previous_time_scale": 1.0, "current_time_scale": 10.0},
        ),
        (
            "strategy-changed",
            "StrategyChanged",
            {
                "variable": "soil_moisture",
                "previous_strategy": "Threshold",
                "current_strategy": "PID",
            },
        ),
    )
    for event_id, event_type, payload in events:
        response = client.post(
            "/api/v1/zones/zone-1/events",
            json={
                **base,
                "event_id": event_id,
                "event_type": event_type,
                "payload": payload,
            },
        )
        assert response.status_code == 201

    zone = client.get("/zones/zone-1").json()
    assert zone["lifecycle_state"] == "Paused"
    assert zone["time_scale"] == 10.0
    assert zone["current_strategies"]["soil_moisture"] == "PID"


def test_recipe_events_update_phase_and_completion_state(
    client: TestClient,
) -> None:
    base_event = {
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "timestamp_seconds": 3600,
        "recorded_at": "2026-07-30T12:00:00Z",
    }
    phase_changed = client.post(
        "/api/v1/zones/zone-1/events",
        json={
            **base_event,
            "event_id": "phase-1",
            "event_type": "RecipePhaseChanged",
            "payload": {
                "previous_phase": "Avvio e attecchimento",
                "current_phase": "Crescita vegetativa",
            },
        },
    )
    running_zone = client.get("/zones/zone-1").json()
    completed = client.post(
        "/api/v1/zones/zone-1/events",
        json={
            **base_event,
            "event_id": "completed-1",
            "event_type": "RecipeCompleted",
            "payload": {
                "recipe_id": "recipe-calathea",
                "final_phase": "Riposo vegetativo",
                "total_duration_hours": 5016,
            },
        },
    )
    ready_zone = client.get("/zones/zone-1").json()

    assert phase_changed.status_code == 201
    assert running_zone["current_phase"] == "Crescita vegetativa"
    assert running_zone["cultivation_completed"] is False
    assert completed.status_code == 201
    assert ready_zone["current_phase"] == "Riposo vegetativo"
    assert ready_zone["cultivation_completed"] is True


@pytest.mark.parametrize(
    "command_type", ["ActivateCultivation", "LoadRecipe"]
)
def test_new_recipe_command_clears_previous_completion_after_success(
    client: TestClient,
    command_type: str,
) -> None:
    completed_event = {
        "event_id": f"completed-before-{command_type}",
        "edge_id": "edge-1",
        "boot_id": "boot-1",
        "event_type": "RecipeCompleted",
        "timestamp_seconds": 3600,
        "recorded_at": "2026-07-30T12:00:00Z",
        "payload": {
            "recipe_id": "recipe-old",
            "final_phase": "Riposo vegetativo",
            "total_duration_hours": 5016,
        },
    }
    assert client.post(
        "/api/v1/zones/zone-1/events", json=completed_event
    ).status_code == 201
    command_id = f"new-recipe-{command_type}"
    created = client.post(
        "/api/v1/zones/zone-1/commands",
        json={
            "command_id": command_id,
            "command_type": command_type,
            "payload": {"recipe_id": "recipe-new"},
        },
    )
    completed = client.post(
        f"/api/v1/zones/zone-1/commands/{command_id}/result",
        json={
            "status": "succeeded",
            "message": "recipe activated",
            "replayed": False,
        },
    )
    zone = client.get("/zones/zone-1").json()

    assert created.status_code == 201
    assert completed.status_code == 200
    assert zone["active_recipe_id"] == "recipe-new"
    assert zone["current_phase"] is None
    assert zone["cultivation_completed"] is False


def test_cultivation_start_event_clears_previous_completion(
    client: TestClient,
) -> None:
    for event_id, event_type, payload in (
        (
            "completed-before-start",
            "RecipeCompleted",
            {
                "recipe_id": "recipe-old",
                "final_phase": "Riposo vegetativo",
                "total_duration_hours": 5016,
            },
        ),
        (
            "new-cultivation-started",
            "ZoneLifecycleChanged",
            {
                "previous_state": "Starting",
                "current_state": "Running",
                "reason": "cultivation started",
            },
        ),
    ):
        response = client.post(
            "/api/v1/zones/zone-1/events",
            json={
                "event_id": event_id,
                "edge_id": "edge-1",
                "boot_id": "boot-1",
                "event_type": event_type,
                "timestamp_seconds": 3600,
                "recorded_at": "2026-07-30T12:00:00Z",
                "payload": payload,
            },
        )
        assert response.status_code == 201

    zone = client.get("/zones/zone-1").json()
    assert zone["current_phase"] is None
    assert zone["cultivation_completed"] is False


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


def test_can_enqueue_cultivation_activation(client: TestClient) -> None:
    response = client.post(
        "/api/v1/zones/zone-1/commands",
        json={
            "command_id": "activate-1",
            "command_type": "ActivateCultivation",
            "payload": {
                "cultivation_id": "cultivation-1",
                "recipe_id": "tomato_demo_v1",
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert response.json()["command_type"] == "ActivateCultivation"


@pytest.mark.parametrize(
    "command_type",
    [
        "PauseCultivation",
        "ResumeCultivation",
        "StopCultivation",
        "SetSimulationSpeed",
        "SetSimulationDuration",
    ],
)
def test_can_enqueue_zone_lifecycle_commands(
    client: TestClient,
    command_type: str,
) -> None:
    response = client.post(
        "/api/v1/zones/zone-1/commands",
        json={
            "command_id": f"{command_type}-1",
            "command_type": command_type,
            "payload": {},
        },
    )

    assert response.status_code == 201
    assert response.json()["command_type"] == command_type
