"""Persistenza SQLite dei comandi destinati alle zone."""

import json
import sqlite3
from datetime import datetime, timezone

from .models import (
    CommandStatus,
    RuntimeCommand,
    RuntimeCommandCreate,
    RuntimeCommandResultCreate,
)


class RuntimeCommandConflict(Exception):
    """Segnala un command_id riutilizzato con dati incompatibili."""


def _command_from_row(row: tuple) -> RuntimeCommand:
    return RuntimeCommand(
        command_id=row[0],
        zone_id=row[1],
        command_type=row[2],
        payload=json.loads(row[3]),
        status=row[4],
        created_at=row[5],
        completed_at=row[6],
        result_message=row[7],
        result_replayed=None if row[8] is None else bool(row[8]),
    )


def get_command(
    connection: sqlite3.Connection,
    command_id: str,
) -> RuntimeCommand | None:
    row = connection.execute(
        """
        SELECT command_id, zone_id, command_type, payload_data, status,
               created_at, completed_at, result_message, result_replayed
        FROM runtime_commands
        WHERE command_id = ?
        """,
        (command_id,),
    ).fetchone()
    return None if row is None else _command_from_row(row)


def create_command(
    connection: sqlite3.Connection,
    zone_id: str,
    command: RuntimeCommandCreate,
    *,
    commit: bool = True,
) -> RuntimeCommand:
    created_at = datetime.now(timezone.utc)
    payload_data = json.dumps(command.payload, sort_keys=True, separators=(",", ":"))
    try:
        connection.execute(
            """
            INSERT INTO runtime_commands (
                command_id, zone_id, command_type, payload_data, status,
                created_at
            )
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                command.command_id,
                zone_id,
                command.command_type.value,
                payload_data,
                created_at.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        existing = get_command(connection, command.command_id)
        if (
            existing is not None
            and existing.zone_id == zone_id
            and existing.command_type == command.command_type
            and existing.payload == command.payload
        ):
            return existing
        raise RuntimeCommandConflict(
            f"command_id {command.command_id!r} already exists with different data"
        ) from error
    if commit:
        connection.commit()
    stored = get_command(connection, command.command_id)
    assert stored is not None
    return stored


def list_pending_commands(
    connection: sqlite3.Connection,
    zone_id: str,
    limit: int = 100,
) -> list[RuntimeCommand]:
    rows = connection.execute(
        """
        SELECT command_id, zone_id, command_type, payload_data, status,
               created_at, completed_at, result_message, result_replayed
        FROM runtime_commands
        WHERE zone_id = ? AND status = 'pending'
        ORDER BY created_at, command_id
        LIMIT ?
        """,
        (zone_id, limit),
    ).fetchall()
    return [_command_from_row(row) for row in rows]


def complete_command(
    connection: sqlite3.Connection,
    zone_id: str,
    command_id: str,
    result: RuntimeCommandResultCreate,
) -> RuntimeCommand | None:
    existing = get_command(connection, command_id)
    if existing is None or existing.zone_id != zone_id:
        return None
    if existing.status is not CommandStatus.PENDING:
        if (
            existing.status is result.status
            and existing.result_message == result.message
            and existing.result_replayed == result.replayed
        ):
            return existing
        raise RuntimeCommandConflict(
            f"command {command_id!r} already has a different final result"
        )
    completed_at = datetime.now(timezone.utc)
    connection.execute(
        """
        UPDATE runtime_commands
        SET status = ?, completed_at = ?, result_message = ?,
            result_replayed = ?
        WHERE command_id = ? AND zone_id = ?
        """,
        (
            result.status.value,
            completed_at.isoformat(),
            result.message,
            int(result.replayed),
            command_id,
            zone_id,
        ),
    )
    if (
        result.status is CommandStatus.SUCCEEDED
        and existing.command_type.value
        in {"ActivateCultivation", "LoadRecipe"}
    ):
        recipe_id = existing.payload.get("recipe_id")
        if isinstance(recipe_id, str) and recipe_id:
            connection.execute(
                """
                UPDATE zones
                SET active_recipe_id = ?, current_phase = NULL,
                    cultivation_completed = 0
                WHERE id = ?
                """,
                (recipe_id, zone_id),
            )
    from ..cultivations.repository import apply_command_result

    apply_command_result(
        connection,
        zone_id=zone_id,
        command_type=existing.command_type.value,
        result=result,
    )
    connection.commit()
    return get_command(connection, command_id)
