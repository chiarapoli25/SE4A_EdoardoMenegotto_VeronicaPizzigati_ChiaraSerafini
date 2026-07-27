import json
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
    # Connessione in memoria condivisa fra le richieste: una nuova per
    # ognuna, come farebbe get_db(), perderebbe i dati scritti dalle altre.
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


def test_create_recipe_then_read_it_back(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_response = client.post("/recipes", json=example_recipe_data)
    assert create_response.status_code == 201

    read_response = client.get(f"/recipes/{example_recipe_data['id']}")
    assert read_response.status_code == 200
    assert read_response.json() == create_response.json()


def test_create_recipe_exports_json_file_for_edge(
    client: TestClient, example_recipe_data: dict, export_directory: Path
) -> None:
    response = client.post("/recipes", json=example_recipe_data)

    exported_path = export_directory / f"{example_recipe_data['id']}.json"
    assert exported_path.exists()
    assert json.loads(exported_path.read_text()) == response.json()


def test_create_recipe_rejects_non_increasing_version(
    client: TestClient, example_recipe_data: dict
) -> None:
    assert client.post("/recipes", json=example_recipe_data).status_code == 201

    same_version_again = client.post("/recipes", json=example_recipe_data)

    assert same_version_again.status_code == 409


def test_version_conflict_does_not_touch_exported_file(
    client: TestClient, example_recipe_data: dict, export_directory: Path
) -> None:
    client.post("/recipes", json=example_recipe_data)
    exported_path = export_directory / f"{example_recipe_data['id']}.json"
    written_after_first_save = exported_path.read_text()

    assert client.post("/recipes", json=example_recipe_data).status_code == 409

    assert exported_path.read_text() == written_after_first_save


def test_read_missing_recipe_returns_404(client: TestClient) -> None:
    response = client.get("/recipes/does-not-exist")

    assert response.status_code == 404


def test_create_recipe_rejects_invalid_payload(client: TestClient) -> None:
    response = client.post("/recipes", json={"id": "incomplete"})

    assert response.status_code == 422


def test_create_recipe_rejects_unsafe_id(
    client: TestClient, example_recipe_data: dict, export_directory: Path
) -> None:
    unsafe_recipe = {**example_recipe_data, "id": "../escape"}

    response = client.post("/recipes", json=unsafe_recipe)

    assert response.status_code == 400
    assert not export_directory.exists() or list(export_directory.iterdir()) == []
