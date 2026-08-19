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
    # FastAPI puo creare la dipendenza sincrona e invocare l'endpoint in due
    # worker thread differenti. La connessione resta comunque confinata alla
    # singola richiesta, ma SQLite deve consentirne l'uso sequenziale fra i due
    # thread gestiti da Starlette.
    connection = sqlite3.connect(database_path, check_same_thread=False)
    # Sotto scritture concorrenti (es. due conferme di coltivazione su zone
    # diverse nello stesso istante) SQLite puo rifiutare subito una scrittura
    # con "database is locked" invece di attendere il rilascio del lock.
    # busy_timeout fa attendere il driver fino a 5s prima di sollevare
    # l'errore, che in pratica elimina i falsi conflitti sotto carico normale.
    connection.execute("PRAGMA busy_timeout = 5000")
    if database_path != ":memory:":
        # WAL consente letture concorrenti mentre e in corso una scrittura
        # (le richieste GET non vengono piu bloccate da un confirm/pause in
        # volo). Non e supportato dai database in memoria usati dai test.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


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


def _migrate_recipes_primary_key(connection: sqlite3.Connection) -> None:
    """Ricrea `recipes` con chiave primaria (id, version) senza perdere dati.

    @details Lo schema precedente usava `id` come unica chiave primaria e
    ogni salvataggio sovrascriveva la versione precedente. Le coltivazioni
    devono invece potersi riferire a una versione esatta e gia confermata,
    quindi ogni versione va conservata come riga a se stante.
    """
    columns = connection.execute("PRAGMA table_info(recipes)").fetchall()
    if not columns:
        return
    version_column = next((col for col in columns if col[1] == "version"), None)
    if version_column is not None and version_column[5] != 0:
        return
    connection.execute("ALTER TABLE recipes RENAME TO recipes_legacy")
    connection.execute(
        """
        CREATE TABLE recipes (
            id TEXT NOT NULL,
            version INTEGER NOT NULL,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (id, version)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO recipes (id, version, data, updated_at)
        SELECT id, version, data, updated_at FROM recipes_legacy
        """
    )
    connection.execute("DROP TABLE recipes_legacy")


def _migrate_cultivation_status_starting(connection: sqlite3.Connection) -> None:
    """Rinomina lo stato `confirmed` in `starting` sulle installazioni precedenti.

    @details Il CHECK di SQLite fa parte dello schema e non si puo alterare
    in place: la tabella va ricreata, come per `_migrate_recipes_primary_key`.
    """
    schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cultivations'"
    ).fetchone()
    schema = "" if schema_row is None else (schema_row[0] or "")
    if not schema or "'starting'" in schema:
        return
    connection.execute("ALTER TABLE cultivations RENAME TO cultivations_legacy")
    connection.execute(
        """
        CREATE TABLE cultivations (
            id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            plant_species TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            recipe_version INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN (
                    'draft', 'starting', 'active', 'paused',
                    'completed', 'failed'
                )),
            created_by TEXT,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            elapsed_simulation_seconds REAL NOT NULL DEFAULT 0
                CHECK (elapsed_simulation_seconds >= 0),
            requested_time_scale REAL NOT NULL DEFAULT 1.0
                CHECK (requested_time_scale > 0),
            applied_time_scale REAL,
            error_message TEXT,
            activation_command_id TEXT,
            FOREIGN KEY (zone_id) REFERENCES zones(id),
            FOREIGN KEY (recipe_id, recipe_version)
                REFERENCES recipes(id, version)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO cultivations
        SELECT id, zone_id, plant_species, recipe_id, recipe_version,
               CASE WHEN status = 'confirmed' THEN 'starting' ELSE status END,
               created_by, created_at, confirmed_at, started_at, completed_at,
               elapsed_simulation_seconds, requested_time_scale,
               applied_time_scale, error_message, activation_command_id
        FROM cultivations_legacy
        """
    )
    connection.execute("DROP TABLE cultivations_legacy")


def _migrate_cultivation_current_phase_column(
    connection: sqlite3.Connection,
) -> None:
    """Aggiunge la fase corrente allo storico coltivazioni preesistente.

    @details Il valore rispecchia `zones.current_phase` al momento
    dell'ultimo evento `RecipePhaseChanged`/`RecipeCompleted` o del risultato
    strutturato di `ActivateCultivation` interpretato per questa
    coltivazione, cosi che lo storico resti leggibile anche dopo che la zona
    e passata a una coltivazione successiva (che azzera la propria proiezione
    corrente).
    """
    if (
        _table_columns(connection, "cultivations")
        and "current_phase" not in _table_columns(connection, "cultivations")
    ):
        connection.execute(
            "ALTER TABLE cultivations ADD COLUMN current_phase TEXT"
        )


