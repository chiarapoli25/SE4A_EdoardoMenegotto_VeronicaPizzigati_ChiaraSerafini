"""@file test_control_strategy.py
@brief Verifica della Strategy di controllo come impostazione globale d'impianto.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.database import init_db
from backend.app.features.users.repository import seed_default_users
from backend.app.main import app, get_db


@pytest.fixture()
def client() -> TestClient:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(connection)
    # init_db() non semina piu' automaticamente gli account (vedi
    # database.py): lo facciamo esplicitamente qui, come test_users.py, per
    # poter loggare come admin/pass123 seminato.
    seed_default_users(connection)

    def override_get_db():
        yield connection

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _login(client: TestClient, username: str, password: str = "pass123") -> str:
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_defaults_match_required_strategy_per_variable(client: TestClient) -> None:
    response = client.get(
        "/control-strategy", headers=_auth_headers(_login(client, "admin"))
    )
    assert response.status_code == 200
    by_variable = {
        row["variable"]: row["selected_strategy"] for row in response.json()
    }
    assert by_variable == {
        "soil_moisture": "Threshold",
        "light": "Threshold",
        "ph": "PID",
        "nitrogen": "Predictive",
        "phosphorus": "Predictive",
        "potassium": "Predictive",
    }


def test_read_requires_a_session(client: TestClient) -> None:
    assert client.get("/control-strategy").status_code == 401


def test_only_admin_can_change_it(client: TestClient) -> None:
    token = _login(client, "agronomo")

    response = client.put(
        "/control-strategy/soil_moisture",
        json={"selected_strategy": "PID"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 403


def test_admin_change_persists_and_is_reflected_on_recipes(
    client: TestClient,
) -> None:
    token = _login(client, "admin")

    response = client.put(
        "/control-strategy/soil_moisture",
        json={"selected_strategy": "PID"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "variable": "soil_moisture",
        "selected_strategy": "PID",
    }

    # Una ricetta il cui controller "soil_moisture" era stato seedato come
    # Threshold torna ora con PID: la Strategy non e' piu' letta dalla
    # ricetta, ma dall'impostazione globale appena cambiata (vedi
    # recipes/repository.py::_stamp_global_strategy).
    recipe = client.get(
        "/recipes/recipe-calathea", headers=_auth_headers(token)
    ).json()
    controller = next(
        c for c in recipe["controllers"] if c["variable"] == "soil_moisture"
    )
    assert controller["selected_strategy"] == "PID"
    assert set(controller["parameters"]) == {
        "setpoint",
        "proportional_gain",
        "integral_gain",
        "derivative_gain",
        "command_minimum",
        "command_maximum",
        "direction",
    }

    # Un'altra variabile non toccata dal cambio resta al proprio default.
    ph_controller = next(c for c in recipe["controllers"] if c["variable"] == "ph")
    assert ph_controller["selected_strategy"] == "PID"


def test_admin_change_pushes_a_live_command_to_active_zones_only(
    client: TestClient,
) -> None:
    assert (
        client.post(
            "/zones",
            json={
                "id": "r1-s1",
                "name": "Tropicali - Settore 1",
                "department_number": 1,
                "sector_number": 1,
                "plant_species": "Calathea",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/cultivations",
            json={"zone_id": "r1-s1", "recipe_id": "recipe-calathea"},
        ).status_code
        == 201
    )

    token = _login(client, "admin")
    response = client.put(
        "/control-strategy/soil_moisture",
        json={"selected_strategy": "PID"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200

    commands = client.get("/zones/r1-s1/commands").json()
    change_strategy = [c for c in commands if c["command_type"] == "ChangeStrategy"]
    confirmations = [
        c for c in commands if c["command_type"] == "ConfirmConfiguration"
    ]

    assert len(change_strategy) == 1
    assert change_strategy[0]["payload"]["variable"] == "soil_moisture"
    assert change_strategy[0]["payload"]["strategy"] == "PID"
    assert len(confirmations) == 1
    assert confirmations[0]["payload"]["variable"] == "soil_moisture"
