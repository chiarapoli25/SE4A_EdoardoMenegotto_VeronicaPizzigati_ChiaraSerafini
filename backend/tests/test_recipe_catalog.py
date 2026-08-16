import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.app.core.database import init_db
from backend.app.features.recipes.catalog import (
    DEFAULT_CATALOG_PATH,
    load_recipe_catalog,
    seed_recipe_catalog,
)
from backend.app.features.recipes.repository import get_recipe


def test_catalog_contains_one_json_file_for_each_recipe() -> None:
    recipe_files = sorted((DEFAULT_CATALOG_PATH / "recipes").glob("*.json"))
    recipes = load_recipe_catalog()

    assert len(recipe_files) == 20
    assert len(recipes) == len(recipe_files)
    assert {recipe.id for recipe in recipes} == {
        f"recipe-{path.stem}" for path in recipe_files
    }


def test_adding_a_recipe_file_requires_no_python_change(tmp_path: Path) -> None:
    catalog_directory = tmp_path / "recipe_catalog"
    shutil.copytree(DEFAULT_CATALOG_PATH, catalog_directory)
    source_path = catalog_directory / "recipes" / "calathea.json"
    new_recipe = json.loads(source_path.read_text(encoding="utf-8"))
    new_recipe["id"] = "recipe-calathea-test"
    new_recipe["plant_type"] = "Calathea Test"
    (catalog_directory / "recipes" / "calathea-test.json").write_text(
        json.dumps(new_recipe),
        encoding="utf-8",
    )

    recipes = load_recipe_catalog(catalog_directory)

    assert len(recipes) == 21
    assert "recipe-calathea-test" in {recipe.id for recipe in recipes}

    connection = sqlite3.connect(":memory:")
    try:
        init_db(connection)
        assert seed_recipe_catalog(connection, catalog_directory) == 1
        connection.commit()
        stored = get_recipe(connection, "recipe-calathea-test")
    finally:
        connection.close()

    assert stored is not None
    assert stored.plant_type == "Calathea Test"


def test_invalid_recipe_error_identifies_its_file(tmp_path: Path) -> None:
    catalog_directory = tmp_path / "recipe_catalog"
    shutil.copytree(DEFAULT_CATALOG_PATH, catalog_directory)
    invalid_path = catalog_directory / "recipes" / "calathea.json"
    invalid_path.write_text('{"id": "incomplete"}', encoding="utf-8")

    with pytest.raises(ValueError, match=r"calathea\.json"):
        load_recipe_catalog(catalog_directory)


def test_unknown_shared_profile_identifies_the_recipe(tmp_path: Path) -> None:
    catalog_directory = tmp_path / "recipe_catalog"
    shutil.copytree(DEFAULT_CATALOG_PATH, catalog_directory)
    recipe_path = catalog_directory / "recipes" / "calathea.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    recipe["light_profile"] = "missing-profile"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")

    with pytest.raises(ValueError, match=r"recipe-calathea.*missing-profile"):
        load_recipe_catalog(catalog_directory)
