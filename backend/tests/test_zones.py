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


def zone_payload(
    zone_id: str = "r1-s1",
    department_number: int = 1,
    sector_number: int = 1,
    plant_species: str = "Pomodoro",
) -> dict:
    return {
        "id": zone_id,
        "name": f"Reparto {department_number} - Settore {sector_number}",
        "department_number": department_number,
        "sector_number": sector_number,
        "plant_species": plant_species,
    }


def test_create_and_read_zone(client: TestClient) -> None:
    created = client.post("/zones", json=zone_payload())

    assert created.status_code == 201
    assert created.json() == {
        **zone_payload(),
        "active_recipe_id": None,
        "current_phase": None,
        "status": "offline",
        "last_edge_contact": None,
    }

    read = client.get("/zones/r1-s1")
    assert read.status_code == 200
    assert read.json() == created.json()


def test_list_zones_orders_departments_and_sectors(client: TestClient) -> None:
    client.post("/zones", json=zone_payload("r2-s2", 2, 2, "Lattuga"))
    client.post("/zones", json=zone_payload("r1-s2", 1, 2, "Basilico"))
    client.post("/zones", json=zone_payload("r1-s1", 1, 1, "Pomodoro"))

    response = client.get("/zones")

    assert response.status_code == 200
    assert [zone["id"] for zone in response.json()] == [
        "r1-s1",
        "r1-s2",
        "r2-s2",
    ]


def test_each_department_accepts_at_most_two_sector_numbers(
    client: TestClient,
) -> None:
    assert client.post("/zones", json=zone_payload("r1-s1", 1, 1)).status_code == 201
    assert client.post("/zones", json=zone_payload("r1-s2", 1, 2)).status_code == 201

    invalid_sector = client.post(
        "/zones", json=zone_payload("r1-s3", 1, 3, "Basilico")
    )

    assert invalid_sector.status_code == 422


def test_greenhouse_accepts_only_four_departments(client: TestClient) -> None:
    response = client.post("/zones", json=zone_payload("r5-s1", 5, 1))

    assert response.status_code == 422


def test_cannot_assign_two_zones_to_same_physical_sector(
    client: TestClient,
) -> None:
    assert client.post("/zones", json=zone_payload()).status_code == 201

    conflict = client.post(
        "/zones", json=zone_payload("other-zone", 1, 1, "Lattuga")
    )

    assert conflict.status_code == 409


def test_zone_contains_one_scalar_plant_species(client: TestClient) -> None:
    response = client.post(
        "/zones",
        json={**zone_payload(), "plant_species": ["Pomodoro", "Basilico"]},
    )

    assert response.status_code == 422


def test_read_missing_zone_returns_404(client: TestClient) -> None:
    response = client.get("/zones/does-not-exist")

    assert response.status_code == 404
