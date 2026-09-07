"""@file database.py
@brief Connessione, dipendenza FastAPI e schema SQLite del backend.

@details Il modulo conosce esclusivamente l'infrastruttura SQLite. Le query di
dominio appartengono ai moduli `repository.py` delle singole feature.
"""

import sqlite3
import os
from collections.abc import Generator
from pathlib import Path

from .config import default_edge_id


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
    # FastAPI puo creare la dipendenza sincrona e invocare l'endpoint in due
    # worker thread differenti. La connessione resta comunque confinata alla
    # singola richiesta, ma SQLite deve consentirne l'uso sequenziale fra i due
    # thread gestiti da Starlette.
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
                soil_bulk_ec_ms_cm REAL,
                soil_ec_ms_cm REAL,
                fertilizer_concentration_mg_per_liter REAL,
                nitrogen_estimate_mg_per_liter REAL,
                phosphorus_estimate_mg_per_liter REAL,
                potassium_estimate_mg_per_liter REAL,
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


def _migrate_soil_probe_columns(connection: sqlite3.Connection) -> None:
    """Aggiunge alle telemetrie legacy le stime prodotte dalle sonde nel suolo."""
    columns = _table_columns(connection, "telemetry_samples")
    additions = {
        "soil_bulk_ec_ms_cm": "REAL",
        "soil_ec_ms_cm": "REAL",
        "fertilizer_concentration_mg_per_liter": "REAL",
        "nitrogen_estimate_mg_per_liter": "REAL",
        "phosphorus_estimate_mg_per_liter": "REAL",
        "potassium_estimate_mg_per_liter": "REAL",
    }
    for column, declaration in additions.items():
        if columns and column not in columns:
            connection.execute(
                f"ALTER TABLE telemetry_samples ADD COLUMN {column} {declaration}"
            )


def _migrate_telemetry_state_columns(connection: sqlite3.Connection) -> None:
    """Aggiunge lo snapshot operativo completo ai campioni precedenti."""
    columns = _table_columns(connection, "telemetry_samples")
    additions = {
        "active_recipe_id": "TEXT NOT NULL DEFAULT 'legacy-unknown'",
        "active_recipe_version": "INTEGER NOT NULL DEFAULT 1",
        "current_phase": "TEXT NOT NULL DEFAULT 'legacy-unknown'",
        "operational_state": "TEXT NOT NULL DEFAULT 'Nominal'",
        "lifecycle_state": "TEXT NOT NULL DEFAULT 'Running'",
        "current_strategies": "TEXT NOT NULL DEFAULT '{}'",
        "current_setpoints": "TEXT NOT NULL DEFAULT '{}'",
        "time_scale": "REAL NOT NULL DEFAULT 1.0",
    }
    for column, declaration in additions.items():
        if columns and column not in columns:
            connection.execute(
                f"ALTER TABLE telemetry_samples ADD COLUMN {column} {declaration}"
            )


def _migrate_zone_projection_columns(connection: sqlite3.Connection) -> None:
    """Aggiunge la proiezione corrente senza derivarla dallo storico eventi."""
    columns = _table_columns(connection, "zones")
    additions = {
        "lifecycle_state": "TEXT NOT NULL DEFAULT 'Idle'",
        "operational_state": "TEXT NOT NULL DEFAULT 'Nominal'",
        "active_recipe_version": "INTEGER",
        "current_strategies": "TEXT NOT NULL DEFAULT '{}'",
        "current_setpoints": "TEXT NOT NULL DEFAULT '{}'",
        "time_scale": "REAL NOT NULL DEFAULT 1.0",
        "projection_updated_at": "TEXT",
        "active_cultivation_id": "TEXT",
    }
    for column, declaration in additions.items():
        if columns and column not in columns:
            connection.execute(
                f"ALTER TABLE zones ADD COLUMN {column} {declaration}"
            )


def _migrate_recipe_catalog_version(connection: sqlite3.Connection) -> None:
    """Versiona il bootstrap senza trasformare i JSON in sorgente runtime."""
    columns = _table_columns(connection, "recipe_catalog_imports")
    if columns and "catalog_version" not in columns:
        connection.execute(
            """
            ALTER TABLE recipe_catalog_imports
            ADD COLUMN catalog_version INTEGER NOT NULL DEFAULT 1
            """
        )


def _migrate_cultivation_completed_column(
    connection: sqlite3.Connection,
) -> None:
    """Aggiunge lo stato finale esplicito alle zone create in precedenza."""
    columns = _table_columns(connection, "zones")
    if columns and "cultivation_completed" not in columns:
        connection.execute(
            """
            ALTER TABLE zones
            ADD COLUMN cultivation_completed INTEGER NOT NULL DEFAULT 0
                CHECK (cultivation_completed IN (0, 1))
            """
        )


