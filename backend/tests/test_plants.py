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


def test_delete_plant_removes_it_and_returns_404_after(
    client: TestClient,
) -> None:
    register_tomato(client)

    deleted = client.delete("/plants/tomato-1")

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/plants/tomato-1").status_code == 404


def test_delete_plant_via_versioned_route_also_works(client: TestClient) -> None:
    assert client.post(
        "/plants",
        json={
            "id": "tomato-versioned",
            "species": "Pomodoro",
            "home_zone_id": "tomatoes",
        },
    ).status_code == 201

    deleted = client.delete("/api/v1/plants/tomato-versioned")

    assert deleted.status_code == 204
    assert client.get("/plants/tomato-versioned").status_code == 404


def test_delete_missing_plant_returns_404_not_500(client: TestClient) -> None:
    response = client.delete("/plants/does-not-exist")

    assert response.status_code == 404


def test_delete_plant_works_while_quarantined(client: TestClient) -> None:
    # Unlike a zone, a plant has no "active process" of its own tied to it
    # that would make an immediate deletion dangerous -- deletion is
    # unconditional whether the plant is normal or currently quarantined.
    register_tomato(client)
    quarantine_tomato(client)
    assert client.get("/plants/tomato-1").json()["is_quarantined"] is True

    deleted = client.delete("/plants/tomato-1")

    assert deleted.status_code == 204
    assert client.get("/plants/tomato-1").status_code == 404


def test_delete_plant_cascades_to_its_movement_history(
    client: TestClient,
) -> None:
    register_tomato(client)
    quarantine_tomato(client)
    movements_before = client.get("/plants/tomato-1/movements")
    assert len(movements_before.json()) == 1

    deleted = client.delete("/plants/tomato-1")
    assert deleted.status_code == 204

    # The plant is gone, so /movements now 404s the same way it would for
    # any other id that never existed.
    assert client.get("/plants/tomato-1/movements").status_code == 404


def test_delete_plant_actually_removes_movement_rows_from_the_database() -> None:
    # The API-level check above only proves /movements 404s once the plant
    # is gone -- that would be equally true if the rows were merely
    # orphaned (unreachable via the plant's own id) rather than deleted. Set
    # up an isolated connection so the underlying plant_movements table can
    # be queried directly and the rows confirmed actually gone, not just
    # unreachable.
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    try:
        assert test_client.post(
            "/zones",
            json={
                "id": "tomatoes",
                "name": "Pomodori",
                "department_number": 1,
                "sector_number": 1,
                "plant_species": "Pomodoro",
            },
        ).status_code == 201
        assert test_client.post(
            "/zones",
            json={
                "id": "quarantine-1",
                "name": "Quarantena",
                "department_number": 5,
                "sector_number": 1,
                "plant_species": None,
            },
        ).status_code == 201
        register_tomato(test_client)
        quarantine_tomato(test_client)

        rows_before = connection.execute(
            "SELECT COUNT(*) FROM plant_movements WHERE plant_id = ?",
            ("tomato-1",),
        ).fetchone()[0]
        assert rows_before == 1

        deleted = test_client.delete("/plants/tomato-1")
        assert deleted.status_code == 204

        rows_after = connection.execute(
            "SELECT COUNT(*) FROM plant_movements WHERE plant_id = ?",
            ("tomato-1",),
        ).fetchone()[0]
        assert rows_after == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM plants WHERE id = ?", ("tomato-1",)
        ).fetchone()[0] == 0
    finally:
        app.dependency_overrides.clear()
        connection.close()
