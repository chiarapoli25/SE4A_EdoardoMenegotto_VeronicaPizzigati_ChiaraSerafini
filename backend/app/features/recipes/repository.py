"""@file repository.py
@brief Persistenza SQLite delle ricette versionate.
"""

import sqlite3

from ..control_strategy.repository import get_all as get_control_strategy_settings
from .models import Recipe
from .parameters import default_parameters_for


class RecipeVersionConflict(Exception):
    """@brief Segnala una versione non maggiore di quella gia salvata."""


def _stamp_global_strategy(connection: sqlite3.Connection, recipe: Recipe) -> Recipe:
    """@brief Sovrascrive Strategy e parametri con l'impostazione globale.

    @details La Strategy non e' piu' una caratteristica della singola
    ricetta (vedi ../control_strategy/): questo e' l'unico punto in cui una
    `Recipe` viene letta dal database, quindi basta ristampare qui perche'
    ogni consumatore — le rotte GET, la ricetta imbustata nei comandi zona,
    il Simulatore batch — veda sempre la scelta corrente, anche per ricette
    salvate prima dell'ultimo cambio. Setpoint e range restano quelli della
    ricetta (variano per reparto/specie); solo Strategy e i suoi parametri
    derivati diventano globali, usando il target della prima fase come
    riferimento — la stessa convenzione gia' usata in fase di creazione
    ricetta sia da `catalog.py` sia dalla dashboard.
    """
    settings = get_control_strategy_settings(connection)
    first_phase_targets = {
        target.variable: target for target in recipe.phases[0].targets
    }
    for controller in recipe.controllers:
        strategy = settings[controller.variable]
        target = first_phase_targets.get(controller.variable)
        controller.selected_strategy = strategy
        controller.parameters = default_parameters_for(
            strategy, controller.variable, target
        )
    return recipe


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
    return _stamp_global_strategy(connection, Recipe.model_validate_json(row[0]))


def list_recipes(connection: sqlite3.Connection) -> list[Recipe]:
    """Elenca tutte le ricette SQLite ordinate per identificativo."""
    rows = connection.execute(
        "SELECT data FROM recipes ORDER BY id"
    ).fetchall()
    return [
        _stamp_global_strategy(connection, Recipe.model_validate_json(row[0]))
        for row in rows
    ]
