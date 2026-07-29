import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.edge_runner import (
    DEFAULT_EDGE_EXECUTABLE,
    EdgeExecutionFailed,
    EdgeOutputInvalid,
    EdgeTimedOut,
    EdgeUnavailable,
)
from backend.app.main import (
    app,
    get_db,
    get_edge_executable,
    get_export_directory,
)


@pytest.fixture()
def integration_context(tmp_path: Path):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)
    export_directory = tmp_path / "recipes"

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_export_directory] = lambda: export_directory
    app.dependency_overrides[get_edge_executable] = lambda: DEFAULT_EDGE_EXECUTABLE
    try:
        yield TestClient(app), export_directory
    finally:
        app.dependency_overrides.clear()
        connection.close()


def test_dashboard_and_template_are_served(
    integration_context,
) -> None:
    client, _ = integration_context

    dashboard = client.get("/dashboard/")
    template = client.get("/recipes/template")

    assert dashboard.status_code == 200
    assert "SmartHydro · Control room" in dashboard.text
    assert template.status_code == 200
    assert template.json()["plant_type"] == "Tomato"
    assert len(template.json()["controllers"]) == 6


def test_create_list_and_update_recipe_reset_confirmation(
    integration_context,
    example_recipe_data: dict,
) -> None:
    client, _ = integration_context
    example_recipe_data["controllers"][0]["confirmation_state"] = "CONFIRMED"
    example_recipe_data["controllers"][0]["confirmed_recipe_version"] = 1

    created = client.post("/recipes", json=example_recipe_data)
    listed = client.get("/recipes")

    assert created.status_code == 201
    assert created.json()["controllers"][0]["confirmation_state"] == "PENDING_CONFIRMATION"
    assert created.json()["controllers"][0]["confirmed_recipe_version"] == 0
    assert listed.json() == [{
        "id": example_recipe_data["id"],
        "plant_type": "Tomato",
        "substrate": example_recipe_data["substrate"],
        "version": 1,
        "phase_count": 2,
    }]

    updated_payload = created.json()
    updated_payload["version"] = 2
    updated_payload["phases"][0]["name"] = "VegetativeUpdated"
    updated = client.post("/recipes", json=updated_payload)

    assert updated.status_code == 201
    assert client.get(f"/recipes/{example_recipe_data['id']}").json()["version"] == 2


def test_rejects_non_tomato_recipe(
    integration_context,
    example_recipe_data: dict,
) -> None:
    client, _ = integration_context
    example_recipe_data["plant_type"] = "Basil"

    response = client.post("/recipes", json=example_recipe_data)

    assert response.status_code == 422
    assert "only 'Tomato' recipes" in response.json()["detail"]


def test_system_status_reports_real_edge(
    integration_context,
) -> None:
    client, _ = integration_context

    response = client.get("/system/status")

    assert response.status_code == 200
    assert response.json()["database"] == "ready"
    assert response.json()["edge"] == (
        "ready" if DEFAULT_EDGE_EXECUTABLE.is_file() else "unavailable"
    )


@pytest.mark.skipif(
    not DEFAULT_EDGE_EXECUTABLE.is_file(),
    reason="the Edge executable must be built for the end-to-end smoke test",
)
def test_saved_recipe_runs_on_real_edge(
    integration_context,
    example_recipe_data: dict,
) -> None:
    client, export_directory = integration_context
    assert client.post("/recipes", json=example_recipe_data).status_code == 201

    response = client.post(
        "/simulations",
        json={
            "recipe_id": example_recipe_data["id"],
            "steps": 2,
            "step_seconds": 900,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["recipe"]["id"] == example_recipe_data["id"]
    assert len(payload["steps"]) == 2
    assert len(payload["steps"][0]["decisions"]) == 6
    assert "soil_moisture_percent" in payload["steps"][0]["sensors"]
    assert "water_pump_on" in payload["steps"][0]["actuators"]["output"]
    assert (export_directory / f"{example_recipe_data['id']}.json").exists()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (EdgeUnavailable("missing Edge"), 503),
        (EdgeTimedOut("slow Edge"), 504),
        (EdgeExecutionFailed("invalid recipe"), 422),
        (EdgeOutputInvalid("invalid output"), 502),
    ],
)
def test_simulation_maps_edge_failures_to_http_status(
    integration_context,
    example_recipe_data: dict,
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    expected_status: int,
) -> None:
    client, _ = integration_context
    assert client.post("/recipes", json=example_recipe_data).status_code == 201

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("backend.app.main.run_edge_simulation", fail)
    response = client.post(
        "/simulations",
        json={"recipe_id": example_recipe_data["id"], "steps": 1, "step_seconds": 900},
    )

    assert response.status_code == expected_status
