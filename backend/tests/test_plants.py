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
    test_client = TestClient(app)
    zones = [
        {
            "id": "tomatoes",
            "name": "Pomodori",
            "department_number": 1,
            "sector_number": 1,
            "plant_species": "Pomodoro",
        },
        {
            "id": "basil",
            "name": "Basilico",
            "department_number": 2,
            "sector_number": 1,
            "plant_species": "Basilico",
        },
        {
            "id": "quarantine-1",
            "name": "Quarantena",
            "department_number": 5,
            "sector_number": 1,
            "plant_species": None,
        },
    ]
    for zone in zones:
        assert test_client.post("/zones", json=zone).status_code == 201
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
        connection.close()


def register_tomato(client: TestClient) -> None:
    response = client.post(
        "/plants",
        json={
            "id": "tomato-1",
            "species": "Pomodoro",
            "home_zone_id": "tomatoes",
        },
    )
    assert response.status_code == 201


def quarantine_tomato(client: TestClient) -> None:
    response = client.patch(
        "/plants/tomato-1/quarantine",
        json={
            "is_quarantined": True,
            "quarantine_zone_id": "quarantine-1",
            "reason": "foglie con sintomi sospetti",
        },
    )
    assert response.status_code == 200


def test_plant_starts_outside_quarantine(client: TestClient) -> None:
    response = client.post(
        "/api/v1/plants",
        json={
            "id": "tomato-1",
            "species": "Pomodoro",
            "home_zone_id": "tomatoes",
        },
    )

    assert response.status_code == 201
    assert response.json()["current_zone_id"] == "tomatoes"
    assert response.json()["is_quarantined"] is False
    assert response.json()["quarantine_reason"] is None


def test_quarantine_is_a_flag_of_the_individual_plant(
    client: TestClient,
) -> None:
    register_tomato(client)

    response = client.patch(
        "/plants/tomato-1/quarantine",
        json={
            "is_quarantined": True,
            "quarantine_zone_id": "quarantine-1",
            "reason": "foglie con sintomi sospetti",
        },
    )

    assert response.status_code == 200
    assert response.json()["is_quarantined"] is True
    assert response.json()["current_zone_id"] == "quarantine-1"
    assert response.json()["home_zone_id"] == "tomatoes"
    assert response.json()["species"] == "Pomodoro"


def test_different_species_can_be_quarantined_in_the_same_zone(
    client: TestClient,
) -> None:
    plants = [
        ("tomato-1", "Pomodoro", "tomatoes"),
        ("basil-1", "Basilico", "basil"),
    ]
    for plant_id, species, home_zone_id in plants:
        assert client.post(
            "/plants",
            json={
                "id": plant_id,
                "species": species,
                "home_zone_id": home_zone_id,
            },
        ).status_code == 201
        assert client.patch(
            f"/plants/{plant_id}/quarantine",
            json={
                "is_quarantined": True,
                "quarantine_zone_id": "quarantine-1",
                "reason": "controllo sanitario",
            },
        ).status_code == 200

    response = client.get(
        "/plants",
        params={"zone_id": "quarantine-1", "is_quarantined": True},
    )

    assert {plant["species"] for plant in response.json()} == {
        "Pomodoro",
        "Basilico",
    }


def test_releasing_plant_clears_flag_and_returns_it_home(
    client: TestClient,
) -> None:
    register_tomato(client)
    quarantine_tomato(client)

    released = client.patch(
        "/plants/tomato-1/quarantine",
        json={"is_quarantined": False, "reason": "pianta guarita"},
    )
    replay = client.patch(
        "/plants/tomato-1/quarantine",
        json={"is_quarantined": False, "reason": "pianta guarita"},
    )
    movements = client.get("/plants/tomato-1/movements")

    assert released.status_code == 200
    assert released.json()["is_quarantined"] is False
    assert released.json()["current_zone_id"] == "tomatoes"
    assert released.json()["quarantine_reason"] is None
    assert replay.json() == released.json()
    assert [item["is_quarantined"] for item in movements.json()] == [True, False]


def test_plant_species_must_match_home_zone(client: TestClient) -> None:
    response = client.post(
        "/plants",
        json={
            "id": "wrong-plant",
            "species": "Basilico",
            "home_zone_id": "tomatoes",
        },
    )

    assert response.status_code == 400


def test_quarantined_plant_must_move_to_fifth_department(
    client: TestClient,
) -> None:
    register_tomato(client)

    response = client.patch(
        "/plants/tomato-1/quarantine",
        json={
            "is_quarantined": True,
            "quarantine_zone_id": "basil",
            "reason": "controllo sanitario",
        },
    )

    assert response.status_code == 400
