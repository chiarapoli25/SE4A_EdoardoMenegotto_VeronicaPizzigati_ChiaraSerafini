import sqlite3
from pathlib import Path

import pytest

from backend.app.database import (
    RecipeVersionConflict,
    get_connection,
    get_recipe,
    init_db,
    save_recipe,
)
from backend.app.models import Recipe


@pytest.fixture()
def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_save_and_get_recipe_round_trip(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    save_recipe(connection, example_recipe)

    stored = get_recipe(connection, example_recipe.id)

    assert stored == example_recipe


def test_get_missing_recipe_returns_none(connection: sqlite3.Connection) -> None:
    assert get_recipe(connection, "does-not-exist") is None


def test_save_recipe_upserts_on_higher_version(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    save_recipe(connection, example_recipe)
    updated = example_recipe.model_copy(
        update={"version": example_recipe.version + 1})

    save_recipe(connection, updated)

    assert get_recipe(connection, updated.id) == updated


def test_save_recipe_rejects_non_increasing_version(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    stored = example_recipe.model_copy(update={"version": 2})
    save_recipe(connection, stored)

    with pytest.raises(RecipeVersionConflict):
        save_recipe(connection, stored.model_copy(update={"version": 2}))

    with pytest.raises(RecipeVersionConflict):
        save_recipe(connection, stored.model_copy(update={"version": 1}))


def test_get_connection_creates_parent_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "smarthydro.db"

    new_connection = get_connection(database_path)
    try:
        init_db(new_connection)
        assert database_path.parent.is_dir()
        assert database_path.exists()
    finally:
        new_connection.close()


def test_init_db_adds_edge_assignment_to_legacy_zones() -> None:
    legacy = sqlite3.connect(":memory:")
    legacy.execute(
        """
        CREATE TABLE zones (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department_number INTEGER NOT NULL,
            sector_number INTEGER NOT NULL,
            plant_species TEXT NOT NULL,
            status TEXT NOT NULL,
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            UNIQUE (department_number, sector_number)
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO zones (
            id, name, department_number, sector_number, plant_species,
            status, active_recipe_id, last_edge_contact, current_phase
        )
        VALUES ('legacy-zone', 'Legacy', 1, 1, 'Pomodoro',
                'offline', NULL, NULL, NULL)
        """
    )

    try:
        init_db(legacy)
        columns = {
            row[1]
            for row in legacy.execute("PRAGMA table_info(zones)").fetchall()
        }
        assignment = legacy.execute(
            "SELECT assigned_edge_id FROM zones WHERE id = 'legacy-zone'"
        ).fetchone()
    finally:
        legacy.close()

    assert "assigned_edge_id" in columns
    assert assignment == (None,)
