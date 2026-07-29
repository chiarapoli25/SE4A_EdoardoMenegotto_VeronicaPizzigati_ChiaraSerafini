"""@file db.py
@brief Connessione, dipendenza FastAPI e schema SQLite del backend.

@details Il modulo conosce esclusivamente l'infrastruttura SQLite. Le query di
dominio appartengono ai moduli del package `repositories`.
"""

import sqlite3
from collections.abc import Generator
from pathlib import Path


## @brief Percorso predefinito del database SQLite del backend.
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "smarthydro.db"


def get_connection(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> sqlite3.Connection:
    """@brief Apre una connessione al database SQLite.

    @param database_path Percorso del database oppure `:memory:`.
    @return Connessione aperta; il chiamante ne possiede la chiusura.
    """
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(database_path)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """@brief Fornisce una connessione SQLite per una richiesta FastAPI.

    @return Generatore che espone la connessione e la chiude al termine.
    """
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


def init_db(connection: sqlite3.Connection) -> None:
    """@brief Crea lo schema applicativo se non esiste.

    @param connection Connessione sulla quale creare ricette, zone, telemetria
        e snapshot degli attuatori.
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS actuator_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
            timestamp_seconds REAL NOT NULL CHECK (timestamp_seconds >= 0),
            recorded_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            command_data TEXT NOT NULL,
            output_data TEXT NOT NULL,
            FOREIGN KEY (zone_id) REFERENCES zones(id),
            UNIQUE (zone_id, sequence_number)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_actuators_zone_recorded_at
        ON actuator_snapshots (zone_id, recorded_at DESC)
        """
    )
    connection.commit()
