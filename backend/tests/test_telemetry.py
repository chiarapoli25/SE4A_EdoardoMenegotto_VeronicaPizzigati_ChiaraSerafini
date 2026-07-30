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
        test_client = TestClient(app)
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
        "ph": 6.4,
        "light_ppfd_umol_m2_s": 520.0,
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

    latest = client.get("/zones/r1-s1/telemetry/latest")
    assert latest.status_code == 200
    assert latest.json() == body


def test_ingest_marks_zone_online(client: TestClient) -> None:
    response = client.post("/zones/r1-s1/telemetry", json=telemetry_payload())
    assert response.status_code == 201

    zone = client.get("/zones/r1-s1").json()
    assert zone["status"] == "online"
    assert zone["last_edge_contact"] == response.json()["received_at"]


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


def test_invalid_sensor_value_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/zones/r1-s1/telemetry",
        json={**telemetry_payload(), "ph": 15.0},
    )

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
