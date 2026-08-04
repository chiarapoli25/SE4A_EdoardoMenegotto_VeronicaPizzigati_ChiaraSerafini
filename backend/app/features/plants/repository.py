"""Persistenza SQLite delle piante e dei loro spostamenti."""

import sqlite3
from datetime import datetime, timezone

from .models import Plant, PlantCreate, PlantMovement


class PlantConflict(Exception):
    """Segnala un identificativo pianta riutilizzato in modo incompatibile."""


def _plant_from_row(row: tuple) -> Plant:
    return Plant(
        id=row[0],
        species=row[1],
        home_zone_id=row[2],
        current_zone_id=row[3],
        is_quarantined=bool(row[4]),
        quarantine_reason=row[5],
        quarantined_at=row[6],
        created_at=row[7],
        updated_at=row[8],
    )


def get_plant(
    connection: sqlite3.Connection,
    plant_id: str,
) -> Plant | None:
    row = connection.execute(
        """
        SELECT id, species, home_zone_id, current_zone_id, is_quarantined,
               quarantine_reason, quarantined_at, created_at, updated_at
        FROM plants
        WHERE id = ?
        """,
        (plant_id,),
    ).fetchone()
    return None if row is None else _plant_from_row(row)


def create_plant(
    connection: sqlite3.Connection,
    plant: PlantCreate,
) -> Plant:
    now = datetime.now(timezone.utc)
    try:
        connection.execute(
            """
            INSERT INTO plants (
                id, species, home_zone_id, current_zone_id, is_quarantined,
                quarantine_reason, quarantined_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 0, NULL, NULL, ?, ?)
            """,
            (
                plant.id,
                plant.species,
                plant.home_zone_id,
                plant.home_zone_id,
                now.isoformat(),
                now.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        existing = get_plant(connection, plant.id)
        if (
            existing is not None
            and existing.species == plant.species
            and existing.home_zone_id == plant.home_zone_id
        ):
            return existing
        raise PlantConflict(
            f"plant id {plant.id!r} already exists with different data"
        ) from error
    connection.commit()
    stored = get_plant(connection, plant.id)
    assert stored is not None
    return stored


def list_plants(
    connection: sqlite3.Connection,
    zone_id: str | None = None,
    is_quarantined: bool | None = None,
    limit: int = 100,
) -> list[Plant]:
    conditions: list[str] = []
    parameters: list[str | int] = []
    if zone_id is not None:
        conditions.append("current_zone_id = ?")
        parameters.append(zone_id)
    if is_quarantined is not None:
        conditions.append("is_quarantined = ?")
        parameters.append(int(is_quarantined))
    where_clause = "" if not conditions else "WHERE " + " AND ".join(conditions)
    parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT id, species, home_zone_id, current_zone_id, is_quarantined,
               quarantine_reason, quarantined_at, created_at, updated_at
        FROM plants
        {where_clause}
        ORDER BY id
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [_plant_from_row(row) for row in rows]


def set_quarantine_state(
    connection: sqlite3.Connection,
    plant: Plant,
    is_quarantined: bool,
    destination_zone_id: str,
    reason: str | None,
) -> Plant:
    """Aggiorna flag e posizione e registra lo spostamento atomico."""
    stored_reason = reason if is_quarantined else None
    if (
        plant.is_quarantined == is_quarantined
        and plant.current_zone_id == destination_zone_id
        and plant.quarantine_reason == stored_reason
    ):
        return plant

    now = datetime.now(timezone.utc)
    quarantined_at = now.isoformat() if is_quarantined else None
    quarantine_reason = stored_reason
    connection.execute(
        """
        UPDATE plants
        SET current_zone_id = ?, is_quarantined = ?, quarantine_reason = ?,
            quarantined_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            destination_zone_id,
            int(is_quarantined),
            quarantine_reason,
            quarantined_at,
            now.isoformat(),
            plant.id,
        ),
    )
    connection.execute(
        """
        INSERT INTO plant_movements (
            plant_id, from_zone_id, to_zone_id, is_quarantined,
            reason, moved_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            plant.id,
            plant.current_zone_id,
            destination_zone_id,
            int(is_quarantined),
            reason,
            now.isoformat(),
        ),
    )
    connection.commit()
    stored = get_plant(connection, plant.id)
    assert stored is not None
    return stored


def list_plant_movements(
    connection: sqlite3.Connection,
    plant_id: str,
    limit: int = 100,
) -> list[PlantMovement]:
    rows = connection.execute(
        """
        SELECT id, plant_id, from_zone_id, to_zone_id, is_quarantined,
               reason, moved_at
        FROM plant_movements
        WHERE plant_id = ?
        ORDER BY moved_at DESC, id DESC
        LIMIT ?
        """,
        (plant_id, limit),
    ).fetchall()
    return [
        PlantMovement(
            movement_id=row[0],
            plant_id=row[1],
            from_zone_id=row[2],
            to_zone_id=row[3],
            is_quarantined=bool(row[4]),
            reason=row[5],
            moved_at=row[6],
        )
        for row in reversed(rows)
    ]
