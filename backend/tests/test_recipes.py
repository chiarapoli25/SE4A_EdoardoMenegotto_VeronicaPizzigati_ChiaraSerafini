import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.main import app, get_db
from backend.app.models import ControlledVariable


@pytest.fixture()
def client() -> TestClient:
    # Connessione in memoria condivisa fra le richieste: una nuova per
    # ognuna, come farebbe get_db(), perderebbe i dati scritti dalle altre.
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


def test_create_recipe_then_read_it_back(
    client: TestClient, example_recipe_data: dict
) -> None:
    create_response = client.post("/recipes", json=example_recipe_data)
    assert create_response.status_code == 201

    read_response = client.get(f"/recipes/{example_recipe_data['id']}")
    assert read_response.status_code == 200
    assert read_response.json() == create_response.json()


def test_legacy_sensor_field_is_accepted_but_response_is_canonical(
    client: TestClient, example_recipe_data: dict
) -> None:
    legacy_recipe = json.loads(json.dumps(example_recipe_data))
    for controller in legacy_recipe["controllers"]:
        controller["sensor"] = controller.pop("input_source")

    response = client.post("/recipes", json=legacy_recipe)

    assert response.status_code == 201
    for controller in response.json()["controllers"]:
        assert "input_source" in controller
        assert "sensor" not in controller


def test_air_conditions_are_not_controlled_variables() -> None:
    controlled = {variable.value for variable in ControlledVariable}

    assert controlled == {
        "soil_moisture",
        "light",
        "ph",
        "nitrogen",
        "phosphorus",
        "potassium",
    }
    assert "temperature" not in controlled
    assert "air_humidity" not in controlled


def test_create_recipe_rejects_non_increasing_version(
    client: TestClient, example_recipe_data: dict
) -> None:
    assert client.post("/recipes", json=example_recipe_data).status_code == 201

    same_version_again = client.post("/recipes", json=example_recipe_data)

    assert same_version_again.status_code == 409


def test_read_missing_recipe_returns_404(client: TestClient) -> None:
    response = client.get("/recipes/does-not-exist")

    assert response.status_code == 404


def test_seeded_catalog_contains_five_recipes_for_each_department(
    client: TestClient,
) -> None:
    response = client.get("/recipes")

    assert response.status_code == 200
    catalog_recipes = [
        recipe for recipe in response.json()
        if recipe["id"].startswith("recipe-")
    ]
    assert len(catalog_recipes) == 20
    assert {
        department: sum(
            recipe["department_number"] == department
            for recipe in catalog_recipes
        )
        for department in range(1, 5)
    } == {1: 5, 2: 5, 3: 5, 4: 5}
    assert {
        department: {
            recipe["plant_type"]
            for recipe in catalog_recipes
            if recipe["department_number"] == department
        }
        for department in range(1, 5)
    } == {
        1: {
            "Pothos (Epipremnum)",
            "Monstera Deliciosa",
            "Ficus Lyrata",
            "Calathea",
            "Sansevieria",
        },
        2: {
            "Orchidea (Phalaenopsis)",
            "Spatifillo",
            "Anturio",
            "Ibisco",
            "Violetta Africana",
        },
        3: {
            "Aloe Vera",
            "Echeveria",
            "Cactus di Natale",
            "Albero di Giada",
            "Lithops",
        },
        4: {
            "Limone (vaso)",
            "Pomodorino",
            "Fragola",
            "Peperoncino",
            "Kumquat",
        },
    }


def test_catalog_recipe_preserves_source_care_profile(client: TestClient) -> None:
    response = client.get("/recipes/recipe-calathea")

    assert response.status_code == 200
    assert response.json()["plant_type"] == "Calathea"
    assert response.json()["department_name"] == (
        "Piante Tropicali e da Fogliame"
    )
    assert response.json()["care_profile"] == {
        "light": "Bassa/Media",
        "watering": "Costante, leggermente umido",
        "temperature": "Umidità >60%",
        "fertilization": "Mensile diluito",
    }
    assert [phase["name"] for phase in response.json()["phases"]] == [
        "Avvio e attecchimento",
        "Crescita vegetativa",
        "Mantenimento fogliare",
        "Riposo vegetativo",
    ]
    assert len(response.json()["phases"][0]["targets"]) == 6
    assert len(response.json()["controllers"]) == 6


@pytest.mark.parametrize(
    ("recipe_id", "expected_phase"),
    [
        ("recipe-calathea", "Mantenimento fogliare"),
        ("recipe-orchidea-phalaenopsis", "Fioritura"),
        ("recipe-lithops", "Maturazione"),
        ("recipe-pomodorino", "Produzione e maturazione"),
    ],
)
def test_catalog_uses_category_specific_phase_sequences(
    client: TestClient,
    recipe_id: str,
    expected_phase: str,
) -> None:
    recipe = client.get(f"/recipes/{recipe_id}").json()

    assert recipe["version"] == 2
    assert len(recipe["phases"]) == 4
    assert recipe["phases"][0]["name"] == "Avvio e attecchimento"
    assert recipe["phases"][1]["name"] == "Crescita vegetativa"
    assert expected_phase in {phase["name"] for phase in recipe["phases"]}


def test_catalog_phases_change_duration_photoperiod_and_setpoints(
    client: TestClient,
) -> None:
    phases = client.get("/recipes/recipe-orchidea-phalaenopsis").json()[
        "phases"
    ]

    assert len({phase["duration_hours"] for phase in phases}) > 1
    assert len(
        {phase["photoperiod"]["duration_hours"] for phase in phases}
    ) > 1
    light_setpoints = [
        next(
            target["setpoint"]
            for target in phase["targets"]
            if target["variable"] == "light"
        )
        for phase in phases
    ]
    nutrient_setpoints = [
        next(
            target["setpoint"]
            for target in phase["targets"]
            if target["variable"] == "potassium"
        )
        for phase in phases
    ]
    assert len(set(light_setpoints)) > 1
    assert len(set(nutrient_setpoints)) > 1


def test_plant_recipe_can_override_a_shared_phase_profile(
    client: TestClient,
) -> None:
    phases = client.get("/recipes/recipe-pomodorino").json()["phases"]
    production = next(
        phase for phase in phases
        if phase["name"] == "Produzione e maturazione"
    )
    potassium = next(
        target for target in production["targets"]
        if target["variable"] == "potassium"
    )

    assert production["duration_hours"] == 1344
    assert potassium["setpoint"] == 351


def test_catalog_recipes_can_be_filtered_by_department(
    client: TestClient,
) -> None:
    response = client.get("/recipes", params={"department_number": 3})

    assert response.status_code == 200
    assert len(response.json()) == 5
    assert {
        recipe["plant_type"] for recipe in response.json()
    } == {
        "Aloe Vera",
        "Echeveria",
        "Cactus di Natale",
        "Albero di Giada",
        "Lithops",
    }


def test_recipe_filter_rejects_quarantine_department(client: TestClient) -> None:
    response = client.get("/recipes", params={"department_number": 5})

    assert response.status_code == 422


def test_catalog_recipe_requires_a_higher_version_to_override(
    client: TestClient,
) -> None:
    recipe = client.get("/recipes/recipe-lithops").json()

    conflict = client.post("/recipes", json=recipe)
    recipe["version"] += 1
    updated = client.post("/recipes", json=recipe)

    assert conflict.status_code == 409
    assert updated.status_code == 201
    assert updated.json()["version"] == 3


def test_create_recipe_rejects_invalid_payload(client: TestClient) -> None:
    response = client.post("/recipes", json={"id": "incomplete"})

    assert response.status_code == 422
