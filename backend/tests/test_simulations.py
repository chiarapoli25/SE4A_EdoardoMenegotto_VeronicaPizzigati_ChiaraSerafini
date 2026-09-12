import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.recipes.repository import get_recipe
from backend.app.features.simulations import manager as manager_module
from backend.app.features.simulations.manager import (
    SimulationBusy,
    SimulationManager,
    _active_actuators,
    _actuator_intensity,
    _reduce_series,
)
from backend.app.features.simulations.models import SimulationCreate
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


def wait_for_result(client: TestClient, job_id: str) -> tuple[dict, dict]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get(f"/simulations/{job_id}").json()
        if job["status"] == "succeeded":
            response = client.get(f"/simulations/{job_id}/result")
            assert response.status_code == 200
            return job, response.json()
        assert job["status"] in {"queued", "running"}, job
        time.sleep(0.02)
    raise AssertionError("batch simulation did not finish")


def test_one_week_batch_is_non_operational_and_ephemeral(
    client_and_connection,
) -> None:
    client, connection = client_and_connection
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("telemetry_samples", "actuator_snapshots", "runtime_commands")
    }
    created = client.post(
        "/simulations",
        json={"recipe_id": "recipe-calathea", "duration_seconds": 7 * 86400},
    )
    assert created.status_code == 202

    job, result = wait_for_result(client, created.json()["id"])
    assert job["progress_percent"] == 100.0
    assert result["source_label"] == "Scenario simulato — non operativo"
    assert result["non_operational"] is True
    assert result["step_seconds"] == 900
    assert result["recipe"]["strategies"] == {
        "soil_moisture": "Threshold",
        "light": "Threshold",
        "ph": "PID",
        "nitrogen": "Predictive",
        "phosphorus": "Predictive",
        "potassium": "Predictive",
    }
    assert 1 <= len(result["series"]) <= 1000
    assert result["summary"]["control_cycles"] == 7 * 24 * 4
    after = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before

    assert client.delete(f"/simulations/{job['id']}").status_code == 204
    assert client.get(f"/simulations/{job['id']}").status_code == 404


@pytest.mark.parametrize("days", [90, 360])
def test_long_batch_durations_reduce_to_at_most_one_thousand_points(days: int) -> None:
    request = SimulationCreate(
        recipe_id="recipe-calathea",
        duration_seconds=days * 86400,
    )
    assert request.duration_seconds == days * 86400
    steps = [
        {
            "start_time_seconds": index * 900,
            "duration_seconds": 900,
            "phase_name": "Fase test",
            "sensors": {"temperature_c": 20.0 + index % 5},
            "models": {"nitrogen_mg_per_liter": 100.0 + index % 7},
        }
        for index in range(request.duration_seconds // 900)
    ]
    reduced = _reduce_series(steps)
    assert len(reduced) <= 1000
    assert min(point["minimum"]["temperature_c"] for point in reduced) == 20.0
    assert max(point["maximum"]["temperature_c"] for point in reduced) == 24.0


def test_reduction_preserves_phase_changes() -> None:
    steps = [
        {
            "start_time_seconds": index * 900,
            "duration_seconds": 900,
            "phase_name": "Radicazione" if index < 1200 else "Crescita",
            "sensors": {"temperature_c": 20.0},
            "models": {},
        }
        for index in range(2400)
    ]
    reduced = _reduce_series(steps)
    phases = [point["phase_name"] for point in reduced]
    change_index = phases.index("Crescita")
    assert phases[change_index - 1] == "Radicazione"
    assert reduced[change_index]["start_seconds"] == 1200 * 900
    assert len(reduced) <= 1000


def test_partial_irrigation_remains_visible_after_fast_cutoff() -> None:
    step = {
        "actuators": {"output": {"water_pump_on": False}},
        "delivered": {"water_liters": 0.2},
        "decisions": {},
    }

    assert _active_actuators(step)["water_pump"] is True
    assert _actuator_intensity(step)["water_pump_intensity"] == 1.0


def test_batch_validation_rejects_limits_and_non_quarter_hour_steps(
    client_and_connection,
) -> None:
    client, _ = client_and_connection
    assert client.post(
        "/simulations",
        json={"recipe_id": "recipe-calathea", "duration_seconds": 899},
    ).status_code == 422
    assert client.post(
        "/simulations",
        json={"recipe_id": "recipe-calathea", "duration_seconds": 901},
    ).status_code == 422
    assert client.post(
        "/simulations",
        json={"recipe_id": "recipe-calathea", "duration_seconds": 361 * 86400},
    ).status_code == 422


class DeferredExecutor:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, function, *args):
        self.calls.append((function, args))


