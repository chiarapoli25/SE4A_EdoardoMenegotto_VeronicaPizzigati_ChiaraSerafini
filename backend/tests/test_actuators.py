import sqlite3
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.auth.models import UserRole
from backend.app.main import app, get_db


@pytest.fixture()
def client(
    issue_token: Callable[[sqlite3.Connection, str, UserRole], str],
) -> TestClient:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        token = issue_token(connection, "admin-1", UserRole.ADMIN)
        test_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        zone_response = test_client.post(
            "/zones",
            json={
                "id": "r1-s1",
                "name": "Reparto 1 - Settore 1",
                "department_number": 1,
                "sector_number": 1,
                "plant_species": "Pomodoro",
            },
        )
        assert zone_response.status_code == 201
        yield test_client
    finally:
        app.dependency_overrides.clear()
        connection.close()


def valve_states(
    nitrogen: bool = False,
    phosphorus: bool = False,
    potassium: bool = False,
    ph_up: bool = False,
    ph_down: bool = False,
) -> dict:
    return {
        "nitrogen": nitrogen,
        "phosphorus": phosphorus,
        "potassium": potassium,
        "ph-up": ph_up,
        "ph-down": ph_down,
    }


def quantities(value: float = 0.0) -> dict:
    return {
        "nitrogen": value,
        "phosphorus": value,
        "potassium": value,
        "ph-up": value,
        "ph-down": value,
    }


def actuator_payload(
    sequence_number: int = 1,
    recorded_at: str = "2026-07-29T10:00:00Z",
) -> dict:
    return {
        "sequence_number": sequence_number,
        "timestamp_seconds": sequence_number * 15.0,
        "recorded_at": recorded_at,
        "command": {
            "requested_irrigation_volume_liters": 1.0,
            "fertilizer_valves_open": valve_states(nitrogen=True),
            "lighting_percent": 60.0,
        },
        "output": {
            "water_pump_on": True,
            "water_pump_flow_liters_per_hour": 2.0,
            "irrigation_volume_liters_last_step": 0.25,
            "water_pump_on_time_seconds_last_step": 450.0,
            "remaining_irrigation_volume_liters": 0.75,
            "fertilizer_valves_open": valve_states(nitrogen=True),
            "fertilizer_flow_milliliters_per_hour": {
                **quantities(),
                "nitrogen": 20.0,
            },
            "fertilizer_volume_milliliters_last_step": {
                **quantities(),
                "nitrogen": 2.5,
            },
            "lighting_power_watts": 120.0,
        },
    }


def test_ingest_and_read_latest_actuator_snapshot(client: TestClient) -> None:
    created = client.post("/zones/r1-s1/actuators", json=actuator_payload())

    assert created.status_code == 201
    body = created.json()
    assert body["snapshot_id"] == 1
    assert body["zone_id"] == "r1-s1"
    assert body["command"]["requested_irrigation_volume_liters"] == 1.0
    assert body["output"]["irrigation_volume_liters_last_step"] == 0.25
    assert body["output"]["remaining_irrigation_volume_liters"] == 0.75
    assert body["command"]["fertilizer_valves_open"]["ph-up"] is False

    latest = client.get("/zones/r1-s1/actuators/latest")
    assert latest.status_code == 200
    assert latest.json() == body


def test_actuator_snapshot_marks_zone_online(client: TestClient) -> None:
    response = client.post("/zones/r1-s1/actuators", json=actuator_payload())
    assert response.status_code == 201

    zone = client.get("/zones/r1-s1").json()
    assert zone["status"] == "online"
    assert zone["last_edge_contact"] == response.json()["received_at"]


def test_duplicate_identical_actuator_sequence_is_replayed(client: TestClient) -> None:
    first = client.post("/zones/r1-s1/actuators", json=actuator_payload())
    assert first.status_code == 201

    duplicate = client.post("/zones/r1-s1/actuators", json=actuator_payload())

    assert duplicate.status_code == 201
    assert duplicate.json() == first.json()


def test_actuator_history_filters_and_orders_results(client: TestClient) -> None:
    snapshots = [
        actuator_payload(1, "2026-07-29T10:00:00Z"),
        actuator_payload(2, "2026-07-29T10:05:00Z"),
        actuator_payload(3, "2026-07-29T10:10:00Z"),
    ]
    for snapshot in snapshots:
        response = client.post("/zones/r1-s1/actuators", json=snapshot)
        assert response.status_code == 201

    response = client.get(
        "/zones/r1-s1/actuators",
        params={
            "from": "2026-07-29T10:04:00Z",
            "to": "2026-07-29T10:11:00Z",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    assert [item["sequence_number"] for item in response.json()] == [2, 3]


def test_invalid_lighting_command_is_rejected(client: TestClient) -> None:
    payload = actuator_payload()
    payload["command"]["lighting_percent"] = 101.0

    response = client.post("/zones/r1-s1/actuators", json=payload)

    assert response.status_code == 422


def test_negative_physical_output_is_rejected(client: TestClient) -> None:
    payload = actuator_payload()
    payload["output"]["lighting_power_watts"] = -1.0

    response = client.post("/zones/r1-s1/actuators", json=payload)

    assert response.status_code == 422


def test_actuators_require_known_zone(client: TestClient) -> None:
    response = client.post("/zones/unknown/actuators", json=actuator_payload())

    assert response.status_code == 404


def test_latest_actuator_snapshot_without_data_returns_404(
    client: TestClient,
) -> None:
    response = client.get("/zones/r1-s1/actuators/latest")

    assert response.status_code == 404


def test_actuator_history_rejects_inverted_interval(client: TestClient) -> None:
    response = client.get(
        "/zones/r1-s1/actuators",
        params={
            "from": "2026-07-29T11:00:00Z",
            "to": "2026-07-29T10:00:00Z",
        },
    )

    assert response.status_code == 400
