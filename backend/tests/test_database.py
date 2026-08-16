import sqlite3
from pathlib import Path

import pytest

from backend.app.database import (
    RecipeVersionConflict,
    get_connection,
    get_recipe,
    init_db,
    list_recipes,
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


def test_save_recipe_keeps_previous_versions_readable(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    save_recipe(connection, example_recipe)
    updated = example_recipe.model_copy(
        update={"version": example_recipe.version + 1})
    save_recipe(connection, updated)

    assert get_recipe(
        connection, example_recipe.id, example_recipe.version
    ) == example_recipe
    assert get_recipe(connection, updated.id, updated.version) == updated
    assert get_recipe(connection, updated.id) == updated


def test_list_recipes_returns_only_the_latest_version_per_id(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    save_recipe(connection, example_recipe)
    updated = example_recipe.model_copy(
        update={"version": example_recipe.version + 1})
    save_recipe(connection, updated)

    recipes = list_recipes(connection)

    assert [recipe.version for recipe in recipes if recipe.id == updated.id] == [
        updated.version
    ]


def test_get_connection_creates_parent_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "smarthydro.db"

    new_connection = get_connection(database_path)
    try:
        init_db(new_connection)
        assert database_path.parent.is_dir()
        assert database_path.exists()
    finally:
        new_connection.close()
