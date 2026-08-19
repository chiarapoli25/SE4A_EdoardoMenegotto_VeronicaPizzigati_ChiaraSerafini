import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import auth_secret, auth_token_ttl_seconds
from backend.app.database import init_db
from backend.app.features.auth.models import UserCreate, UserRole
from backend.app.features.auth.repository import create_user
from backend.app.features.auth.security import create_access_token
from backend.app.main import app, get_db


def issue_token(connection: sqlite3.Connection, username: str, role: UserRole) -> str:
    """Crea un utente e restituisce un token valido, senza passare da HTTP."""
    user = create_user(
        connection,
        UserCreate(username=username, password="Test-password-1", role=role),
    )
    token, _ = create_access_token(
        {"sub": user.username, "role": user.role.value},
        auth_secret(),
        auth_token_ttl_seconds(),
    )
    return token


@pytest.fixture()
def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def client(connection: sqlite3.Connection) -> TestClient:
    """Client autenticato come agronomo: puo eseguire l'intero workflow."""

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    token = issue_token(connection, "agronomist-1", UserRole.AGRONOMIST)
    try:
        yield TestClient(app, headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.clear()


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
    plant_species: str = "Tomato",
    recipe_id: str = "tomato_demo_v1",
    recipe_version: int = 1,
) -> dict:
    return {
        "id": cultivation_id,
        "plant_species": plant_species,
        "recipe_id": recipe_id,
        "recipe_version": recipe_version,
    }


def open_draft(
    client: TestClient, zone_id: str = "r1-s1", **payload_overrides
) -> None:
    response = client.post(
        f"/zones/{zone_id}/cultivations", json=draft_payload(**payload_overrides)
    )
    assert response.status_code == 201


def confirm_and_get_command_id(
    client: TestClient, zone_id: str = "r1-s1", cultivation_id: str = "cult-1"
) -> str:
    """Conferma la bozza e restituisce l'id del comando ActivateCultivation accodato."""
    confirmed = client.post(
        f"/zones/{zone_id}/cultivations/{cultivation_id}/confirm", json={}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "starting"
    command_id = confirmed.json()["activation_command_id"]
    assert command_id
    return command_id


def report_command_result(
    client: TestClient,
    zone_id: str,
    command_id: str,
    status: str,
    message: str,
    result: dict | None = None,
) -> None:
    body = {"status": status, "message": message, "replayed": False}
    if result is not None:
        body["result"] = result
    response = client.post(
        f"/zones/{zone_id}/commands/{command_id}/result",
        json=body,
    )
    assert response.status_code == 200


def test_open_draft_requires_existing_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_recipe(client, example_recipe_data)

    response = client.post("/zones/r1-s1/cultivations", json=draft_payload())

    assert response.status_code == 404


def test_confirm_enqueues_activate_cultivation_command(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    created = client.post("/zones/r1-s1/cultivations", json=draft_payload())
    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert created.json()["zone_id"] == "r1-s1"

    confirmed = client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={})

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "starting"
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
    open_draft(client)
    command_id = confirm_and_get_command_id(client)

    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "active"
    assert cultivation["started_at"] is not None


def test_rejected_command_result_fails_cultivation_and_frees_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)

    report_command_result(client, "r1-s1", command_id, "rejected", "edge unreachable")

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "failed"
    assert cultivation["error_message"] == "edge unreachable"
    assert cultivation["started_at"] is None

    # Il settore e libero: si puo aprire una nuova bozza.
    reopened = client.post(
        "/zones/r1-s1/cultivations", json=draft_payload("cult-2")
    )
    assert reopened.status_code == 201


def test_replaying_the_same_command_result_is_a_no_op(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)

    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "active"


def test_only_one_non_concluded_cultivation_per_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    assert (
        client.post("/zones/r1-s1/cultivations", json=draft_payload()).status_code
        == 201
    )

    conflict = client.post(
        "/zones/r1-s1/cultivations", json=draft_payload("cult-2")
    )

    assert conflict.status_code == 409


def test_confirm_rejects_species_mismatch(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client, species="Tomato")
    create_recipe(client, example_recipe_data)
    open_draft(client, plant_species="Basilico")

    response = client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={})

    assert response.status_code == 409


def test_confirm_rejects_missing_recipe_version(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data, version=1)
    open_draft(client, recipe_version=5)

    response = client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={})

    assert response.status_code == 409


def test_confirm_fixes_recipe_version_even_after_new_version_is_published(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data, version=1)
    open_draft(client, recipe_version=1)

    # Una nuova versione della ricetta viene pubblicata dopo la bozza.
    create_recipe(client, example_recipe_data, version=2)

    confirmed = client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={})

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
    open_draft(client)
    assert (
        client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={}).status_code
        == 200
    )

    again = client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={})

    assert again.status_code == 409


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
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")
    assert (
        client.post("/zones/r1-s1/cultivations/cult-1/complete", json={}).status_code
        == 200
    )

    # Secondo ciclo sullo stesso settore, ma con un substrato diverso.
    client.post(
        "/zones/r1-s1/cultivations",
        json=draft_payload("cult-2", recipe_id="tomato_draining_v1"),
    )

    response = client.post("/zones/r1-s1/cultivations/cult-2/confirm", json={})

    assert response.status_code == 409


