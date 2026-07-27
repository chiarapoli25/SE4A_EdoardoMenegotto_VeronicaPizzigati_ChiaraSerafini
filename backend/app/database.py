"""Persistenza SQLite delle ricette.

Una riga per ricetta, indicizzata dal suo ``id``: l'intera ricetta e
serializzata cosi come arriva da Pydantic in un'unica colonna di testo
JSON, senza tabelle normalizzate per fasi o controllori. ``version`` e
duplicata in una colonna propria solo per poter rifiutare un salvataggio
con versione non crescente senza dover prima deserializzare la colonna
JSON, rispecchiando il vincolo di
``RecipeControlSystem::replace_recipe`` lato Edge (versione di
sostituzione strettamente maggiore di quella corrente).
"""

import sqlite3
from pathlib import Path

from app.models import Recipe

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "smarthydro.db"


class RecipeVersionConflict(Exception):
    """La versione ricevuta non e maggiore di quella gia salvata."""


def get_connection(database_path: Path | str = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    """Apre una connessione al database, creando la cartella se serve."""
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(database_path)


def init_db(connection: sqlite3.Connection) -> None:
    """Crea la tabella ``recipes`` se non esiste gia."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def save_recipe(connection: sqlite3.Connection, recipe: Recipe) -> None:
    """Inserisce o aggiorna la ricetta, rifiutando versioni non crescenti.

    @throws RecipeVersionConflict Se esiste gia una ricetta con lo stesso
        id e versione maggiore o uguale a quella ricevuta.
    """
    row = connection.execute(
        "SELECT version FROM recipes WHERE id = ?", (recipe.id,)
    ).fetchone()
    if row is not None and recipe.version <= row[0]:
        raise RecipeVersionConflict(
            f"recipe {recipe.id!r} version {recipe.version} must be greater "
            f"than the stored version {row[0]}")

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
    """Restituisce la ricetta salvata con questo id, o None se assente."""
    row = connection.execute(
        "SELECT data FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if row is None:
        return None
    return Recipe.model_validate_json(row[0])
