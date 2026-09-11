import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.recipes.repository import get_recipe
from backend.app.features.simulations import live_manager as live_manager_module
from backend.app.features.simulations.live_manager import _Target, initial_steps_per_target
from backend.app.features.simulations.models import STEP_SECONDS
from backend.app.main import app, get_db


@pytest.fixture()
def client_and_connection():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), connection
    finally:
        app.dependency_overrides.clear()
        connection.close()


@pytest.fixture()
def small_horizon(monkeypatch: pytest.MonkeyPatch):
    """In produzione il primo pezzo dura quanto la prima fase della
    ricetta (14-21 giorni sul catalogo reale, vedi
    initial_steps_per_target()) — troppo lento anche alla velocita'
    massima consentita per questi test. Lo forziamo a 4 step (1h), stesso
    schema di BATCH_TIMEOUT_SECONDS in test_simulations.py. NON autouse:
    test_initial_steps_per_target_matches_the_longest_first_phase deve
    vedere la funzione vera, non questa sostituita."""
    monkeypatch.setattr(live_manager_module, "initial_steps_per_target", lambda targets: 4)


def _register_zone(client: TestClient, zone_id: str, department: int, recipe_id: str) -> None:
    assert client.post(
        "/zones",
        json={
            "id": zone_id,
            "name": f"Settore {zone_id}",
            "department_number": department,
            "sector_number": 1,
            "plant_species": "Calathea",
            "active_recipe_id": recipe_id,
        },
    ).status_code == 201


