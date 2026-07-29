"""@file database.py
@brief Persistenza SQLite delle zone della serra e delle ricette.

@details `zones` rappresenta al massimo otto settori: quattro reparti con due
posizioni ciascuno e una sola specie per riga. Una riga di `recipes`, indicizzata
dal suo `id`, contiene invece l'intera ricetta serializzata in JSON, senza
tabelle normalizzate per fasi o controllori. `version` e duplicata in una
colonna propria per rifiutare un salvataggio con versione non crescente,
rispecchiando il vincolo di `RecipeControlSystem::replace_recipe()` lato Edge.
"""

import sqlite3
from pathlib import Path

from .models import Recipe, Zone, ZoneCreate, ZoneStatus

## @brief Percorso predefinito del database SQLite del backend.
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "smarthydro.db"


class RecipeVersionConflict(Exception):
    """@brief Segnala una versione non maggiore di quella gia salvata."""


class ZoneConflict(Exception):
    """@brief Segnala un identificativo o settore fisico gia occupato."""


def get_connection(database_path: Path | str = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    """@brief Apre una connessione al database SQLite.

    @param database_path Percorso del database oppure `:memory:` per un database
        temporaneo.
    @return Connessione SQLite aperta; il chiamante ne possiede la chiusura.
    """
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(database_path)


def init_db(connection: sqlite3.Connection) -> None:
    """@brief Crea lo schema minimo del backend se non esiste.

    @param connection Connessione SQLite sulla quale creare le tabelle
        `recipes` e `zones`.
    @return Nessun valore.
    """
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS zones (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department_number INTEGER NOT NULL
                CHECK (department_number BETWEEN 1 AND 4),
            sector_number INTEGER NOT NULL
                CHECK (sector_number BETWEEN 1 AND 2),
            plant_species TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('online', 'offline')),
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            UNIQUE (department_number, sector_number)
        )
        """
    )
    connection.commit()


def save_recipe(connection: sqlite3.Connection, recipe: Recipe) -> None:
    """@brief Inserisce o aggiorna una ricetta versionata.

    @param connection Connessione SQLite sulla quale eseguire il salvataggio.
    @param recipe Ricetta validata da serializzare come JSON.
    @return Nessun valore.
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
    """@brief Recupera una ricetta tramite il suo identificativo.

    @param connection Connessione SQLite dalla quale leggere.
    @param recipe_id Identificativo univoco da cercare.
    @return Ricetta validata e deserializzata, oppure `None` se assente.
    """
    row = connection.execute(
        "SELECT data FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if row is None:
        return None
    return Recipe.model_validate_json(row[0])


def create_zone(connection: sqlite3.Connection, zone: ZoneCreate) -> Zone:
    """@brief Registra un settore libero della serra.

    @param connection Connessione SQLite sulla quale salvare il settore.
    @param zone Identita, posizione e specie del nuovo settore.
    @return Zona persistita con stato iniziale `offline`.
    @throws ZoneConflict Se l'identificativo esiste gia o il settore fisico e
        gia occupato.
    """
    stored_zone = Zone(**zone.model_dump())
    try:
        connection.execute(
            """
            INSERT INTO zones (
                id, name, department_number, sector_number, plant_species,
                status, active_recipe_id, last_edge_contact, current_phase
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stored_zone.id,
                stored_zone.name,
                stored_zone.department_number,
                stored_zone.sector_number,
                stored_zone.plant_species,
                stored_zone.status.value,
                stored_zone.active_recipe_id,
                stored_zone.last_edge_contact,
                stored_zone.current_phase,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise ZoneConflict(
            f"zone id {zone.id!r} already exists or department "
            f"{zone.department_number} sector {zone.sector_number} is occupied"
        ) from error
    connection.commit()
    return stored_zone


def _zone_from_row(row: tuple) -> Zone:
    """@brief Converte una riga SQLite nel relativo modello di zona.

    @param row Riga ottenuta selezionando tutte le colonne pubbliche di `zones`.
    @return Zona validata da Pydantic.
    """
    return Zone(
        id=row[0],
        name=row[1],
        department_number=row[2],
        sector_number=row[3],
        plant_species=row[4],
        status=ZoneStatus(row[5]),
        active_recipe_id=row[6],
        last_edge_contact=row[7],
        current_phase=row[8],
    )


def get_zone(connection: sqlite3.Connection, zone_id: str) -> Zone | None:
    """@brief Recupera un settore tramite il suo identificativo.

    @param connection Connessione SQLite dalla quale leggere.
    @param zone_id Identificativo univoco del settore.
    @return Zona corrispondente, oppure `None` se assente.
    """
    row = connection.execute(
        """
        SELECT id, name, department_number, sector_number, plant_species,
               status, active_recipe_id, last_edge_contact, current_phase
        FROM zones
        WHERE id = ?
        """,
        (zone_id,),
    ).fetchone()
    if row is None:
        return None
    return _zone_from_row(row)


def list_zones(connection: sqlite3.Connection) -> list[Zone]:
    """@brief Elenca i settori ordinati per reparto e numero.

    @param connection Connessione SQLite dalla quale leggere.
    @return Tutte le zone registrate nella serra.
    """
    rows = connection.execute(
        """
        SELECT id, name, department_number, sector_number, plant_species,
               status, active_recipe_id, last_edge_contact, current_phase
        FROM zones
        ORDER BY department_number, sector_number
        """
    ).fetchall()
    return [_zone_from_row(row) for row in rows]