def _migrate_zone_administrative_status_column(
    connection: sqlite3.Connection,
) -> None:
    """Aggiunge lo stato amministrativo senza confonderlo con la connettivita."""
    if (
        _table_columns(connection, "zones")
        and "administrative_status" not in _table_columns(connection, "zones")
    ):
        connection.execute(
            """
            ALTER TABLE zones
            ADD COLUMN administrative_status TEXT NOT NULL DEFAULT 'active'
                CHECK (administrative_status IN ('active', 'inactive', 'maintenance'))
            """
        )


def _migrate_fifth_department_schema(connection: sqlite3.Connection) -> None:
    """Estende i reparti a 1-5 e rende mista la composizione del quinto."""
    columns = _table_columns(connection, "zones")
    if not columns:
        return
    schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'zones'"
    ).fetchone()
    schema = "" if schema_row is None else (schema_row[0] or "")
    if (
        "zone_type" not in columns
        and "BETWEEN 1 AND 5" in schema.upper()
    ):
        return

    connection.execute("DROP TABLE IF EXISTS zones_department_migration")
    connection.execute(
        """
        CREATE TABLE zones_department_migration (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department_number INTEGER NOT NULL
                CHECK (department_number BETWEEN 1 AND 5),
            sector_number INTEGER NOT NULL
                CHECK (sector_number BETWEEN 1 AND 2),
            plant_species TEXT,
            assigned_edge_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('online', 'offline')),
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            cultivation_completed INTEGER NOT NULL DEFAULT 0
                CHECK (cultivation_completed IN (0, 1)),
            administrative_status TEXT NOT NULL DEFAULT 'active'
                CHECK (administrative_status IN
                       ('active', 'inactive', 'maintenance')),
            CHECK (
                (department_number BETWEEN 1 AND 4
                 AND plant_species IS NOT NULL)
                OR
                (department_number = 5
                 AND plant_species IS NULL)
            ),
            UNIQUE (department_number, sector_number)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO zones_department_migration (
            id, name, department_number, sector_number, plant_species,
            assigned_edge_id, status, active_recipe_id,
            last_edge_contact, current_phase
        )
        SELECT id, name, department_number, sector_number,
               CASE WHEN department_number = 5
                    THEN NULL ELSE plant_species END,
               assigned_edge_id, status, active_recipe_id,
               last_edge_contact, current_phase
        FROM zones
        """
    )
    connection.execute("DROP TABLE zones")
    connection.execute(
        "ALTER TABLE zones_department_migration RENAME TO zones"
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
        CREATE TABLE IF NOT EXISTS recipe_catalog_imports (
            recipe_id TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL,
            catalog_version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    _migrate_recipe_catalog_version(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS control_strategy_settings (
            variable TEXT PRIMARY KEY,
            selected_strategy TEXT NOT NULL,
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
                CHECK (department_number BETWEEN 1 AND 5),
            sector_number INTEGER NOT NULL
                CHECK (sector_number BETWEEN 1 AND 2),
            plant_species TEXT,
            assigned_edge_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('online', 'offline')),
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            lifecycle_state TEXT NOT NULL DEFAULT 'Idle'
                CHECK (lifecycle_state IN ('Idle', 'Running', 'Paused', 'Error')),
            operational_state TEXT NOT NULL DEFAULT 'Nominal'
                CHECK (operational_state IN
                       ('Nominal', 'Degraded', 'EmergencyLockdown')),
            active_recipe_version INTEGER,
            current_strategies TEXT NOT NULL DEFAULT '{}',
            current_setpoints TEXT NOT NULL DEFAULT '{}',
            time_scale REAL NOT NULL DEFAULT 1.0
                CHECK (time_scale BETWEEN 1.0 AND 60.0),
            projection_updated_at TEXT,
            active_cultivation_id TEXT,
            cultivation_completed INTEGER NOT NULL DEFAULT 0
                CHECK (cultivation_completed IN (0, 1)),
            administrative_status TEXT NOT NULL DEFAULT 'active'
                CHECK (administrative_status IN
                       ('active', 'inactive', 'maintenance')),
            CHECK (
                (department_number BETWEEN 1 AND 4
                 AND plant_species IS NOT NULL)
                OR
                (department_number = 5
                 AND plant_species IS NULL)
            ),
            UNIQUE (department_number, sector_number)
        )
        """
    )
    _migrate_zone_assignment_column(connection)
    _migrate_fifth_department_schema(connection)
    _migrate_zone_administrative_status_column(connection)
    _migrate_cultivation_completed_column(connection)
    _migrate_zone_projection_columns(connection)
    connection.execute(
        """
        UPDATE zones
        SET assigned_edge_id = ?
        WHERE department_number BETWEEN 1 AND 4
          AND assigned_edge_id IS NULL
        """,
        (default_edge_id(),),
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cultivations (
            id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            plant_species TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            recipe_version INTEGER NOT NULL CHECK (recipe_version >= 1),
            state TEXT NOT NULL
                CHECK (state IN (
                    'activating', 'running', 'pausing', 'paused',
                    'resuming', 'stopping', 'error', 'archived'
                )),
            recipe_completed INTEGER NOT NULL DEFAULT 0
                CHECK (recipe_completed IN (0, 1)),
            created_at TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            archived_at TEXT,
            last_command_id TEXT,
            FOREIGN KEY (zone_id) REFERENCES zones(id),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cultivations_one_active_per_zone
        ON cultivations (zone_id)
        WHERE archived_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cultivations_state_created
        ON cultivations (state, created_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plants (
            id TEXT PRIMARY KEY,
            species TEXT NOT NULL,
            home_zone_id TEXT NOT NULL,
            current_zone_id TEXT NOT NULL,
            is_quarantined INTEGER NOT NULL DEFAULT 0
                CHECK (is_quarantined IN (0, 1)),
            quarantine_reason TEXT,
            quarantined_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (home_zone_id) REFERENCES zones(id),
            FOREIGN KEY (current_zone_id) REFERENCES zones(id),
            CHECK (
                (is_quarantined = 0
                 AND quarantine_reason IS NULL
                 AND quarantined_at IS NULL)
                OR
                (is_quarantined = 1
                 AND quarantine_reason IS NOT NULL
                 AND quarantined_at IS NOT NULL)
            )
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_plants_current_zone_quarantine
        ON plants (current_zone_id, is_quarantined, id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plant_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id TEXT NOT NULL,
            from_zone_id TEXT NOT NULL,
            to_zone_id TEXT NOT NULL,
            is_quarantined INTEGER NOT NULL
                CHECK (is_quarantined IN (0, 1)),
            reason TEXT,
            moved_at TEXT NOT NULL,
            FOREIGN KEY (plant_id) REFERENCES plants(id),
            FOREIGN KEY (from_zone_id) REFERENCES zones(id),
            FOREIGN KEY (to_zone_id) REFERENCES zones(id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_plant_movements_plant_moved_at
        ON plant_movements (plant_id, moved_at DESC)
        """
    )
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
            soil_bulk_ec_ms_cm REAL,
            soil_ec_ms_cm REAL,
            fertilizer_concentration_mg_per_liter REAL,
            nitrogen_estimate_mg_per_liter REAL,
            phosphorus_estimate_mg_per_liter REAL,
            potassium_estimate_mg_per_liter REAL,
            ph REAL,
            light_ppfd_umol_m2_s REAL,
            active_recipe_id TEXT NOT NULL,
            active_recipe_version INTEGER NOT NULL,
            current_phase TEXT NOT NULL,
            operational_state TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            current_strategies TEXT NOT NULL,
            current_setpoints TEXT NOT NULL,
            time_scale REAL NOT NULL,
            cultivation_id TEXT,
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
            cultivation_id TEXT,
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
    _migrate_soil_probe_columns(connection)
    _migrate_telemetry_state_columns(connection)
    telemetry_columns = _table_columns(connection, "telemetry_samples")
    if telemetry_columns and "cultivation_id" not in telemetry_columns:
        connection.execute(
            "ALTER TABLE telemetry_samples ADD COLUMN cultivation_id TEXT"
        )
    actuator_columns = _table_columns(connection, "actuator_snapshots")
    if actuator_columns and "cultivation_id" not in actuator_columns:
        connection.execute(
            "ALTER TABLE actuator_snapshots ADD COLUMN cultivation_id TEXT"
        )
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
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_telemetry_cultivation_recorded_at
        ON telemetry_samples (cultivation_id, recorded_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_actuators_cultivation_recorded_at
        ON actuator_snapshots (cultivation_id, recorded_at DESC)
        """
    )
    # Autenticazione della dashboard (utenti/ruoli e sessioni via token
    # opaco, vedi backend/app/features/users/): meccanismo separato dal
    # Bearer SMARTHYDRO_API_TOKEN usato dall'Edge, vedi
    # backend/app/core/security.py.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'agronomo')),
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_username
        ON sessions (username)
        """
    )

    from ..features.control_strategy.repository import (
        seed_defaults as seed_control_strategy_defaults,
    )
    from ..features.recipes.catalog import seed_recipe_catalog

    seed_recipe_catalog(connection)
    seed_control_strategy_defaults(connection)
    # Gli account non vengono piu' seminati automaticamente all'avvio: un
    # admin/pass123 cablato nel codice di startup e' un rischio di sicurezza
    # (vedi discussione merge del 2026-09-07). Il primo account si crea in
    # modo esplicito con demo/seed_users.py.
    connection.commit()
