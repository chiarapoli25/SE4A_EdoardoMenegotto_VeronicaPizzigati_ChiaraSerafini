from pathlib import Path

import pytest

from backend.app.models import Recipe
from backend.app.recipe_export import (
    UnsafeRecipeId,
    assert_safe_recipe_id,
    export_recipe_for_edge,
)


def test_export_recipe_writes_json_file(tmp_path: Path, example_recipe: Recipe) -> None:
    path = export_recipe_for_edge(example_recipe, tmp_path)

    assert path == tmp_path / f"{example_recipe.id}.json"
    assert Recipe.model_validate_json(path.read_text()) == example_recipe


def test_export_recipe_creates_missing_directory(
    tmp_path: Path, example_recipe: Recipe
) -> None:
    directory = tmp_path / "nested" / "recipes"

    path = export_recipe_for_edge(example_recipe, directory)

    assert path.exists()


@pytest.mark.parametrize(
    "malicious_id", ["../escape", "a/b", "..\\escape", "/etc/passwd"])
def test_export_recipe_rejects_path_separators(
    tmp_path: Path, example_recipe: Recipe, malicious_id: str
) -> None:
    malicious_recipe = example_recipe.model_copy(update={"id": malicious_id})

    with pytest.raises(UnsafeRecipeId):
        export_recipe_for_edge(malicious_recipe, tmp_path)

    assert list(tmp_path.rglob("*.json")) == []


def test_assert_safe_recipe_id_does_not_write_anything(tmp_path: Path) -> None:
    assert_safe_recipe_id("safe-id", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_assert_safe_recipe_id_rejects_path_separator(tmp_path: Path) -> None:
    with pytest.raises(UnsafeRecipeId):
        assert_safe_recipe_id("../escape", tmp_path)