def test_pause_resume_and_complete_workflow(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    paused = client.post(
        "/zones/r1-s1/cultivations/cult-1/pause",
        json={"elapsed_simulation_seconds": 3600},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["elapsed_simulation_seconds"] == 3600

    resumed = client.post("/zones/r1-s1/cultivations/cult-1/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    completed = client.post(
        "/zones/r1-s1/cultivations/cult-1/complete",
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
    open_draft(client)

    response = client.post("/zones/r1-s1/cultivations/cult-1/pause")

    assert response.status_code == 409


def test_resume_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)

    response = client.post("/zones/r1-s1/cultivations/cult-1/resume")

    assert response.status_code == 409


def test_complete_wrong_state_returns_409(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)

    response = client.post("/zones/r1-s1/cultivations/cult-1/complete", json={})

    assert response.status_code == 409


@pytest.mark.parametrize(
    "path",
    ["confirm", "pause", "resume", "complete"],
)
def test_transition_on_missing_cultivation_returns_404(
    client: TestClient, path: str
) -> None:
    create_zone(client)

    response = client.post(
        f"/zones/r1-s1/cultivations/does-not-exist/{path}", json={}
    )

    assert response.status_code == 404


def test_transition_returns_404_when_zone_path_does_not_match(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client, "r1-s1", "Tomato", sector_number=1)
    create_zone(client, "r1-s2", "Basilico", sector_number=2)
    create_recipe(client, example_recipe_data)
    open_draft(client, "r1-s1")

    # cult-1 appartiene a r1-s1: confermarla dal path di r1-s2 non deve
    # trovarla, esattamente come se non esistesse.
    response = client.post("/zones/r1-s2/cultivations/cult-1/confirm", json={})

    assert response.status_code == 404


def test_read_missing_cultivation_returns_404(client: TestClient) -> None:
    create_zone(client)

    response = client.get("/zones/r1-s1/cultivations/does-not-exist")

    assert response.status_code == 404


def test_read_active_cultivation_returns_current_non_concluded_cultivation(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)

    active = client.get("/zones/r1-s1/cultivations/active")

    assert active.status_code == 200
    assert active.json()["id"] == "cult-1"


def test_read_active_cultivation_returns_404_when_none(
    client: TestClient,
) -> None:
    create_zone(client)

    response = client.get("/zones/r1-s1/cultivations/active")

    assert response.status_code == 404


def test_list_cultivations_filters_by_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client, "r1-s1", "Tomato", sector_number=1)
    create_zone(client, "r1-s2", "Basilico", sector_number=2)
    create_recipe(client, example_recipe_data)
    open_draft(client, "r1-s1")
    client.post(
        "/zones/r1-s2/cultivations",
        json=draft_payload("cult-2", plant_species="Basilico"),
    )

    filtered = client.get("/zones/r1-s1/cultivations")

    assert [item["id"] for item in filtered.json()] == ["cult-1"]


# --- Contratti dei comandi ("Nuovi comandi") ------------------------------


def test_activate_cultivation_payload_includes_initial_time_scale(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)

    confirmed = client.post("/zones/r1-s1/cultivations/cult-1/confirm", json={})

    assert confirmed.status_code == 200
    command_id = confirmed.json()["activation_command_id"]
    pending = client.get("/zones/r1-s1/commands").json()
    activate = next(c for c in pending if c["command_id"] == command_id)
    assert activate["payload"]["cultivation_id"] == "cult-1"
    assert activate["payload"]["recipe_id"] == "tomato_demo_v1"
    assert activate["payload"]["recipe_version"] == 1
    assert activate["payload"]["initial_time_scale"] == 1.0


def test_pause_resume_stop_payloads_match_the_edge_contract(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    client.post(
        "/zones/r1-s1/cultivations/cult-1/pause",
        json={"elapsed_simulation_seconds": 1800},
    )
    client.post(
        "/zones/r1-s1/cultivations/cult-1/resume", json={"time_scale": 5.0}
    )
    client.post(
        "/zones/r1-s1/cultivations/cult-1/complete",
        json={"elapsed_simulation_seconds": 3600, "reason": "ciclo concluso"},
    )

    commands_by_type = {
        c["command_type"]: c["payload"]
        for c in client.get("/zones/r1-s1/commands").json()
    }
    assert commands_by_type["PauseCultivation"] == {"cultivation_id": "cult-1"}
    assert commands_by_type["ResumeCultivation"] == {
        "cultivation_id": "cult-1",
        "time_scale": 5.0,
    }
    assert commands_by_type["StopCultivation"] == {
        "cultivation_id": "cult-1",
        "reason": "ciclo concluso",
    }


def test_resume_without_time_scale_omits_it_from_the_payload(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")
    client.post("/zones/r1-s1/cultivations/cult-1/pause")

    client.post("/zones/r1-s1/cultivations/cult-1/resume")

    commands = client.get("/zones/r1-s1/commands").json()
    resume = next(c for c in commands if c["command_type"] == "ResumeCultivation")
    assert resume["payload"] == {"cultivation_id": "cult-1"}


def test_simulation_speed_command_includes_active_cultivation_id(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    response = client.patch(
        "/zones/r1-s1/simulation-speed", json={"time_scale": 4.0}
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "time_scale": 4.0,
        "cultivation_id": "cult-1",
    }


def test_simulation_speed_command_omits_cultivation_id_without_active_cycle(
    client: TestClient,
) -> None:
    create_zone(client)

    response = client.patch(
        "/zones/r1-s1/simulation-speed", json={"time_scale": 4.0}
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {"time_scale": 4.0}


# --- Risultati strutturati ------------------------------------------------


def test_structured_result_sets_applied_time_scale_and_current_phase(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)

    report_command_result(
        client,
        "r1-s1",
        command_id,
        "succeeded",
        "cultivation activated",
        result={
            "cultivation_id": "cult-1",
            "recipe_id": "tomato_demo_v1",
            "recipe_version": 1,
            "applied_time_scale": 2.5,
            "current_phase": "Germinazione",
        },
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "active"
    assert cultivation["applied_time_scale"] == 2.5
    assert cultivation["current_phase"] == "Germinazione"


def test_structured_result_is_optional_and_falls_back_to_requested_time_scale(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)

    report_command_result(
        client, "r1-s1", command_id, "succeeded", "cultivation activated"
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["applied_time_scale"] == 1.0
    assert cultivation["current_phase"] is None


# --- Eventi di lifecycle riportati dall'Edge ------------------------------


def post_cultivation_event(
    client: TestClient,
    zone_id: str,
    event_id: str,
    event_type: str,
    payload: dict,
) -> None:
    response = client.post(
        f"/zones/{zone_id}/events",
        json={
            "event_id": event_id,
            "edge_id": "edge-1",
            "boot_id": "boot-1",
            "event_type": event_type,
            "timestamp_seconds": 60,
            "recorded_at": "2026-08-18T12:00:00Z",
            "payload": payload,
        },
    )
    assert response.status_code == 201


def test_cultivation_activated_event_activates_a_starting_cultivation(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    confirm_and_get_command_id(client)

    post_cultivation_event(
        client,
        "r1-s1",
        "activated-1",
        "CultivationActivated",
        {"cultivation_id": "cult-1", "applied_time_scale": 3.0, "current_phase": "Germinazione"},
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "active"
    assert cultivation["applied_time_scale"] == 3.0
    assert cultivation["current_phase"] == "Germinazione"


def test_cultivation_activation_failed_event_fails_and_frees_the_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    confirm_and_get_command_id(client)

    post_cultivation_event(
        client,
        "r1-s1",
        "activation-failed-1",
        "CultivationActivationFailed",
        {"cultivation_id": "cult-1", "error_message": "sensore non raggiungibile"},
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "failed"
    assert cultivation["error_message"] == "sensore non raggiungibile"

    reopened = client.post(
        "/zones/r1-s1/cultivations", json=draft_payload("cult-2")
    )
    assert reopened.status_code == 201


def test_cultivation_paused_and_resumed_events_reconcile_status(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    post_cultivation_event(
        client, "r1-s1", "paused-1", "CultivationPaused", {"cultivation_id": "cult-1"}
    )
    assert (
        client.get("/zones/r1-s1/cultivations/cult-1").json()["status"] == "paused"
    )

    post_cultivation_event(
        client, "r1-s1", "resumed-1", "CultivationResumed", {"cultivation_id": "cult-1"}
    )
    assert (
        client.get("/zones/r1-s1/cultivations/cult-1").json()["status"] == "active"
    )


def test_cultivation_stopped_event_completes_and_frees_the_zone(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    command_id = confirm_and_get_command_id(client)
    report_command_result(client, "r1-s1", command_id, "succeeded", "recipe applied")

    post_cultivation_event(
        client, "r1-s1", "stopped-1", "CultivationStopped",
        {"cultivation_id": "cult-1", "reason": "arresto manuale"},
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "completed"
    assert cultivation["completed_at"] is not None

    reopened = client.post(
        "/zones/r1-s1/cultivations", json=draft_payload("cult-2")
    )
    assert reopened.status_code == 201


def test_cultivation_event_is_a_no_op_when_state_is_incompatible(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)

    # La coltivazione e ancora `draft`: un evento `CultivationPaused` non ha
    # alcun effetto e non deve fallire.
    post_cultivation_event(
        client, "r1-s1", "paused-noop", "CultivationPaused", {"cultivation_id": "cult-1"}
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "draft"


def test_cultivation_activation_started_event_is_purely_informational(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_zone(client)
    create_recipe(client, example_recipe_data)
    open_draft(client)
    confirm_and_get_command_id(client)

    post_cultivation_event(
        client, "r1-s1", "activation-started-1", "CultivationActivationStarted",
        {"cultivation_id": "cult-1"},
    )

    cultivation = client.get("/zones/r1-s1/cultivations/cult-1").json()
    assert cultivation["status"] == "starting"
    events = client.get("/zones/r1-s1/events").json()
    assert [e["event_id"] for e in events] == ["activation-started-1"]