def wait_for_computed(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get(f"/simulations/live/{job_id}").json()
        if job["status"] != "computing":
            return job
        time.sleep(0.02)
    raise AssertionError("live simulation never finished computing")


def test_live_simulation_rejects_when_no_zone_has_a_recipe(client_and_connection) -> None:
    client, _ = client_and_connection
    response = client.post("/simulations/live", json={})
    assert response.status_code == 409
    assert "nothing to simulate" in response.json()["detail"]


def test_initial_steps_per_target_matches_the_longest_first_phase(
    client_and_connection,
) -> None:
    """Il primo pezzo dura quanto la fase INIZIALE piu' lunga fra i
    settori coinvolti — mai quella piu' corta, che verrebbe tagliata a
    meta' per il settore con la fase piu' lunga. Niente small_horizon qui
    apposta: deve vedere la funzione vera, non quella sostituita."""
    _, connection = client_and_connection
    # recipe-calathea: prima fase "Avvio e attecchimento" = 336h (14 giorni).
    # recipe-aloe-vera: prima fase = 504h (21 giorni, famiglia succulente) —
    # piu' lunga, deve essere lei a decidere il primo pezzo.
    calathea = get_recipe(connection, "recipe-calathea")
    aloe = get_recipe(connection, "recipe-aloe-vera")
    assert calathea.phases[0].duration_hours == 336.0
    assert aloe.phases[0].duration_hours == 504.0

    targets = [
        _Target(recipe=calathea, zone_id="r1-s1"),
        _Target(recipe=aloe, zone_id="r1-s2"),
    ]
    steps = initial_steps_per_target(targets)
    assert steps == round(504.0 * 3600 / STEP_SECONDS)


def test_live_simulation_has_no_duration_input_and_extends_its_horizon(
    client_and_connection, small_horizon,
) -> None:
    """Niente duration_seconds nella richiesta (vedi LiveSimulationCreate):
    l'orizzonte calcolato parte piccolo e RADDOPPIA da solo quando la
    riproduzione lo raggiunge (_maybe_extend) — mai uno status "finished",
    e i dati gia' rivelati non cambiano mai (lo scambio e' un prefisso
    identico, solo la coda cresce)."""
    client, _ = client_and_connection
    _register_zone(client, "r1-s1", 1, "recipe-calathea")

    created = client.post(
        "/simulations/live",
        json={"speed_multiplier": 36000.0},
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["zone_ids"] == ["r1-s1"]
    assert created.json()["status"] == "computing"

    job = wait_for_computed(client, job_id)
    assert job["status"] == "playing"
    initial_horizon = job["horizon_seconds"]
    assert initial_horizon == 4 * 900
    assert job["zones"][0]["zone_id"] == "r1-s1"
    assert job["zones"][0]["recipe"]["id"] == "recipe-calathea"

    # A velocita' 36000 (10 ore simulate/s) l'orizzonte iniziale (1h) e'
    # superato quasi subito, ben oltre la soglia del 50% che fa scattare
    # un'estensione in background — mai "finished": osserviamo qualche tick
    # di poll finche' l'orizzonte raddoppia da solo.
    deadline = time.monotonic() + 10
    horizon = initial_horizon
    while time.monotonic() < deadline:
        job = client.get(f"/simulations/live/{job_id}").json()
        assert job["status"] == "playing"
        horizon = job["horizon_seconds"]
        if horizon > initial_horizon:
            break
        time.sleep(0.02)
    assert horizon == initial_horizon * 2, "l'orizzonte avrebbe dovuto raddoppiare da solo"

    result = client.get(f"/simulations/live/{job_id}/result")
    assert result.status_code == 200
    previews = result.json()
    assert isinstance(previews, list) and len(previews) == 1
    assert previews[0]["zone_id"] == "r1-s1"
    # Il risultato non si azzera mai: puo' gia' contenere piu' step di
    # quanti ne stava l'orizzonte ORIGINALE (4), ora che si e' esteso.
    assert previews[0]["summary"]["control_cycles"] >= 1

    # Cancellare un run "playing" (mai finito da solo, vedi sopra) lo marca
    # "cancelled" e lo tiene in giro finche' non scade — stesso schema del
    # batch — non lo fa sparire subito.
    assert client.delete(f"/simulations/live/{job_id}").status_code == 204
    cancelled = client.get(f"/simulations/live/{job_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_live_simulation_extension_never_changes_already_revealed_data(
    client_and_connection, small_horizon,
) -> None:
    """Il prefisso gia' rivelato prima di un'estensione deve restare
    IDENTICO dopo — e' l'intera premessa per poter scambiare
    computed_steps senza che il grafico faccia un salto visibile."""
    client, _ = client_and_connection
    _register_zone(client, "r1-s1", 1, "recipe-calathea")

    created = client.post("/simulations/live", json={"speed_multiplier": 3600.0})
    job_id = created.json()["id"]
    wait_for_computed(client, job_id)

    before = client.get(f"/simulations/live/{job_id}/result").json()[0]["series"]
    assert before  # almeno un punto gia' rivelato

    deadline = time.monotonic() + 10
    horizon_grew = False
    while time.monotonic() < deadline:
        job = client.get(f"/simulations/live/{job_id}").json()
        if job["horizon_seconds"] > 4 * 900:
            horizon_grew = True
            break
        time.sleep(0.02)
    assert horizon_grew, "l'orizzonte avrebbe dovuto estendersi entro il timeout"

    after = client.get(f"/simulations/live/{job_id}/result").json()[0]["series"]
    assert after[: len(before)] == before

    client.delete(f"/simulations/live/{job_id}")


def test_live_simulation_pause_freezes_elapsed_time(client_and_connection, small_horizon) -> None:
    client, _ = client_and_connection
    _register_zone(client, "r1-s1", 1, "recipe-calathea")

    created = client.post(
        "/simulations/live",
        json={"speed_multiplier": 900.0},
    )
    job_id = created.json()["id"]
    wait_for_computed(client, job_id)

    time.sleep(0.3)
    paused = client.post(f"/simulations/live/{job_id}/control", json={"action": "pause"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    frozen = paused.json()["elapsed_seconds"]
    assert frozen > 0

    time.sleep(0.3)
    still_paused = client.get(f"/simulations/live/{job_id}").json()
    assert still_paused["status"] == "paused"
    assert still_paused["elapsed_seconds"] == frozen

    resumed = client.post(f"/simulations/live/{job_id}/control", json={"action": "play"})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "playing"

    client.delete(f"/simulations/live/{job_id}")


def test_only_one_live_simulation_can_run_at_a_time(client_and_connection, small_horizon) -> None:
    client, _ = client_and_connection
    _register_zone(client, "r1-s1", 1, "recipe-calathea")

    first = client.post("/simulations/live", json={"speed_multiplier": 1.0})
    assert first.status_code == 202
    second = client.post("/simulations/live", json={"speed_multiplier": 1.0})
    assert second.status_code == 409
    assert "already running" in second.json()["detail"]

    client.delete(f"/simulations/live/{first.json()['id']}")
