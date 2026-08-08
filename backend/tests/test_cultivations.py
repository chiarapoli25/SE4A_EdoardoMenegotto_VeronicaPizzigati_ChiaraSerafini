import sqlite3
from datetime import datetime, timezone

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
    assert client.post(
        "/zones",
        json={
            "id": "r1-s1",
            "name": "Tropicali - Settore 1",
            "department_number": 1,
            "sector_number": 1,
            "plant_species": "Calathea",
        },
    ).status_code == 201
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        connection.close()


def create_cultivation(client: TestClient) -> dict:
    response = client.post(
        "/cultivations",
        json={"zone_id": "r1-s1", "recipe_id": "recipe-calathea"},
    )
    assert response.status_code == 201
    return response.json()


def complete_command(client: TestClient, command: dict, status: str = "succeeded") -> None:
    response = client.post(
        f"/zones/r1-s1/commands/{command['command_id']}/result",
        json={"status": status, "message": "test result", "replayed": False},
    )
    assert response.status_code == 200


def telemetry_payload(sequence_number: int, timestamp: float) -> dict:
    return {
        "sequence_number": sequence_number,
        "boot_id": "boot-1",
        "timestamp_seconds": timestamp,
        "recorded_at": "2026-08-07T12:00:00Z",
        "temperature_c": 24.0,
        "air_humidity_percent": 70.0,
        "soil_moisture_percent": 55.0,
        "soil_bulk_ec_ms_cm": 1.5,
        "soil_ec_ms_cm": 1.7,
        "fertilizer_concentration_mg_per_liter": 350.0,
        "nitrogen_estimate_mg_per_liter": 150.0,
        "phosphorus_estimate_mg_per_liter": 50.0,
        "potassium_estimate_mg_per_liter": 200.0,
        "ph": 6.2,
        "light_ppfd_umol_m2_s": 200.0,
        "active_recipe_id": "recipe-calathea",
        "active_recipe_version": 1,
        "current_phase": "Avvio e attecchimento",
        "operational_state": "Nominal",
        "lifecycle_state": "Running",
        "current_strategies": {
            "soil_moisture": "Threshold",
            "light": "Threshold",
            "ph": "PID",
            "nitrogen": "Predictive",
            "phosphorus": "Predictive",
            "potassium": "Predictive",
        },
        "current_setpoints": {
            "soil_moisture": 55.0,
            "light": 200.0,
            "ph": 6.2,
            "nitrogen": 150.0,
            "phosphorus": 50.0,
            "potassium": 200.0,
        },
        "time_scale": 1.0,
    }


def test_create_is_atomic_and_queues_edge_activation(client: TestClient) -> None:
    action = create_cultivation(client)

    assert action["cultivation"]["state"] == "activating"
    assert action["command"]["command_type"] == "ActivateCultivation"
    assert action["command"]["payload"] == {
        "cultivation_id": action["cultivation"]["id"],
        "recipe_id": "recipe-calathea",
    }
    zone = client.get("/zones/r1-s1").json()
    assert zone["active_cultivation_id"] == action["cultivation"]["id"]
    assert zone["active_recipe_id"] == "recipe-calathea"
    assert zone["status"] == "offline"


def test_rejects_incompatible_recipe_and_occupied_zone(client: TestClient) -> None:
    incompatible = client.post(
        "/cultivations",
        json={"zone_id": "r1-s1", "recipe_id": "recipe-orchidea-phalaenopsis"},
    )
    assert incompatible.status_code == 422
    assert client.get("/cultivations").json() == []

    create_cultivation(client)
    occupied = client.post(
        "/cultivations",
        json={"zone_id": "r1-s1", "recipe_id": "recipe-calathea"},
    )
    assert occupied.status_code == 409
    assert len(client.get("/cultivations").json()) == 1


def test_offline_legacy_running_zone_is_realigned_before_activation(
    client: TestClient,
) -> None:
    connection = next(app.dependency_overrides[get_db]())
    connection.execute(
        """
        UPDATE zones
        SET lifecycle_state = 'Running', last_edge_contact = ?
        WHERE id = 'r1-s1'
        """,
        ("2020-01-01T00:00:00+00:00",),
    )
    connection.commit()
    legacy_zone = client.get("/zones/r1-s1").json()
    assert legacy_zone["status"] == "offline"
    assert legacy_zone["lifecycle_state"] == "Running"
    assert legacy_zone["active_cultivation_id"] is None

    created = client.post(
        "/cultivations",
        json={"zone_id": "r1-s1", "recipe_id": "recipe-calathea"},
    )

    assert created.status_code == 201
    assert created.json()["command"]["command_type"] == "ActivateCultivation"
    realigned_zone = client.get("/zones/r1-s1").json()
    assert realigned_zone["lifecycle_state"] == "Idle"
    assert realigned_zone["active_cultivation_id"] == created.json()["cultivation"]["id"]


