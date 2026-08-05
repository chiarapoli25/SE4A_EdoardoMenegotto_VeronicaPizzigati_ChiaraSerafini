"""Persistenza SQLite degli eventi Edge."""

import json
import sqlite3
from datetime import datetime, timezone

from .models import EdgeEvent, EdgeEventCreate


class EdgeEventConflict(Exception):
    """Segnala il riuso di un event_id con contenuto differente."""


def _phase_name(payload: dict, key: str) -> str | None:
    """Estrae un nome fase sicuro dai payload Edge non tipizzati."""
    value = payload.get(key)
    if isinstance(value, str) and 0 < len(value) <= 100:
        return value
    return None


def _apply_recipe_state(
    connection: sqlite3.Connection,
    zone_id: str,
    event: EdgeEventCreate,
) -> None:
    """Aggiorna subito la proiezione corrente, mantenendo l'evento storico."""
    projection_time = event.recorded_at.astimezone(timezone.utc).isoformat()
    stored_projection = connection.execute(
        "SELECT projection_updated_at FROM zones WHERE id = ?",
        (zone_id,),
    ).fetchone()
    if (
        stored_projection is not None
        and stored_projection[0] is not None
        and stored_projection[0] > projection_time
    ):
        return

    projected = False
    if event.event_type == "ZoneLifecycleChanged":
        previous_state = event.payload.get("previous_state")
        current_state = event.payload.get("current_state")
        if current_state in {"Idle", "Running", "Paused", "Error"}:
            connection.execute(
                "UPDATE zones SET lifecycle_state = ? WHERE id = ?",
                (current_state, zone_id),
            )
            projected = True
        if previous_state == "Starting" and current_state == "Running":
            connection.execute(
                """
                UPDATE zones
                SET current_phase = NULL, cultivation_completed = 0
                WHERE id = ?
                """,
                (zone_id,),
            )
            projected = True
        elif current_state == "Idle":
            connection.execute(
                """
                UPDATE zones
                SET active_recipe_id = NULL, active_recipe_version = NULL,
                    current_phase = NULL, cultivation_completed = 0,
                    time_scale = 1.0
                WHERE id = ?
                """,
                (zone_id,),
            )
            projected = True
    elif event.event_type == "StateChanged":
        current_state = event.payload.get("current_state")
        if current_state in {"Nominal", "Degraded", "EmergencyLockdown"}:
            connection.execute(
                "UPDATE zones SET operational_state = ? WHERE id = ?",
                (current_state, zone_id),
            )
            projected = True
    elif event.event_type == "SimulationSpeedChanged":
        current_scale = event.payload.get("current_time_scale")
        if (
            isinstance(current_scale, (int, float))
            and 1.0 <= current_scale <= 60.0
        ):
            connection.execute(
                "UPDATE zones SET time_scale = ? WHERE id = ?",
                (float(current_scale), zone_id),
            )
            projected = True
    elif event.event_type == "StrategyChanged":
        variable = event.payload.get("variable")
        current_strategy = event.payload.get("current_strategy")
        if variable in {
            "soil_moisture",
            "light",
            "ph",
            "nitrogen",
            "phosphorus",
            "potassium",
        } and current_strategy in {"Threshold", "PID", "Predictive"}:
            row = connection.execute(
                "SELECT current_strategies FROM zones WHERE id = ?",
                (zone_id,),
            ).fetchone()
            strategies = json.loads(row[0]) if row is not None else {}
            strategies[variable] = current_strategy
            connection.execute(
                "UPDATE zones SET current_strategies = ? WHERE id = ?",
                (json.dumps(strategies, sort_keys=True), zone_id),
            )
            projected = True
    elif event.event_type == "RecipePhaseChanged":
        current_phase = _phase_name(event.payload, "current_phase")
        if current_phase is not None:
            connection.execute(
                """
                UPDATE zones
                SET current_phase = ?, cultivation_completed = 0
                WHERE id = ?
                """,
                (current_phase, zone_id),
            )
            projected = True
    elif event.event_type == "RecipeCompleted":
        final_phase = _phase_name(event.payload, "final_phase")
        if final_phase is not None:
            connection.execute(
                """
                UPDATE zones
                SET current_phase = ?, cultivation_completed = 1
                WHERE id = ?
                """,
                (final_phase, zone_id),
            )
            projected = True

    if projected:
        connection.execute(
            "UPDATE zones SET projection_updated_at = ? WHERE id = ?",
            (projection_time, zone_id),
        )


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

    _apply_recipe_state(connection, zone_id, event)
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
