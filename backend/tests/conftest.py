import json
from pathlib import Path

import pytest

from backend.app.models import Recipe

EXAMPLE_RECIPE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "example_recipe.json"
)


@pytest.fixture()
def example_recipe_data() -> dict:
    return json.loads(EXAMPLE_RECIPE_PATH.read_text())


@pytest.fixture()
def example_recipe(example_recipe_data: dict) -> Recipe:
    return Recipe.model_validate(example_recipe_data)
