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


def confirm_and_get_command_id(client: TestClient, cultivation_id: str = "cult-1") -> str:
    """Conferma la bozza e restituisce l'id del comando ActivateCultivation accodato."""
    confirmed = client.post(f"/cultivations/{cultivation_id}/confirm", json={})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    command_id = confirmed.json()["activation_command_id"]
    assert command_id
    return command_id


def report_command_result(
    client: TestClient,
    zone_id: str,
    command_id: str,
    status: str,
    message: str,
) -> None:
    response = client.post(
        f"/zones/{zone_id}/commands/{command_id}/result",
        json={"status": status, "message": message, "replayed": False},
    )
    assert response.status_code == 200


def test_open_draft_requires_existing_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_recipe(client, example_recipe_data)

    response = client.post("/cultivations", json=draft_payload())

    assert response.status_code == 404


def test_confirm_enqueues_activate_cultivation_command(
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
    command_id = confirmed.json()["activation_command_id"]
    assert command_id

    pending = client.get("/zones/r1-s1/commands")
    assert pending.status_code == 200
    matching = [c for c in pending.json() if c["command_id"] == command_id]
    assert len(matching) == 1
    assert matching[0]["command_type"] == "ActivateCultivation"
    assert matching[0]["payload"]["cultivation_id"] == "cult-1"
    assert matching[0]["payload"]["recipe"]["version"] == 1


def test_positive_command_result_activates_the_cultivation(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())
    command_id = confirm_and_get_command_id(client)

    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    cultivation = client.get("/cultivations/cult-1").json()
    assert cultivation["status"] == "active"
    assert cultivation["started_at"] is not None


def test_rejected_command_result_fails_cultivation_and_frees_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())
    command_id = confirm_and_get_command_id(client)

    report_command_result(client, "r1-s1", command_id, "rejected", "edge unreachable")

    cultivation = client.get("/cultivations/cult-1").json()
    assert cultivation["status"] == "failed"
    assert cultivation["error_message"] == "edge unreachable"
    assert cultivation["started_at"] is None

    # Il settore e libero: si puo aprire una nuova bozza.
    reopened = client.post("/cultivations", json=draft_payload("cult-2"))
    assert reopened.status_code == 201


def test_replaying_the_same_command_result_is_a_no_op(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())
    command_id = confirm_and_get_command_id(client)

    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    cultivation = client.get("/cultivations/cult-1").json()
    assert cultivation["status"] == "active"


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
    pending = client.get("/zones/r1-s1/commands").json()
    activate = next(
        c for c in pending if c["command_id"] == confirmed.json()["activation_command_id"]
    )
    assert activate["payload"]["recipe"]["version"] == 1


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
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

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

    # Pausa e ripresa hanno anche notificato la coda comandi della zona.
    types = {c["command_type"] for c in client.get("/zones/r1-s1/commands").json()}
    assert "PauseCultivation" in types
    assert "ResumeCultivation" in types
    assert "StopCultivation" in types


def test_pause_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())

    response = client.post("/cultivations/cult-1/pause")

    assert response.status_code == 409


def test_resume_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())

    response = client.post("/cultivations/cult-1/resume")

    assert response.status_code == 409


def test_complete_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    client.post("/cultivations", json=draft_payload())

    response = client.post("/cultivations/cult-1/complete", json={})

    assert response.status_code == 409


@pytest.mark.parametrize(
    "path",
    ["confirm", "pause", "resume", "complete"],
)
def test_transition_on_missing_cultivation_returns_404(
    client: TestClient, path: str
) -> None:
    response = client.post(f"/cultivations/does-not-exist/{path}", json={})

    assert response.status_code == 404


def test_confirm_rejects_substrate_mismatch_with_previous_run(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data, version=1)
    draining_recipe = {
        **example_recipe_data,
        "id": "tomato_draining_v1",
        "substrate": "draining",
        "version": 1,
    }
    assert client.post("/recipes", json=draining_recipe).status_code == 201

    # Primo ciclo: coltivato con substrato aerated-universal e concluso
    # regolarmente, cosi da liberare il settore per un nuovo ciclo.
    client.post("/cultivations", json=draft_payload())
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")
    assert client.post("/cultivations/cult-1/complete", json={}).status_code == 200

    # Secondo ciclo sullo stesso settore, ma con un substrato diverso.
    client.post(
        "/cultivations",
        json=draft_payload("cult-2", recipe_id="tomato_draining_v1"),
    )

    response = client.post("/cultivations/cult-2/confirm", json={})

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