def _migrate_runtime_command_result_column(connection: sqlite3.Connection) -> None:
    """Aggiunge il risultato strutturato ai comandi runtime preesistenti.

    @details Oltre al messaggio libero gia previsto, l'Edge puo riportare un
    oggetto JSON con i dettagli dell'esito (es. `applied_time_scale`,
    `current_phase`): viene conservato per intero e interpretato dal modulo
    di dominio competente (`features.cultivations`), senza che la coda
    comandi debba conoscerne la struttura.
    """
    if (
        _table_columns(connection, "runtime_commands")
        and "result_data" not in _table_columns(connection, "runtime_commands")
    ):
        connection.execute(
            "ALTER TABLE runtime_commands ADD COLUMN result_data TEXT"
        )


def _migrate_cultivation_confirmed_by_column(
    connection: sqlite3.Connection,
) -> None:
    """Aggiunge l'operatore che ha confermato l'attivazione allo storico esistente.

    @details Distinto da `created_by` (chi apre la bozza): la conferma e
    l'azione che impegna davvero il settore e l'Edge, quindi va tracciata
    separatamente. Popolato dall'utente autenticato che chiama
    `POST /zones/{zone_id}/cultivations/{id}/confirm`.
    """
    if (
        _table_columns(connection, "cultivations")
        and "confirmed_by" not in _table_columns(connection, "cultivations")
    ):
        connection.execute(
            "ALTER TABLE cultivations ADD COLUMN confirmed_by TEXT"
        )


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

    @param connection Connessione sulla quale creare ricette, zone,
        coltivazioni, telemetria e snapshot degli attuatori.
    @return Nessun valore.
    """
    _migrate_recipes_primary_key(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT NOT NULL,
            version INTEGER NOT NULL,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (id, version)
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
            result_data TEXT,
            FOREIGN KEY (zone_id) REFERENCES zones(id)
        )
        """
    )
    _migrate_runtime_command_result_column(connection)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_commands_zone_status_created
        ON runtime_commands (zone_id, status, created_at)
        """
    )
    _migrate_cultivation_status_starting(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cultivations (
            id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            plant_species TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            recipe_version INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN (
                    'draft', 'starting', 'active', 'paused',
                    'completed', 'failed'
                )),
            created_by TEXT,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            elapsed_simulation_seconds REAL NOT NULL DEFAULT 0
                CHECK (elapsed_simulation_seconds >= 0),
            requested_time_scale REAL NOT NULL DEFAULT 1.0
                CHECK (requested_time_scale > 0),
            applied_time_scale REAL,
            current_phase TEXT,
            error_message TEXT,
            activation_command_id TEXT,
            confirmed_by TEXT,
            FOREIGN KEY (zone_id) REFERENCES zones(id),
            FOREIGN KEY (recipe_id, recipe_version)
                REFERENCES recipes(id, version)
        )
        """
    )
    _migrate_cultivation_current_phase_column(connection)
    _migrate_cultivation_confirmed_by_column(connection)
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cultivations_zone_not_concluded
        ON cultivations (zone_id)
        WHERE status NOT IN ('completed', 'failed')
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cultivations_zone_created_at
        ON cultivations (zone_id, created_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cultivations_zone_status
        ON cultivations (zone_id, status)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cultivations_activation_command
        ON cultivations (activation_command_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
                CHECK (role IN ('agronomist', 'operator', 'admin')),
            full_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
                CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            actor_username TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
            resource_type TEXT,
            resource_id TEXT,
            detail_data TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_occurred_at
        ON audit_log (occurred_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_actor_username
        ON audit_log (actor_username, occurred_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_resource
        ON audit_log (resource_type, resource_id, occurred_at DESC)
        """
    )
    _migrate_edge_session_columns(connection)
    _migrate_soil_probe_columns(connection)
    _migrate_telemetry_state_columns(connection)
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
    from ..features.recipes.catalog import seed_recipe_catalog

    seed_recipe_catalog(connection)
    connection.commit()
