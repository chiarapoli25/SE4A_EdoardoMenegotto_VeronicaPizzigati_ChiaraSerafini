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
    plant_species: str | None = "Pomodoro",
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
        "department_name": "Piante Tropicali e da Fogliame",
        "assigned_edge_id": None,
        "active_recipe_id": None,
        "current_phase": None,
        "administrative_status": "active",
        "status": "offline",
        "last_edge_contact": None,
    }

    read = client.get("/zones/r1-s1")
    assert read.status_code == 200
    assert read.json() == created.json()


def test_zone_responses_use_the_canonical_department_names(
    client: TestClient,
) -> None:
    expected_names = {
        1: "Piante Tropicali e da Fogliame",
        2: "Piante da Fiore",
        3: "Piante Grasse e Succulente",
        4: "Piante da Frutto e Ortaggi",
        5: "Quarantena",
    }
    for department_number, expected_name in expected_names.items():
        plant_species = None if department_number == 5 else "Specie test"
        response = client.post(
            "/zones",
            json=zone_payload(
                f"r{department_number}-s1",
                department_number,
                1,
                plant_species,
            ),
        )

        assert response.status_code == 201
        assert response.json()["department_name"] == expected_name


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


def test_edge_lists_only_its_assigned_zones(client: TestClient) -> None:
    client.post(
        "/zones",
        json={**zone_payload("edge-a-zone", 1, 1), "assigned_edge_id": "edge-a"},
    )
    client.post(
        "/zones",
        json={**zone_payload("edge-b-zone", 1, 2), "assigned_edge_id": "edge-b"},
    )
    client.post("/zones", json=zone_payload("unassigned", 2, 1))

    response = client.get("/api/v1/edges/edge-a/zones")

    assert response.status_code == 200
    assert [zone["id"] for zone in response.json()] == ["edge-a-zone"]
    assert response.json()[0]["assigned_edge_id"] == "edge-a"


def test_each_department_accepts_at_most_two_sector_numbers(
    client: TestClient,
) -> None:
    assert client.post("/zones", json=zone_payload("r1-s1", 1, 1)).status_code == 201
    assert client.post("/zones", json=zone_payload("r1-s2", 1, 2)).status_code == 201

    invalid_sector = client.post(
        "/zones", json=zone_payload("r1-s3", 1, 3, "Basilico")
    )

    assert invalid_sector.status_code == 422


def test_fifth_department_is_reserved_for_quarantine(
    client: TestClient,
) -> None:
    response = client.post(
        "/zones",
        json=zone_payload(
            "quarantine-1",
            5,
            1,
            plant_species=None,
        ),
    )

    assert response.status_code == 201
    assert response.json()["department_number"] == 5
    assert response.json()["plant_species"] is None


def test_quarantine_cannot_declare_one_plant_species(
    client: TestClient,
) -> None:
    response = client.post(
        "/zones",
        json=zone_payload(
            "quarantine-1",
            5,
            1,
            plant_species="Pomodoro",
        ),
    )

    assert response.status_code == 422


def test_sixth_department_is_rejected(client: TestClient) -> None:
    response = client.post("/zones", json=zone_payload("r6-s1", 6, 1))

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


