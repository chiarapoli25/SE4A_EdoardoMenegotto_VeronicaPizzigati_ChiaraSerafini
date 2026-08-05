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
