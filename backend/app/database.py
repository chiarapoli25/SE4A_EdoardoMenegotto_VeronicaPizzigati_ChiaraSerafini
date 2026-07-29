"""@file database.py
@brief Persistenza SQLite delle zone, della telemetria e delle ricette.

@details `zones` rappresenta al massimo otto settori: quattro reparti con due
posizioni ciascuno e una sola specie per riga. `telemetry_samples` conserva lo
storico dei sensori di ciascuna zona. Una riga di `recipes`, indicizzata dal suo
`id`, contiene invece l'intera ricetta serializzata in JSON. `version` e
duplicata per rifiutare un salvataggio con versione non crescente.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    Recipe,
    TelemetryCreate,
    TelemetrySample,
    Zone,
    ZoneCreate,
    ZoneStatus,
)

## @brief Percorso predefinito del database SQLite del backend.
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "smarthydro.db"


class RecipeVersionConflict(Exception):
    """@brief Segnala una versione non maggiore di quella gia salvata."""


class ZoneConflict(Exception):
    """@brief Segnala un identificativo o settore fisico gia occupato."""


class TelemetryConflict(Exception):
    """@brief Segnala un progressivo di telemetria gia ricevuto."""


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
        `recipes`, `zones` e `telemetry_samples`.
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
            timestamp_seconds REAL NOT NULL CHECK (timestamp_seconds >= 0),
            recorded_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            temperature_c REAL,
            air_humidity_percent REAL,
            soil_moisture_percent REAL,
            ph REAL,
            light_ppfd_umol_m2_s REAL,
            FOREIGN KEY (zone_id) REFERENCES zones(id),
            UNIQUE (zone_id, sequence_number)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_telemetry_zone_recorded_at
        ON telemetry_samples (zone_id, recorded_at DESC)
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


def save_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
    telemetry: TelemetryCreate,
) -> TelemetrySample:
    """@brief Salva un campione e aggiorna lo stato della zona.

    @param connection Connessione SQLite sulla quale scrivere.
    @param zone_id Identificativo della zona che ha prodotto il campione.
    @param telemetry Misure validate ricevute dall'Edge.
    @return Campione completo degli identificativi e del tempo di ricezione.
    @throws TelemetryConflict Se il progressivo e gia presente nella zona.
    """
    recorded_at = telemetry.recorded_at.astimezone(timezone.utc)
    received_at = datetime.now(timezone.utc)
    try:
        cursor = connection.execute(
            """
            INSERT INTO telemetry_samples (
                zone_id, sequence_number, timestamp_seconds, recorded_at,
                received_at, temperature_c, air_humidity_percent,
                soil_moisture_percent, ph, light_ppfd_umol_m2_s
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone_id,
                telemetry.sequence_number,
                telemetry.timestamp_seconds,
                recorded_at.isoformat(),
                received_at.isoformat(),
                telemetry.temperature_c,
                telemetry.air_humidity_percent,
                telemetry.soil_moisture_percent,
                telemetry.ph,
                telemetry.light_ppfd_umol_m2_s,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise TelemetryConflict(
            f"telemetry sequence {telemetry.sequence_number} already exists "
            f"for zone {zone_id!r}"
        ) from error

    connection.execute(
        """
        UPDATE zones
        SET status = 'online', last_edge_contact = ?
        WHERE id = ?
        """,
        (received_at.isoformat(), zone_id),
    )
    connection.commit()
    stored_data = telemetry.model_dump()
    stored_data["recorded_at"] = recorded_at
    return TelemetrySample(
        **stored_data,
        sample_id=cursor.lastrowid,
        zone_id=zone_id,
        received_at=received_at,
    )


def _telemetry_from_row(row: tuple) -> TelemetrySample:
    """@brief Converte una riga SQLite in un campione di telemetria.

    @param row Riga della tabella `telemetry_samples`.
    @return Campione validato da Pydantic.
    """
    return TelemetrySample(
        sample_id=row[0],
        zone_id=row[1],
        sequence_number=row[2],
        timestamp_seconds=row[3],
        recorded_at=row[4],
        received_at=row[5],
        temperature_c=row[6],
        air_humidity_percent=row[7],
        soil_moisture_percent=row[8],
        ph=row[9],
        light_ppfd_umol_m2_s=row[10],
    )


def get_latest_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
) -> TelemetrySample | None:
    """@brief Recupera il campione piu recente di una zona.

    @param connection Connessione SQLite dalla quale leggere.
    @param zone_id Identificativo della zona.
    @return Ultimo campione per data di misura, oppure `None`.
    """
    row = connection.execute(
        """
        SELECT id, zone_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, temperature_c, air_humidity_percent,
               soil_moisture_percent, ph, light_ppfd_umol_m2_s
        FROM telemetry_samples
        WHERE zone_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
        """,
        (zone_id,),
    ).fetchone()
    if row is None:
        return None
    return _telemetry_from_row(row)


def list_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
    recorded_from: datetime | None = None,
    recorded_to: datetime | None = None,
    limit: int = 100,
) -> list[TelemetrySample]:
    """@brief Legge lo storico recente della telemetria.

    @param connection Connessione SQLite dalla quale leggere.
    @param zone_id Identificativo della zona.
    @param recorded_from Estremo temporale inferiore incluso, se presente.
    @param recorded_to Estremo temporale superiore incluso, se presente.
    @param limit Numero massimo di campioni restituiti.
    @return Campioni selezionati in ordine cronologico crescente.
    """
    conditions = ["zone_id = ?"]
    parameters: list[str | int] = [zone_id]
    if recorded_from is not None:
        conditions.append("recorded_at >= ?")
        parameters.append(recorded_from.astimezone(timezone.utc).isoformat())
    if recorded_to is not None:
        conditions.append("recorded_at <= ?")
        parameters.append(recorded_to.astimezone(timezone.utc).isoformat())
    parameters.append(limit)

    rows = connection.execute(
        f"""
        SELECT id, zone_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, temperature_c, air_humidity_percent,
               soil_moisture_percent, ph, light_ppfd_umol_m2_s
        FROM telemetry_samples
        WHERE {" AND ".join(conditions)}
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [_telemetry_from_row(row) for row in reversed(rows)]