def test_patch_zone_updates_only_supplied_fields(client: TestClient) -> None:
    created = client.post(
        "/zones",
        json={**zone_payload(), "assigned_edge_id": "edge-a"},
    ).json()

    response = client.patch(
        "/api/v1/zones/r1-s1",
        json={
            "name": "Settore pomodori nord",
            "administrative_status": "maintenance",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        **created,
        "name": "Settore pomodori nord",
        "administrative_status": "maintenance",
    }


def test_patch_zone_can_assign_and_clear_an_existing_recipe(
    client: TestClient,
    example_recipe_data: dict,
) -> None:
    client.post("/zones", json=zone_payload())
    assert client.post("/recipes", json=example_recipe_data).status_code == 201

    assigned = client.patch(
        "/api/v1/zones/r1-s1",
        json={"active_recipe_id": example_recipe_data["id"]},
    )
    cleared = client.patch(
        "/api/v1/zones/r1-s1",
        json={"active_recipe_id": None},
    )

    assert assigned.status_code == 200
    assert assigned.json()["active_recipe_id"] == example_recipe_data["id"]
    assert cleared.status_code == 200
    assert cleared.json()["active_recipe_id"] is None


def test_patch_zone_rejects_missing_recipe(client: TestClient) -> None:
    client.post("/zones", json=zone_payload())

    response = client.patch(
        "/api/v1/zones/r1-s1",
        json={"active_recipe_id": "missing-recipe"},
    )

    assert response.status_code == 404


def test_create_zone_rejects_missing_recipe(client: TestClient) -> None:
    response = client.post(
        "/api/v1/zones",
        json={**zone_payload(), "active_recipe_id": "missing-recipe"},
    )

    assert response.status_code == 404
    assert client.get("/zones/r1-s1").status_code == 404


def test_create_zone_accepts_a_catalog_recipe(client: TestClient) -> None:
    response = client.post(
        "/api/v1/zones",
        json={
            **zone_payload(plant_species="Calathea"),
            "active_recipe_id": "recipe-calathea",
        },
    )

    assert response.status_code == 201
    assert response.json()["active_recipe_id"] == "recipe-calathea"


def test_create_zone_rejects_catalog_recipe_from_another_department(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/zones",
        json={
            **zone_payload(plant_species="Lithops"),
            "active_recipe_id": "recipe-lithops",
        },
    )

    assert response.status_code == 409


def test_create_zone_rejects_catalog_recipe_for_another_species(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/zones",
        json={
            **zone_payload(plant_species="Pothos (Epipremnum)"),
            "active_recipe_id": "recipe-calathea",
        },
    )

    assert response.status_code == 409


def test_patch_zone_rejects_species_incompatible_with_registered_plants(
    client: TestClient,
) -> None:
    client.post("/zones", json=zone_payload())
    assert client.post(
        "/plants",
        json={
            "id": "plant-1",
            "species": "Pomodoro",
            "home_zone_id": "r1-s1",
        },
    ).status_code == 201

    response = client.patch(
        "/api/v1/zones/r1-s1",
        json={"plant_species": "Basilico"},
    )

    assert response.status_code == 409
    assert client.get("/zones/r1-s1").json()["plant_species"] == "Pomodoro"


def test_patch_zone_allows_species_change_without_incompatible_plants(
    client: TestClient,
) -> None:
    client.post("/zones", json=zone_payload())

    response = client.patch(
        "/api/v1/zones/r1-s1",
        json={"plant_species": "Basilico"},
    )

    assert response.status_code == 200
    assert response.json()["plant_species"] == "Basilico"


def test_patch_quarantine_cannot_set_one_species(client: TestClient) -> None:
    client.post(
        "/zones",
        json=zone_payload("quarantine-1", 5, 1, plant_species=None),
    )

    response = client.patch(
        "/api/v1/zones/quarantine-1",
        json={"plant_species": "Pomodoro"},
    )

    assert response.status_code == 422


def test_patch_zone_rejects_edge_reassignment_while_running(
    client: TestClient,
) -> None:
    client.post(
        "/zones",
        json={**zone_payload(), "assigned_edge_id": "edge-a"},
    )
    event = {
        "event_id": "lifecycle-running",
        "edge_id": "edge-a",
        "boot_id": "boot-1",
        "event_type": "ZoneLifecycleChanged",
        "timestamp_seconds": 10,
        "recorded_at": "2026-08-04T10:00:00Z",
        "payload": {
            "previous_state": "Starting",
            "current_state": "Running",
            "reason": "cultivation started",
        },
    }
    assert client.post("/api/v1/zones/r1-s1/events", json=event).status_code == 201

    same_assignment = client.patch(
        "/api/v1/zones/r1-s1",
        json={"assigned_edge_id": "edge-a"},
    )
    reassignment = client.patch(
        "/api/v1/zones/r1-s1",
        json={"assigned_edge_id": "edge-b"},
    )

    assert same_assignment.status_code == 200
    assert reassignment.status_code == 409
    assert client.get("/zones/r1-s1").json()["assigned_edge_id"] == "edge-a"


def test_patch_zone_allows_edge_reassignment_after_pause(
    client: TestClient,
) -> None:
    client.post(
        "/zones",
        json={**zone_payload(), "assigned_edge_id": "edge-a"},
    )
    for event_id, recorded_at, previous_state, current_state in (
        ("running", "2026-08-04T10:00:00Z", "Starting", "Running"),
        ("paused", "2026-08-04T10:01:00Z", "Running", "Paused"),
    ):
        response = client.post(
            "/api/v1/zones/r1-s1/events",
            json={
                "event_id": event_id,
                "edge_id": "edge-a",
                "boot_id": "boot-1",
                "event_type": "ZoneLifecycleChanged",
                "timestamp_seconds": 10,
                "recorded_at": recorded_at,
                "payload": {
                    "previous_state": previous_state,
                    "current_state": current_state,
                    "reason": "test",
                },
            },
        )
        assert response.status_code == 201

    reassigned = client.patch(
        "/api/v1/zones/r1-s1",
        json={"assigned_edge_id": "edge-b"},
    )

    assert reassigned.status_code == 200
    assert reassigned.json()["assigned_edge_id"] == "edge-b"


def test_patch_zone_rejects_unmodifiable_or_null_required_fields(
    client: TestClient,
) -> None:
    client.post("/zones", json=zone_payload())

    move = client.patch(
        "/api/v1/zones/r1-s1",
        json={"department_number": 2},
    )
    null_name = client.patch(
        "/api/v1/zones/r1-s1",
        json={"name": None},
    )

    assert move.status_code == 422
    assert null_name.status_code == 422


def test_patch_missing_zone_returns_404(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/zones/does-not-exist",
        json={"name": "Nuovo nome"},
    )

    assert response.status_code == 404
