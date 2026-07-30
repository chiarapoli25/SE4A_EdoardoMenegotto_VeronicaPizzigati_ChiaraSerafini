"""Persistenza SQLite degli eventi Edge."""

import json
import sqlite3
from datetime import datetime, timezone

from .models import EdgeEvent, EdgeEventCreate


class EdgeEventConflict(Exception):
    """Segnala il riuso di un event_id con contenuto differente."""


def _event_from_row(row: tuple) -> EdgeEvent:
    return EdgeEvent(
        event_id=row[0],
        edge_id=row[1],
        boot_id=row[2],
        zone_id=row[3],
        event_type=row[4],
        timestamp_seconds=row[5],
        recorded_at=row[6],
        received_at=row[7],
        payload=json.loads(row[8]),
    )


def save_event(
    connection: sqlite3.Connection,
    zone_id: str,
    event: EdgeEventCreate,
) -> EdgeEvent:
    """Salva una sola volta un evento o riproduce quello identico."""
    recorded_at = event.recorded_at.astimezone(timezone.utc)
    received_at = datetime.now(timezone.utc)
    payload_data = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
    try:
        connection.execute(
            """
            INSERT INTO edge_events (
                event_id, edge_id, boot_id, zone_id, event_type,
                timestamp_seconds, recorded_at, received_at, payload_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.edge_id,
                event.boot_id,
                zone_id,
                event.event_type,
                event.timestamp_seconds,
                recorded_at.isoformat(),
                received_at.isoformat(),
                payload_data,
            ),
        )
    except sqlite3.IntegrityError as error:
        existing = get_event(connection, event.event_id)
        if existing is not None:
            expected_data = event.model_dump()
            expected_data["recorded_at"] = recorded_at
            expected = EdgeEvent(
                **expected_data,
                zone_id=zone_id,
                received_at=existing.received_at,
            )
            if existing == expected:
                return existing
        raise EdgeEventConflict(
            f"event_id {event.event_id!r} already exists with different data"
        ) from error

    current_phase = event.payload.get("current_phase")
    if (
        event.event_type == "RecipePhaseChanged"
        and isinstance(current_phase, str)
        and current_phase.strip()
    ):
        connection.execute(
            """
            UPDATE zones
            SET status = 'online', last_edge_contact = ?, current_phase = ?
            WHERE id = ?
            """,
            (received_at.isoformat(), current_phase, zone_id),
        )
    else:
        connection.execute(
            """
            UPDATE zones
            SET status = 'online', last_edge_contact = ?
            WHERE id = ?
            """,
            (received_at.isoformat(), zone_id),
        )
    connection.commit()
    stored_data = event.model_dump()
    stored_data["recorded_at"] = recorded_at
    return EdgeEvent(
        **stored_data,
        zone_id=zone_id,
        received_at=received_at,
    )


def get_event(connection: sqlite3.Connection, event_id: str) -> EdgeEvent | None:
    row = connection.execute(
        """
        SELECT event_id, edge_id, boot_id, zone_id, event_type,
               timestamp_seconds, recorded_at, received_at, payload_data
        FROM edge_events
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    return None if row is None else _event_from_row(row)


def list_events(
    connection: sqlite3.Connection,
    zone_id: str,
    limit: int = 100,
) -> list[EdgeEvent]:
    rows = connection.execute(
        """
        SELECT event_id, edge_id, boot_id, zone_id, event_type,
               timestamp_seconds, recorded_at, received_at, payload_data
        FROM edge_events
        WHERE zone_id = ?
        ORDER BY recorded_at DESC, received_at DESC, event_id DESC
        LIMIT ?
        """,
        (zone_id, limit),
    ).fetchall()
    return [_event_from_row(row) for row in reversed(rows)]