def test_online_running_zone_without_cycle_is_not_restarted(
    client: TestClient,
) -> None:
    online_snapshot = telemetry_payload(1, 900)
    online_snapshot["recorded_at"] = datetime.now(timezone.utc).isoformat()
    assert client.post("/zones/r1-s1/telemetry", json=online_snapshot).status_code == 201
    assert client.get("/zones/r1-s1").json()["status"] == "online"

    response = client.post(
        "/cultivations",
        json={"zone_id": "r1-s1", "recipe_id": "recipe-calathea"},
    )

    assert response.status_code == 409
    assert "ancora in esecuzione sull'Edge" in response.json()["detail"]
    assert client.get("/cultivations").json() == []


def test_rejected_command_marks_cycle_error(client: TestClient) -> None:
    action = create_cultivation(client)
    complete_command(client, action["command"], "rejected")
    cycle = client.get(f"/cultivations/{action['cultivation']['id']}").json()
    assert cycle["state"] == "error"


def test_pause_and_resume_follow_edge_confirmation(client: TestClient) -> None:
    started = create_cultivation(client)
    cultivation_id = started["cultivation"]["id"]
    complete_command(client, started["command"])

    pausing = client.post(f"/cultivations/{cultivation_id}/pause")
    assert pausing.status_code == 202
    assert pausing.json()["cultivation"]["state"] == "pausing"
    complete_command(client, pausing.json()["command"])
    assert client.get(f"/cultivations/{cultivation_id}").json()["state"] == "paused"

    resuming = client.post(f"/cultivations/{cultivation_id}/resume")
    assert resuming.status_code == 202
    complete_command(client, resuming.json()["command"])
    assert client.get(f"/cultivations/{cultivation_id}").json()["state"] == "running"


def test_terminate_waits_for_edge_then_archives_without_deleting_history(
    client: TestClient,
) -> None:
    started = create_cultivation(client)
    cultivation_id = started["cultivation"]["id"]
    complete_command(client, started["command"])
    stored = client.post(
        "/zones/r1-s1/telemetry",
        json=telemetry_payload(1, 900),
    )
    assert stored.status_code == 201
    assert stored.json()["cultivation_id"] == cultivation_id

    stopping = client.post(f"/cultivations/{cultivation_id}/terminate")
    assert stopping.status_code == 202
    assert stopping.json()["cultivation"]["state"] == "stopping"
    assert client.get("/zones/r1-s1").json()["active_cultivation_id"] == cultivation_id

    complete_command(client, stopping.json()["command"])
    archived = client.get(f"/cultivations/{cultivation_id}").json()
    assert archived["state"] == "archived"
    assert archived["archived_at"] is not None
    assert client.get("/zones/r1-s1").json()["active_cultivation_id"] is None
    history = client.get(
        f"/zones/r1-s1/telemetry?cultivation_id={cultivation_id}"
    ).json()
    assert [sample["sequence_number"] for sample in history] == [1]


def test_history_is_associated_with_the_exact_cycle(client: TestClient) -> None:
    first = create_cultivation(client)
    first_id = first["cultivation"]["id"]
    complete_command(client, first["command"])
    client.post("/zones/r1-s1/telemetry", json=telemetry_payload(1, 900))
    stopping = client.post(f"/cultivations/{first_id}/terminate").json()
    complete_command(client, stopping["command"])

    second = create_cultivation(client)
    second_id = second["cultivation"]["id"]
    client.post("/zones/r1-s1/telemetry", json=telemetry_payload(2, 1800))

    first_history = client.get(
        f"/zones/r1-s1/telemetry?cultivation_id={first_id}"
    ).json()
    second_history = client.get(
        f"/zones/r1-s1/telemetry?cultivation_id={second_id}"
    ).json()
    assert [item["sequence_number"] for item in first_history] == [1]
    assert [item["sequence_number"] for item in second_history] == [2]
