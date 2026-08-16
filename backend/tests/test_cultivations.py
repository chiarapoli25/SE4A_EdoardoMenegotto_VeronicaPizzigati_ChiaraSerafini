import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.main import app, get_db, get_export_directory


@pytest.fixture()
def export_directory(tmp_path: Path) -> Path:
    return tmp_path / "recipes"


@pytest.fixture()
def client(export_directory: Path) -> TestClient:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_export_directory] = lambda: export_directory
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        connection.close()


def create_zone(
    client: TestClient,
    zone_id: str = "r1-s1",
    species: str = "Tomato",
    sector_number: int = 1,
) -> None:
    response = client.post(
        "/zones",
        json={
            "id": zone_id,
            "name": f"Reparto 1 - Settore {sector_number}",
            "department_number": 1,
            "sector_number": sector_number,
            "plant_species": species,
        },
    )
    assert response.status_code == 201


def create_recipe(
    client: TestClient, example_recipe_data: dict, version: int = 1
) -> dict:
    payload = {**example_recipe_data, "version": version}
    response = client.post("/recipes", json=payload)
    assert response.status_code == 201
    return payload


def draft_payload(
    cultivation_id: str = "cult-1",
    zone_id: str = "r1-s1",
    plant_species: str = "Tomato",
    recipe_id: str = "tomato_demo_v1",
    recipe_version: int = 1,
) -> dict:
    return {
        "id": cultivation_id,
        "zone_id": zone_id,
        "plant_species": plant_species,
        "recipe_id": recipe_id,
        "recipe_version": recipe_version,
    }


def test_open_draft_requires_existing_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_recipe(client, example_recipe_data)

    response = client.post("/cultivations", json=draft_payload())

    assert response.status_code == 404


def test_confirm_and_positive_activation_makes_cultivation_active(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)

    created = client.post("/cultivations", json=draft_payload())
    assert created.status_code == 201
    assert created.json()["status"] == "draft"

    confirmed = client.post("/cultivations/cult-1/confirm", json={})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_at"] is not None

    activated = client.post(
        "/cultivations/cult-1/activation-result",
        json={"success": True, "applied_time_scale": 2.0},
    )
    assert activated.status_code == 200
    body = activated.json()
    assert body["status"] == "active"
    assert body["started_at"] is not None
    assert body["applied_time_scale"] == 2.0


def test_failed_activation_keeps_reason_and_frees_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())
    client.post("/cultivations/cult-1/confirm", json={})

    failed = client.post(
        "/cultivations/cult-1/activation-result",
        json={"success": False, "error_message": "edge unreachable"},
    )

    assert failed.status_code == 200
    body = failed.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "edge unreachable"
    assert body["started_at"] is None

    # Il settore e libero: si puo aprire una nuova bozza.
    reopened = client.post("/cultivations", json=draft_payload("cult-2"))
    assert reopened.status_code == 201


def test_only_one_non_concluded_cultivation_per_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    assert client.post("/cultivations", json=draft_payload()).status_code == 201

    conflict = client.post("/cultivations", json=draft_payload("cult-2"))

    assert conflict.status_code == 409


def test_confirm_rejects_species_mismatch(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client, species="Tomato")
    create_recipe(client, example_recipe_data)
    client.post(
        "/cultivations",
        json=draft_payload(plant_species="Basilico"),
    )

    response = client.post("/cultivations/cult-1/confirm", json={})

    assert response.status_code == 409


def test_confirm_rejects_missing_recipe_version(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data, version=1)
    client.post("/cultivations", json=draft_payload(recipe_version=5))

    response = client.post("/cultivations/cult-1/confirm", json={})

    assert response.status_code == 409


def test_confirm_fixes_recipe_version_even_after_new_version_is_published(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data, version=1)
    client.post("/cultivations", json=draft_payload(recipe_version=1))

    # Una nuova versione della ricetta viene pubblicata dopo la bozza.
    create_recipe(client, example_recipe_data, version=2)

    confirmed = client.post("/cultivations/cult-1/confirm", json={})

    assert confirmed.status_code == 200
    assert confirmed.json()["recipe_version"] == 1


def test_confirm_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())
    assert client.post("/cultivations/cult-1/confirm", json={}).status_code == 200

    again = client.post("/cultivations/cult-1/confirm", json={})

    assert again.status_code == 409


def test_pause_resume_and_complete_workflow(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())
    client.post("/cultivations/cult-1/confirm", json={})
    client.post(
        "/cultivations/cult-1/activation-result", json={"success": True}
    )

    paused = client.post(
        "/cultivations/cult-1/pause",
        json={"elapsed_simulation_seconds": 3600},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["elapsed_simulation_seconds"] == 3600

    resumed = client.post("/cultivations/cult-1/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    completed = client.post(
        "/cultivations/cult-1/complete",
        json={"elapsed_simulation_seconds": 7200},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None
    assert completed.json()["elapsed_simulation_seconds"] == 7200


def test_pause_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())

    response = client.post("/cultivations/cult-1/pause")

    assert response.status_code == 409


def test_read_missing_cultivation_returns_404(client: TestClient) -> None:
    response = client.get("/cultivations/does-not-exist")

    assert response.status_code == 404


def test_list_cultivations_filters_by_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client, "r1-s1", "Tomato", sector_number=1)
    create_zone(client, "r1-s2", "Basilico", sector_number=2)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload("cult-1", "r1-s1"))
    client.post(
        "/cultivations",
        json=draft_payload("cult-2", "r1-s2", plant_species="Basilico"),
    )

    filtered = client.get("/cultivations", params={"zone_id": "r1-s1"})

    assert [item["id"] for item in filtered.json()] == ["cult-1"]
