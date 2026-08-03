"""@file database.py
@brief Connessione, dipendenza FastAPI e schema SQLite del backend.

@details Il modulo conosce esclusivamente l'infrastruttura SQLite. Le query di
dominio appartengono ai moduli `repository.py` delle singole feature.
"""

import sqlite3
import os
from collections.abc import Generator
from pathlib import Path


## @brief Percorso predefinito del database SQLite del backend.
DEFAULT_DATABASE_PATH = Path(
    os.environ.get(
        "SMARTHYDRO_DATABASE_PATH",
        Path(__file__).resolve().parents[2] / "data" / "smarthydro.db",
    )
)


def get_connection(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> sqlite3.Connection:
    """@brief Apre una connessione al database SQLite.

    @param database_path Percorso del database oppure `:memory:`.
    @return Connessione aperta; il chiamante ne possiede la chiusura.
    """
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    # FastAPI può aprire e chiudere la stessa dipendenza sincrona in thread
    # diversi del proprio worker pool. Ogni richiesta possiede comunque una
    # connessione dedicata, quindi la disattivazione del vincolo di thread è
    # necessaria e non introduce condivisione fra richieste.
    return sqlite3.connect(database_path, check_same_thread=False)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """@brief Fornisce una connessione SQLite per una richiesta FastAPI.

    @return Generatore che espone la connessione e la chiude al termine.
    """
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Restituisce i nomi delle colonne dichiarate nella tabella."""
    return {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _migrate_edge_session_columns(connection: sqlite3.Connection) -> None:
    """Ricrea le tabelle legacy aggiungendo `boot_id` senza perdere dati."""
    if (
        _table_columns(connection, "telemetry_samples")
        and "boot_id" not in _table_columns(connection, "telemetry_samples")
    ):
        connection.execute(
            "ALTER TABLE telemetry_samples RENAME TO telemetry_samples_legacy"
        )
        connection.execute(
            """
            CREATE TABLE telemetry_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id TEXT NOT NULL,
                boot_id TEXT NOT NULL,
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
                UNIQUE (zone_id, boot_id, sequence_number)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO telemetry_samples (
                id, zone_id, boot_id, sequence_number, timestamp_seconds,
                recorded_at, received_at, temperature_c,
                air_humidity_percent, soil_moisture_percent, ph,
                light_ppfd_umol_m2_s
            )
            SELECT id, zone_id, 'legacy', sequence_number, timestamp_seconds,
                   recorded_at, received_at, temperature_c,
                   air_humidity_percent, soil_moisture_percent, ph,
                   light_ppfd_umol_m2_s
            FROM telemetry_samples_legacy
            """
        )
        connection.execute("DROP TABLE telemetry_samples_legacy")

    if (
        _table_columns(connection, "actuator_snapshots")
        and "boot_id" not in _table_columns(connection, "actuator_snapshots")
    ):
        connection.execute(
            "ALTER TABLE actuator_snapshots RENAME TO actuator_snapshots_legacy"
        )
        connection.execute(
            """
            CREATE TABLE actuator_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id TEXT NOT NULL,
                boot_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
                timestamp_seconds REAL NOT NULL CHECK (timestamp_seconds >= 0),
                recorded_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                command_data TEXT NOT NULL,
                output_data TEXT NOT NULL,
                FOREIGN KEY (zone_id) REFERENCES zones(id),
                UNIQUE (zone_id, boot_id, sequence_number)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO actuator_snapshots (
                id, zone_id, boot_id, sequence_number, timestamp_seconds,
                recorded_at, received_at, command_data, output_data
            )
            SELECT id, zone_id, 'legacy', sequence_number, timestamp_seconds,
                   recorded_at, received_at, command_data, output_data
            FROM actuator_snapshots_legacy
            """
        )
        connection.execute("DROP TABLE actuator_snapshots_legacy")


def _migrate_zone_assignment_column(connection: sqlite3.Connection) -> None:
    """Aggiunge l'assegnazione Edge agli schemi creati da versioni precedenti."""
    if (
        _table_columns(connection, "zones")
        and "assigned_edge_id" not in _table_columns(connection, "zones")
    ):
        connection.execute(
            "ALTER TABLE zones ADD COLUMN assigned_edge_id TEXT"
        )


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
            assigned_edge_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('online', 'offline')),
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            UNIQUE (department_number, sector_number)
        )
        """
    )
    _migrate_zone_assignment_column(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
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
            UNIQUE (zone_id, boot_id, sequence_number)
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
            boot_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
            timestamp_seconds REAL NOT NULL CHECK (timestamp_seconds >= 0),
            recorded_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            command_data TEXT NOT NULL,
            output_data TEXT NOT NULL,
            FOREIGN KEY (zone_id) REFERENCES zones(id),
            UNIQUE (zone_id, boot_id, sequence_number)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_actuators_zone_recorded_at
        ON actuator_snapshots (zone_id, recorded_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS edge_events (
            event_id TEXT PRIMARY KEY,
            edge_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            zone_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp_seconds REAL NOT NULL CHECK (timestamp_seconds >= 0),
            recorded_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            payload_data TEXT NOT NULL,
            FOREIGN KEY (zone_id) REFERENCES zones(id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_edge_events_zone_recorded_at
        ON edge_events (zone_id, recorded_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_commands (
            command_id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            command_type TEXT NOT NULL,
            payload_data TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'succeeded', 'rejected')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            result_message TEXT,
            result_replayed INTEGER,
            FOREIGN KEY (zone_id) REFERENCES zones(id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_commands_zone_status_created
        ON runtime_commands (zone_id, status, created_at)
        """
    )
    _migrate_edge_session_columns(connection)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_telemetry_zone_recorded_at
        ON telemetry_samples (zone_id, recorded_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_actuators_zone_recorded_at
        ON actuator_snapshots (zone_id, recorded_at DESC)
        """
    )
    connection.commit()
