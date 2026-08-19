import sqlite3
import time
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


def telemetry_payload(
    sequence_number: int = 1,
    recorded_at: str = "2026-07-29T10:00:00Z",
) -> dict:
    return {
        "sequence_number": sequence_number,
        "recorded_at": recorded_at,
        "timestamp_seconds": sequence_number * 15.0,
        "temperature_c": 24.5,
        "air_humidity_percent": 61.0,
        "soil_moisture_percent": 48.2,
        "soil_bulk_ec_ms_cm": 0.68,
        "soil_ec_ms_cm": 1.75,
        "fertilizer_concentration_mg_per_liter": 388.9,
        "nitrogen_estimate_mg_per_liter": 145.8,
        "phosphorus_estimate_mg_per_liter": 48.6,
        "potassium_estimate_mg_per_liter": 194.5,
        "ph": 6.4,
        "light_ppfd_umol_m2_s": 520.0,
        "active_recipe_id": "recipe-pomodoro",
        "active_recipe_version": 3,
        "current_phase": "Crescita vegetativa",
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
            "soil_moisture": 52.0,
            "light": 520.0,
            "ph": 6.4,
            "nitrogen": 145.8,
            "phosphorus": 48.6,
            "potassium": 194.5,
        },
        "time_scale": 10.0,
    }


def test_ingest_and_read_latest_telemetry(client: TestClient) -> None:
    created = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())

    assert created.status_code == 201
    body = created.json()
    assert body["sample_id"] == 1
    assert body["zone_id"] == "r1-s1"
    assert body["sequence_number"] == 1
    assert body["recorded_at"] == "2026-07-29T10:00:00Z"
    assert body["received_at"] is not None
    assert body["soil_ec_ms_cm"] == 1.75
    assert body["fertilizer_concentration_mg_per_liter"] == 388.9

    latest = client.get("/zones/r1-s1/telemetry/latest")
    assert latest.status_code == 200
    assert latest.json() == body


def test_ingest_marks_zone_online(client: TestClient) -> None:
    response = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())
    assert response.status_code == 201

    zone = client.get("/zones/r1-s1").json()
    assert zone["status"] == "online"
    assert zone["last_edge_contact"] == response.json()["received_at"]


def test_ingest_updates_persistent_zone_projection(client: TestClient) -> None:
    response = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())
    assert response.status_code == 201

    zone = client.get("/zones/r1-s1").json()
    assert zone["lifecycle_state"] == "Running"
    assert zone["operational_state"] == "Nominal"
    assert zone["active_recipe_id"] == "recipe-pomodoro"
    assert zone["active_recipe_version"] == 3
    assert zone["current_phase"] == "Crescita vegetativa"
    assert zone["current_strategies"]["ph"] == "PID"
    assert zone["current_setpoints"]["soil_moisture"] == 52.0
    assert zone["time_scale"] == 10.0


def test_zone_becomes_offline_after_configured_threshold(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS", "0.001")
    response = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())
    assert response.status_code == 201
    time.sleep(0.01)

    zone = client.get("/zones/r1-s1").json()
    assert zone["status"] == "offline"


def test_sensor_dropout_accepts_null_values(client: TestClient) -> None:
    payload = {
        **telemetry_payload(),
        "temperature_c": None,
        "ph": None,
    }

    response = client.post("/zones/r1-s1/telemetry", json=payload)

    assert response.status_code == 201
    assert response.json()["temperature_c"] is None
    assert response.json()["ph"] is None


def test_duplicate_identical_sequence_is_replayed(client: TestClient) -> None:
    first = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())
    assert first.status_code == 201

    duplicate = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())

    assert duplicate.status_code == 201
    assert duplicate.json() == first.json()


def test_same_sequence_in_a_new_boot_is_accepted(client: TestClient) -> None:
    first = client.post(
        "/zones/r1-s1/telemetry",
        json={**telemetry_payload(), "boot_id": "boot-a"},
    )
    restarted = client.post(
        "/zones/r1-s1/telemetry",
        json={**telemetry_payload(), "boot_id": "boot-b"},
    )

    assert first.status_code == 201
    assert restarted.status_code == 201
    assert first.json()["sample_id"] != restarted.json()["sample_id"]


def test_history_filters_dates_and_orders_results(client: TestClient) -> None:
    samples = [
        telemetry_payload(1, "2026-07-29T10:00:00Z"),
        telemetry_payload(2, "2026-07-29T10:05:00Z"),
        telemetry_payload(3, "2026-07-29T10:10:00Z"),
    ]
    for sample in samples:
        assert client.post("/zones/r1-s1/telemetry", json=sample).status_code == 201

    response = client.get(
        "/zones/r1-s1/telemetry",
        params={
            "from": "2026-07-29T10:04:00Z",
            "to": "2026-07-29T10:11:00Z",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    assert [sample["sequence_number"] for sample in response.json()] == [2, 3]


def test_older_sample_does_not_roll_back_current_projection(
    client: TestClient,
) -> None:
    newest = {
        **telemetry_payload(1, "2026-07-29T10:10:00Z"),
        "current_phase": "Fioritura",
        "active_recipe_version": 4,
    }
    older = {
        **telemetry_payload(2, "2026-07-29T10:00:00Z"),
        "current_phase": "Attecchimento",
        "active_recipe_version": 2,
    }
    assert client.post("/zones/r1-s1/telemetry", json=newest).status_code == 201
    assert client.post("/zones/r1-s1/telemetry", json=older).status_code == 201

    zone = client.get("/zones/r1-s1").json()
    assert zone["current_phase"] == "Fioritura"
    assert zone["active_recipe_version"] == 4


def test_invalid_sensor_value_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/zones/r1-s1/telemetry",
        json={**telemetry_payload(), "ph": 15.0},
    )

    assert response.status_code == 422


def test_negative_fertilizer_estimate_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/zones/r1-s1/telemetry",
        json={
            **telemetry_payload(),
            "fertilizer_concentration_mg_per_liter": -1.0,
        },
    )

    assert response.status_code == 422


def test_snapshot_requires_strategy_for_every_controlled_variable(
    client: TestClient,
) -> None:
    payload = telemetry_payload()
    del payload["current_strategies"]["potassium"]

    response = client.post("/zones/r1-s1/telemetry", json=payload)

    assert response.status_code == 422


def test_telemetry_requires_known_zone(client: TestClient) -> None:
    response = client.post("/zones/unknown/telemetry", json=telemetry_payload())

    assert response.status_code == 404


def test_latest_without_samples_returns_404(client: TestClient) -> None:
    response = client.get("/zones/r1-s1/telemetry/latest")

    assert response.status_code == 404


def test_history_rejects_inverted_interval(client: TestClient) -> None:
    response = client.get(
        "/zones/r1-s1/telemetry",
        params={
            "from": "2026-07-29T11:00:00Z",
            "to": "2026-07-29T10:00:00Z",
        },
    )

    assert response.status_code == 400
