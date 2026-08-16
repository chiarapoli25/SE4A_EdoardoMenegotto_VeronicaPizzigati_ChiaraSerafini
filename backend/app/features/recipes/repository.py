"""@file repository.py
@brief Persistenza SQLite delle ricette versionate.
"""

import sqlite3

from .models import Recipe


class RecipeVersionConflict(Exception):
    """@brief Segnala una versione non maggiore di quella gia salvata."""


def save_recipe(connection: sqlite3.Connection, recipe: Recipe) -> None:
    """@brief Inserisce una nuova versione di una ricetta.

    @details Ogni versione viene conservata come riga a se stante (chiave
    primaria `(id, version)`): una versione confermata da una coltivazione
    resta quindi leggibile anche dopo la pubblicazione di versioni
    successive.

    @param connection Connessione SQLite sulla quale salvare.
    @param recipe Ricetta validata da serializzare.
    @throws RecipeVersionConflict Se la versione non cresce.
    """
    row = connection.execute(
        "SELECT MAX(version) FROM recipes WHERE id = ?", (recipe.id,)
    ).fetchone()
    current_version = row[0] if row is not None else None
    if current_version is not None and recipe.version <= current_version:
        raise RecipeVersionConflict(
            f"recipe {recipe.id!r} version {recipe.version} must be greater "
            f"than the stored version {current_version}"
        )

    connection.execute(
        """
        INSERT INTO recipes (id, version, data, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (recipe.id, recipe.version, recipe.model_dump_json()),
    )
    connection.commit()


def get_recipe(
    connection: sqlite3.Connection,
    recipe_id: str,
    version: int | None = None,
) -> Recipe | None:
    """@brief Recupera una ricetta tramite identificativo.

    @param connection Connessione SQLite dalla quale leggere.
    @param recipe_id Identificativo univoco della ricetta.
    @param version Versione esatta richiesta, oppure `None` per l'ultima
        versione disponibile.
    @return Ricetta deserializzata, oppure `None`.
    """
    if version is None:
        row = connection.execute(
            """
            SELECT data FROM recipes
            WHERE id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (recipe_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT data FROM recipes WHERE id = ? AND version = ?",
            (recipe_id, version),
        ).fetchone()
    if row is None:
        return None
    return Recipe.model_validate_json(row[0])


def list_recipes(connection: sqlite3.Connection) -> list[Recipe]:
<<<<<<< HEAD
    """Elenca l'ultima versione di ogni ricetta, ordinate per identificativo."""
=======
    """Elenca tutte le ricette SQLite ordinate per identificativo."""
>>>>>>> 9739fd89a2d793974daf9df49e585172e2fac6ec
    rows = connection.execute(
        """
        SELECT data FROM recipes
        WHERE (id, version) IN (
            SELECT id, MAX(version) FROM recipes GROUP BY id
        )
        ORDER BY id
        """
    ).fetchall()
    return [Recipe.model_validate_json(row[0]) for row in rows]
