"""@file repository.py
@brief Persistenza SQLite delle ricette versionate.
"""

import sqlite3

from .models import Recipe


class RecipeVersionConflict(Exception):
    """@brief Segnala una versione non maggiore di quella gia salvata."""


def save_recipe(connection: sqlite3.Connection, recipe: Recipe) -> None:
    """@brief Inserisce o aggiorna una ricetta versionata.

    @param connection Connessione SQLite sulla quale salvare.
    @param recipe Ricetta validata da serializzare.
    @throws RecipeVersionConflict Se la versione non cresce.
    """
    row = connection.execute(
        "SELECT version FROM recipes WHERE id = ?", (recipe.id,)
    ).fetchone()
    if row is not None and recipe.version <= row[0]:
        raise RecipeVersionConflict(
            f"recipe {recipe.id!r} version {recipe.version} must be greater "
            f"than the stored version {row[0]}"
        )

    connection.execute(
        """
        INSERT INTO recipes (id, version, data, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            version = excluded.version,
            data = excluded.data,
            updated_at = excluded.updated_at
        """,
        (recipe.id, recipe.version, recipe.model_dump_json()),
    )
    connection.commit()


def get_recipe(connection: sqlite3.Connection, recipe_id: str) -> Recipe | None:
    """@brief Recupera una ricetta tramite identificativo.

    @param connection Connessione SQLite dalla quale leggere.
    @param recipe_id Identificativo univoco della ricetta.
    @return Ricetta deserializzata, oppure `None`.
    """
    row = connection.execute(
        "SELECT data FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if row is None:
        return None
    return Recipe.model_validate_json(row[0])


def list_recipes(connection: sqlite3.Connection) -> list[Recipe]:
    """Elenca tutte le ricette SQLite ordinate per identificativo."""
    rows = connection.execute(
        "SELECT data FROM recipes ORDER BY id"
    ).fetchall()
    return [Recipe.model_validate_json(row[0]) for row in rows]
