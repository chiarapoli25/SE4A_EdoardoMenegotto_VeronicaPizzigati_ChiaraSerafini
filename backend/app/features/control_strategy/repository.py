"""@file repository.py
@brief Persistenza SQLite della Strategy di controllo a livello di impianto.
"""

import sqlite3
from datetime import datetime, timezone

from ..recipes.models import ControlledVariable, StrategyType, _required_default_strategy


def seed_defaults(connection: sqlite3.Connection) -> None:
    """@brief Inserisce, se assenti, i default richiesti per ogni variabile.

    @details A differenza del reseed versionato del catalogo ricette
    (`seed_recipe_catalog`), questo non sovrascrive mai una riga gia'
    presente: una volta che un Amministratore ha scelto una Strategy dalla
    pagina Controllo, quella scelta sopravvive ai riavvii del backend.
    """
    now = datetime.now(timezone.utc).isoformat()
    for variable in ControlledVariable:
        connection.execute(
            """
            INSERT OR IGNORE INTO control_strategy_settings
                (variable, selected_strategy, updated_at)
            VALUES (?, ?, ?)
            """,
            (variable.value, _required_default_strategy(variable).value, now),
        )
    connection.commit()


def get_all(
    connection: sqlite3.Connection,
) -> dict[ControlledVariable, StrategyType]:
    """@brief Legge la Strategy corrente per ciascuna delle sei variabili.

    @details Una variabile assente dalla tabella (un DB creato prima di
    questa tabella, prima che `seed_defaults` giri) ricade sul proprio
    default richiesto, cosi' la lettura resta sempre totale sulle sei
    variabili.
    """
    rows = connection.execute(
        "SELECT variable, selected_strategy FROM control_strategy_settings"
    ).fetchall()
    stored = {ControlledVariable(row[0]): StrategyType(row[1]) for row in rows}
    return {
        variable: stored.get(variable, _required_default_strategy(variable))
        for variable in ControlledVariable
    }


def set_strategy(
    connection: sqlite3.Connection,
    variable: ControlledVariable,
    strategy: StrategyType,
) -> None:
    """@brief Imposta la Strategy di una variabile per l'intero impianto."""
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO control_strategy_settings (variable, selected_strategy, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(variable) DO UPDATE SET
            selected_strategy = excluded.selected_strategy,
            updated_at = excluded.updated_at
        """,
        (variable.value, strategy.value, now),
    )
    connection.commit()
