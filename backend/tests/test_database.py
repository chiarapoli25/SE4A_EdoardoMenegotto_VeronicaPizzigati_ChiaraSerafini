import sqlite3
import threading
from pathlib import Path

import pytest

from backend.app.database import (
    RecipeVersionConflict,
    get_connection,
    get_recipe,
    init_db,
    save_recipe,
)
from backend.app.models import Recipe


@pytest.fixture()
def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_save_and_get_recipe_round_trip(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    save_recipe(connection, example_recipe)

    stored = get_recipe(connection, example_recipe.id)

    assert stored == example_recipe


def test_get_missing_recipe_returns_none(connection: sqlite3.Connection) -> None:
    assert get_recipe(connection, "does-not-exist") is None


def test_init_db_persists_the_seed_catalog_in_sqlite(
    connection: sqlite3.Connection,
) -> None:
    count = connection.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    stored = connection.execute(
        "SELECT version, data FROM recipes WHERE id = 'recipe-calathea'"
    ).fetchone()

    assert count == 20
    assert stored is not None
    assert stored[0] == 5
    expanded = Recipe.model_validate_json(stored[1])
    assert expanded.plant_type == "Calathea"
    assert len(expanded.phases) == 4


def test_recipe_repository_has_no_in_memory_catalog_fallback(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DELETE FROM recipes WHERE id = 'recipe-calathea'")
    connection.commit()

    assert get_recipe(connection, "recipe-calathea") is None

    init_db(connection)

    assert get_recipe(connection, "recipe-calathea") is None


def test_catalog_seed_does_not_overwrite_a_database_customization(
    connection: sqlite3.Connection,
) -> None:
    recipe = get_recipe(connection, "recipe-calathea")
    assert recipe is not None
    customized = recipe.model_copy(
        update={"version": 6, "plant_type": "Calathea personalizzata"}
    )
    save_recipe(connection, customized)

    init_db(connection)

    stored = get_recipe(connection, "recipe-calathea")
    assert stored is not None
    assert stored.version == 6
    assert stored.plant_type == "Calathea personalizzata"


def test_catalog_v1_bootstrap_is_migrated_to_multiphase_v2(
    connection: sqlite3.Connection,
) -> None:
    recipe = get_recipe(connection, "recipe-calathea")
    assert recipe is not None
    legacy = recipe.model_copy(
        update={"version": 1, "phases": recipe.phases[:1]}
    )
    connection.execute(
        "UPDATE recipes SET version = 1, data = ? WHERE id = ?",
        (legacy.model_dump_json(), legacy.id),
    )
    connection.execute(
        """
        UPDATE recipe_catalog_imports
        SET catalog_version = 1
        WHERE recipe_id = ?
        """,
        (legacy.id,),
    )
    connection.commit()

    init_db(connection)

    migrated = get_recipe(connection, legacy.id)
    assert migrated is not None
    assert migrated.version == 5
    assert len(migrated.phases) == 4


def test_save_recipe_upserts_on_higher_version(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    save_recipe(connection, example_recipe)
    updated = example_recipe.model_copy(
        update={"version": example_recipe.version + 1})

    save_recipe(connection, updated)

    assert get_recipe(connection, updated.id) == updated


def test_save_recipe_rejects_non_increasing_version(
    connection: sqlite3.Connection, example_recipe: Recipe
) -> None:
    stored = example_recipe.model_copy(update={"version": 2})
    save_recipe(connection, stored)

    with pytest.raises(RecipeVersionConflict):
        save_recipe(connection, stored.model_copy(update={"version": 2}))

    with pytest.raises(RecipeVersionConflict):
        save_recipe(connection, stored.model_copy(update={"version": 1}))


def test_get_connection_creates_parent_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "smarthydro.db"

    new_connection = get_connection(database_path)
    try:
        init_db(new_connection)
        assert database_path.parent.is_dir()
        assert database_path.exists()
    finally:
        new_connection.close()


def test_get_connection_supports_fastapi_thread_handoff(tmp_path: Path) -> None:
    new_connection = get_connection(tmp_path / "thread-handoff.db")
    observed: list[int] = []
    errors: list[Exception] = []

    def use_connection() -> None:
        try:
            observed.append(new_connection.execute("SELECT 1").fetchone()[0])
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    worker = threading.Thread(target=use_connection)
    worker.start()
    worker.join()
    new_connection.close()

    assert errors == []
    assert observed == [1]


def test_init_db_adds_edge_assignment_to_legacy_zones() -> None:
    legacy = sqlite3.connect(":memory:")
    legacy.execute(
        """
        CREATE TABLE zones (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department_number INTEGER NOT NULL,
            sector_number INTEGER NOT NULL,
            plant_species TEXT NOT NULL,
            status TEXT NOT NULL,
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            UNIQUE (department_number, sector_number)
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO zones (
            id, name, department_number, sector_number, plant_species,
            status, active_recipe_id, last_edge_contact, current_phase
        )
        VALUES ('legacy-zone', 'Legacy', 1, 1, 'Pomodoro',
                'offline', NULL, NULL, NULL)
        """
    )

    try:
        init_db(legacy)
        columns = {
            row[1]
            for row in legacy.execute("PRAGMA table_info(zones)").fetchall()
        }
        assignment = legacy.execute(
            """
            SELECT assigned_edge_id, administrative_status,
                   cultivation_completed
            FROM zones
            WHERE id = 'legacy-zone'
            """
        ).fetchone()
    finally:
        legacy.close()

    assert "assigned_edge_id" in columns
    assert "administrative_status" in columns
    assert "cultivation_completed" in columns
    assert {
        "lifecycle_state",
        "operational_state",
        "active_recipe_version",
        "current_strategies",
        "current_setpoints",
        "time_scale",
        "projection_updated_at",
    } <= columns
    assert "zone_type" not in columns
    assert assignment == ("smarthydro-edge", "active", 0)


def test_init_db_migrates_schema_to_accept_quarantine() -> None:
    legacy = sqlite3.connect(":memory:")
    legacy.execute(
        """
        CREATE TABLE zones (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department_number INTEGER NOT NULL
                CHECK (department_number BETWEEN 1 AND 4),
            sector_number INTEGER NOT NULL,
            plant_species TEXT NOT NULL,
            assigned_edge_id TEXT,
            status TEXT NOT NULL,
            active_recipe_id TEXT,
            last_edge_contact TEXT,
            current_phase TEXT,
            UNIQUE (department_number, sector_number)
        )
        """
    )

    try:
        init_db(legacy)
        legacy.execute(
            """
            INSERT INTO zones (
                id, name, department_number, sector_number,
                plant_species, status
            )
            VALUES ('quarantine-1', 'Quarantena', 5, 1, NULL, 'offline')
            """
        )
        stored = legacy.execute(
            "SELECT department_number, plant_species FROM zones WHERE id = ?",
            ("quarantine-1",),
        ).fetchone()
    finally:
        legacy.close()

    assert stored == (5, None)


def test_init_db_adds_soil_probe_telemetry_to_existing_schema() -> None:
    legacy = sqlite3.connect(":memory:")
    legacy.execute(
        """
        CREATE TABLE telemetry_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            timestamp_seconds REAL NOT NULL,
            recorded_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            temperature_c REAL,
            air_humidity_percent REAL,
            soil_moisture_percent REAL,
            ph REAL,
            light_ppfd_umol_m2_s REAL,
            UNIQUE (zone_id, boot_id, sequence_number)
        )
        """
    )

    try:
        init_db(legacy)
        columns = {
            row[1]
            for row in legacy.execute(
                "PRAGMA table_info(telemetry_samples)"
            ).fetchall()
        }
    finally:
        legacy.close()

    assert {
        "soil_bulk_ec_ms_cm",
        "soil_ec_ms_cm",
        "fertilizer_concentration_mg_per_liter",
        "nitrogen_estimate_mg_per_liter",
        "phosphorus_estimate_mg_per_liter",
        "potassium_estimate_mg_per_liter",
        "active_recipe_id",
        "active_recipe_version",
        "current_phase",
        "operational_state",
        "lifecycle_state",
        "current_strategies",
        "current_setpoints",
        "time_scale",
    } <= columns