def local_manager(connection: sqlite3.Connection) -> tuple[SimulationManager, DeferredExecutor]:
    manager = SimulationManager()
    manager._executor.shutdown(wait=False)
    executor = DeferredExecutor()
    manager._executor = executor
    return manager, executor


def test_only_one_batch_can_be_queued_and_it_can_be_cancelled(
    client_and_connection,
) -> None:
    _, connection = client_and_connection
    recipe = get_recipe(connection, "recipe-calathea")
    manager, executor = local_manager(connection)
    first = manager.create(
        SimulationCreate(recipe_id=recipe.id, duration_seconds=7 * 86400),
        recipe,
    )
    with pytest.raises(SimulationBusy):
        manager.create(
            SimulationCreate(recipe_id=recipe.id, duration_seconds=7 * 86400),
            recipe,
        )
    manager.cancel_or_discard(first.id)
    function, args = executor.calls[0]
    function(*args)
    assert manager.get(first.id).status.value == "cancelled"


def test_greenhouse_batch_simulates_every_zone_with_an_assigned_recipe(
    client_and_connection,
) -> None:
    """"Simula l'intera serra" (nessuna recipe_id nel POST): un job solo,
    un settore-mese solo (il tempo passa uguale per tutti), un elemento del
    risultato per ciascun settore con una ricetta assegnata."""
    client, _ = client_and_connection
    assert client.post(
        "/zones",
        json={
            "id": "r1-s1",
            "name": "Tropicali - Settore 1",
            "department_number": 1,
            "sector_number": 1,
            "plant_species": "Calathea",
            "active_recipe_id": "recipe-calathea",
        },
    ).status_code == 201
    assert client.post(
        "/zones",
        json={
            "id": "r2-s1",
            "name": "Fiore - Settore 1",
            "department_number": 2,
            "sector_number": 1,
            "plant_species": "Anturio",
            "active_recipe_id": "recipe-anturio",
        },
    ).status_code == 201
    # Un settore senza ricetta assegnata: non deve comparire nel risultato.
    assert client.post(
        "/zones",
        json={
            "id": "r2-s2",
            "name": "Fiore - Settore 2",
            "department_number": 2,
            "sector_number": 2,
            "plant_species": "Ibisco",
        },
    ).status_code == 201

    created = client.post("/simulations", json={"duration_seconds": 7 * 86400})
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["recipe_id"] is None
    assert sorted(created.json()["zone_ids"]) == ["r1-s1", "r2-s1"]

    job, result = wait_for_result(client, job_id)
    assert job["progress_percent"] == 100.0
    assert isinstance(result, list)
    assert sorted(preview["zone_id"] for preview in result) == ["r1-s1", "r2-s1"]
    by_zone = {preview["zone_id"]: preview for preview in result}
    assert by_zone["r1-s1"]["recipe"]["id"] == "recipe-calathea"
    assert by_zone["r2-s1"]["recipe"]["id"] == "recipe-anturio"
    for preview in result:
        assert preview["non_operational"] is True
        assert preview["summary"]["control_cycles"] == 7 * 24 * 4


def test_greenhouse_batch_rejects_when_no_zone_has_a_recipe(
    client_and_connection,
) -> None:
    client, _ = client_and_connection
    response = client.post("/simulations", json={"duration_seconds": 7 * 86400})
    assert response.status_code == 409
    assert "nothing to simulate" in response.json()["detail"]


def test_batch_timeout_is_reported_as_failure(
    client_and_connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, connection = client_and_connection
    recipe = get_recipe(connection, "recipe-calathea")
    manager, executor = local_manager(connection)

    class FakeProcess:
        stderr = iter(["PROGRESS 1/672\n"])

        def wait(self, timeout):
            return 0

        def terminate(self):
            return None

    monkeypatch.setattr(manager_module, "edge_is_ready", lambda executable: True)
    monkeypatch.setattr(manager_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(manager_module, "BATCH_TIMEOUT_SECONDS", -1.0)
    job = manager.create(
        SimulationCreate(recipe_id=recipe.id, duration_seconds=7 * 86400),
        recipe,
    )
    function, args = executor.calls[0]
    function(*args)
    failed = manager.get(job.id)
    assert failed.status.value == "failed"
    assert "exceeded" in failed.error
