import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.main import app, get_db


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIRECTORY = PROJECT_ROOT / "dashboard"


@pytest.fixture()
def integration_client() -> TestClient:
    """Espone il backend autorevole su un database isolato in memoria."""
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


def test_dashboard_is_a_standalone_static_bundle() -> None:
    """La dashboard resta separata dal processo FastAPI, come da README."""
    index = (DASHBOARD_DIRECTORY / "index.html").read_text(encoding="utf-8")

    assert "SmartHydro · Control room" in index
    assert (DASHBOARD_DIRECTORY / "script.js").is_file()
    assert (DASHBOARD_DIRECTORY / "styles.css").is_file()


def test_dashboard_is_served_by_fastapi(integration_client: TestClient) -> None:
    """Lo script demo espone la control room e i suoi asset dalla stessa origine."""
    dashboard = integration_client.get("/dashboard/")
    script = integration_client.get("/dashboard/script.js")
    stylesheet = integration_client.get("/dashboard/styles.css")

    assert dashboard.status_code == 200
    assert "SmartHydro · Control room" in dashboard.text
    assert script.status_code == 200
    assert "Priorità agronomiche" in script.text
    assert stylesheet.status_code == 200


def test_local_file_dashboard_can_reach_the_backend(
    integration_client: TestClient,
) -> None:
    """L'origine opaca dei file locali può interrogare il backend demo."""
    response = integration_client.options(
        "/recipes",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "null"
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert integration_client.get(
        "/recipes", headers={"Origin": "null"}
    ).headers["access-control-allow-origin"] == "null"


def test_dashboard_uses_current_backend_contracts() -> None:
    """Verifica i contratti agronomici, i grafici e il controllo temporale."""
    script = (DASHBOARD_DIRECTORY / "script.js").read_text(encoding="utf-8")
    index = (DASHBOARD_DIRECTORY / "index.html").read_text(encoding="utf-8")

    assert 'number: 5, name: "Quarantena"' in script
    assert 'request("/health")' in script
    assert 'request("/plants?limit=1000")' in script
    assert 'request("/recipes/template")' not in script
    assert 'request("/simulations", { method: "POST"' in script
    assert 'window.location.protocol === "file:"' in script
    assert 'DEFAULT_LOCAL_BACKEND = "http://127.0.0.1:8000"' in script
    assert 'data-configure-sector=' in script
    assert 'data-assign-zone-recipe=' in script
    assert 'request("/cultivations", { method: "POST"' in script
    assert '/telemetry?limit=1000' in script
    assert '/actuators?limit=1000' in script
    assert 'Andamento dei sensori' in script
    assert 'Attività degli attuatori' in script
    assert 'id="time-dialog"' in index
    assert 'Scenario simulato — non operativo' in index
    assert 'SetSimulationSpeed' in script
    assert 'SetSimulationDuration' in script
    assert 'route?.name === "recipe"' in script
    assert 'data-chart-window="6h"' in script
    assert 'Mostra dati tabellari' in script
    assert 'id="sector-edge"' not in index
    assert 'request("/zones", { method: "POST"' in script
    assert 'id="sector-dialog"' in index
    assert "Decisioni agronomiche" in script
    assert 'data-view="serra"' in index
    assert 'heading.textContent = "Piantina della serra"' in script
    assert 'data-go-to="serra"' in script


def test_sector_configuration_contract_accepts_species_and_compatible_recipe(
    integration_client: TestClient,
) -> None:
    """Il flusso aperto dal click crea un settore valido per backend ed Edge."""
    recipes = integration_client.get("/recipes").json()
    recipe = next(item for item in recipes if item["id"] == "recipe-calathea")
    payload = {
        "id": "r1-s1",
        "name": "Piante Tropicali e da Fogliame - Settore 1",
        "department_number": 1,
        "sector_number": 1,
        "plant_species": recipe["plant_type"],
        "active_recipe_id": recipe["id"],
    }

    created = integration_client.post("/zones", json=payload)

    assert created.status_code == 201
    assert created.json()["plant_species"] == "Calathea"
    assert created.json()["active_recipe_id"] == "recipe-calathea"
    assert created.json()["assigned_edge_id"] == "smarthydro-edge"


def test_dashboard_recipe_catalog_uses_the_complete_runtime_contract(
    integration_client: TestClient,
) -> None:
    """Il catalogo restituisce ricette SQLite complete, non vecchi summary."""
    response = integration_client.get("/recipes")

    assert response.status_code == 200
    recipes = response.json()
    catalog = [recipe for recipe in recipes if recipe["id"].startswith("recipe-")]
    assert len(catalog) == 20
    assert {recipe["department_number"] for recipe in catalog} == {1, 2, 3, 4}
    assert len({recipe["plant_type"] for recipe in catalog}) == 20

    calathea = next(recipe for recipe in catalog if recipe["id"] == "recipe-calathea")
    assert calathea["department_name"] == "Piante Tropicali e da Fogliame"
    assert len(calathea["phases"]) == 4
    assert len(calathea["controllers"]) == 6


def test_dashboard_can_create_and_version_a_custom_recipe(
    integration_client: TestClient,
    example_recipe_data: dict,
) -> None:
    """Creazione e aggiornamento usano il versionamento autorevole di main."""
    created = integration_client.post("/recipes", json=example_recipe_data)

    assert created.status_code == 201
    assert integration_client.post(
        "/recipes", json=example_recipe_data
    ).status_code == 409

    updated_payload = json.loads(json.dumps(created.json()))
    updated_payload["version"] += 1
    updated_payload["phases"][0]["name"] = "VegetativeUpdated"
    updated = integration_client.post("/recipes", json=updated_payload)

    assert updated.status_code == 201
    persisted = integration_client.get(
        f"/recipes/{example_recipe_data['id']}"
    )
    assert persisted.status_code == 200
    assert persisted.json() == updated.json()


def test_dashboard_recipe_api_is_not_limited_to_tomato(
    integration_client: TestClient,
    example_recipe_data: dict,
) -> None:
    """Il dominio corrente accetta ricette personalizzate multispecie."""
    example_recipe_data["id"] = "basil-custom"
    example_recipe_data["plant_type"] = "Basil"

    response = integration_client.post("/recipes", json=example_recipe_data)

    assert response.status_code == 201
    assert response.json()["plant_type"] == "Basil"
