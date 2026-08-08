"""Persistenza e transizioni dei cicli colturali."""

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from ..commands.models import CommandStatus, RuntimeCommandCreate, RuntimeCommandResultCreate
from ..commands.repository import create_command
from ..recipes.repository import get_recipe
from ..zones.repository import get_zone
from .models import Cultivation, CultivationAction, CultivationCreate, CultivationState


class CultivationConflict(Exception):
    """La zona o il ciclo non consentono la transizione richiesta."""


class CultivationInvalid(Exception):
    """Specie, ricetta o reparto non sono compatibili."""


def _from_row(row: tuple) -> Cultivation:
    return Cultivation(
        id=row[0],
        zone_id=row[1],
        plant_species=row[2],
        recipe_id=row[3],
        recipe_version=row[4],
        state=row[5],
        recipe_completed=bool(row[6]),
        created_at=row[7],
        started_at=row[8],
        ended_at=row[9],
        archived_at=row[10],
        last_command_id=row[11],
    )


_COLUMNS = """
    id, zone_id, plant_species, recipe_id, recipe_version, state,
    recipe_completed, created_at, started_at, ended_at, archived_at,
    last_command_id
"""


def get_cultivation(
    connection: sqlite3.Connection,
    cultivation_id: str,
) -> Cultivation | None:
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM cultivations WHERE id = ?",
        (cultivation_id,),
    ).fetchone()
    return None if row is None else _from_row(row)


def list_cultivations(
    connection: sqlite3.Connection,
    *,
    zone_id: str | None = None,
    include_archived: bool = True,
) -> list[Cultivation]:
    conditions: list[str] = []
    parameters: list[str] = []
    if zone_id is not None:
        conditions.append("zone_id = ?")
        parameters.append(zone_id)
    if not include_archived:
        conditions.append("archived_at IS NULL")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM cultivations {where} ORDER BY created_at DESC",
        parameters,
    ).fetchall()
    return [_from_row(row) for row in rows]


def create_and_activate(
    connection: sqlite3.Connection,
    request: CultivationCreate,
) -> CultivationAction:
    zone = get_zone(connection, request.zone_id)
    if zone is None:
        raise CultivationInvalid(f"zone {request.zone_id!r} not found")
    if zone.department_number == 5:
        raise CultivationInvalid("quarantine zones cannot host a cultivation")
    if zone.administrative_status.value != "active":
        raise CultivationConflict("the selected zone is not administratively active")
    if zone.active_cultivation_id is not None:
        raise CultivationConflict("il settore selezionato ha gia una coltivazione attiva")

    lifecycle_can_activate = zone.lifecycle_state.value in {"Idle", "Error"}
    stale_offline_lifecycle = not lifecycle_can_activate and zone.status.value == "offline"
    if not lifecycle_can_activate and not stale_offline_lifecycle:
        raise CultivationConflict(
            "il settore risulta ancora in esecuzione sull'Edge; "
            "termina il ciclo corrente o attendi la sincronizzazione"
        )

    recipe = get_recipe(connection, request.recipe_id)
    if recipe is None:
        raise CultivationInvalid(f"recipe {request.recipe_id!r} not found")
    if recipe.department_number not in {None, zone.department_number}:
        raise CultivationInvalid("the recipe belongs to a different department")
    if recipe.plant_type != zone.plant_species:
        raise CultivationInvalid("the recipe species does not match the zone")

    cultivation_id = f"cultivation-{uuid4().hex[:16]}"
    command_id = f"activate-{uuid4().hex}"
    created_at = datetime.now(timezone.utc).isoformat()
    command_create = RuntimeCommandCreate(
        command_id=command_id,
        command_type="ActivateCultivation",
        payload={
            "cultivation_id": cultivation_id,
            "recipe_id": recipe.id,
        },
    )
    try:
        if stale_offline_lifecycle:
            # I database creati prima dell'introduzione di Cultivation possono
            # conservare Running/Paused anche quando l'Edge e offline e non
            # esiste alcun ciclo associato. Il riallineamento fa parte della
            # stessa transazione che crea e accoda il nuovo ciclo.
            connection.execute(
                """
                UPDATE zones
                SET lifecycle_state = 'Idle', current_phase = NULL
                WHERE id = ? AND active_cultivation_id IS NULL
                """,
                (zone.id,),
            )
        connection.execute(
            """
            INSERT INTO cultivations (
                id, zone_id, plant_species, recipe_id, recipe_version,
                state, recipe_completed, created_at, last_command_id
            )
            VALUES (?, ?, ?, ?, ?, 'activating', 0, ?, ?)
            """,
            (
                cultivation_id,
                zone.id,
                recipe.plant_type,
                recipe.id,
                recipe.version,
                created_at,
                command_id,
            ),
        )
        connection.execute(
            """
            UPDATE zones
            SET active_cultivation_id = ?, active_recipe_id = ?,
                cultivation_completed = 0
            WHERE id = ?
            """,
            (cultivation_id, recipe.id, zone.id),
        )
        command = create_command(
            connection,
            zone.id,
            command_create,
            commit=False,
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise CultivationConflict(
            "il settore selezionato ha gia una coltivazione non archiviata"
        ) from error
    except Exception:
        connection.rollback()
        raise

    cultivation = get_cultivation(connection, cultivation_id)
    assert cultivation is not None
    return CultivationAction(cultivation=cultivation, command=command)


def _enqueue_action(
    connection: sqlite3.Connection,
    cultivation_id: str,
    *,
    command_type: str,
    next_state: CultivationState,
    allowed_states: set[CultivationState],
) -> CultivationAction:
    cultivation = get_cultivation(connection, cultivation_id)
    if cultivation is None:
        raise CultivationInvalid(f"cultivation {cultivation_id!r} not found")
    if cultivation.state not in allowed_states:
        raise CultivationConflict(
            f"cultivation cannot execute {command_type} while {cultivation.state.value}"
        )
    command_id = f"{command_type.lower()}-{uuid4().hex}"
    command_create = RuntimeCommandCreate(
        command_id=command_id,
        command_type=command_type,
        payload={},
    )
    try:
        connection.execute(
            "UPDATE cultivations SET state = ?, last_command_id = ? WHERE id = ?",
            (next_state.value, command_id, cultivation_id),
        )
        command = create_command(
            connection,
            cultivation.zone_id,
            command_create,
            commit=False,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    updated = get_cultivation(connection, cultivation_id)
    assert updated is not None
    return CultivationAction(cultivation=updated, command=command)


def pause_cultivation(connection: sqlite3.Connection, cultivation_id: str) -> CultivationAction:
    return _enqueue_action(
        connection,
        cultivation_id,
        command_type="PauseCultivation",
        next_state=CultivationState.PAUSING,
        allowed_states={CultivationState.RUNNING},
    )


def resume_cultivation(connection: sqlite3.Connection, cultivation_id: str) -> CultivationAction:
    return _enqueue_action(
        connection,
        cultivation_id,
        command_type="ResumeCultivation",
        next_state=CultivationState.RESUMING,
        allowed_states={CultivationState.PAUSED},
    )


def terminate_cultivation(connection: sqlite3.Connection, cultivation_id: str) -> CultivationAction:
    return _enqueue_action(
        connection,
        cultivation_id,
        command_type="StopCultivation",
        next_state=CultivationState.STOPPING,
        allowed_states={
            CultivationState.ACTIVATING,
            CultivationState.RUNNING,
            CultivationState.PAUSED,
            CultivationState.ERROR,
        },
    )


def apply_command_result(
    connection: sqlite3.Connection,
    *,
    zone_id: str,
    command_type: str,
    result: RuntimeCommandResultCreate,
) -> None:
    row = connection.execute(
        "SELECT active_cultivation_id FROM zones WHERE id = ?",
        (zone_id,),
    ).fetchone()
    cultivation_id = row[0] if row is not None else None
    if cultivation_id is None:
        return
    if result.status is CommandStatus.REJECTED:
        connection.execute(
            "UPDATE cultivations SET state = 'error' WHERE id = ?",
            (cultivation_id,),
        )
        return
    now = datetime.now(timezone.utc).isoformat()
    if command_type == "ActivateCultivation":
        connection.execute(
            """
            UPDATE cultivations
            SET state = 'running', started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (now, cultivation_id),
        )
        connection.execute(
            "UPDATE zones SET lifecycle_state = 'Running' WHERE id = ?",
            (zone_id,),
        )
    elif command_type == "PauseCultivation":
        connection.execute(
            "UPDATE cultivations SET state = 'paused' WHERE id = ?",
            (cultivation_id,),
        )
        connection.execute(
            "UPDATE zones SET lifecycle_state = 'Paused' WHERE id = ?",
            (zone_id,),
        )
    elif command_type == "ResumeCultivation":
        connection.execute(
            "UPDATE cultivations SET state = 'running' WHERE id = ?",
            (cultivation_id,),
        )
        connection.execute(
            "UPDATE zones SET lifecycle_state = 'Running' WHERE id = ?",
            (zone_id,),
        )
    elif command_type == "StopCultivation":
        connection.execute(
            """
            UPDATE cultivations
            SET state = 'archived', ended_at = ?, archived_at = ?
            WHERE id = ?
            """,
            (now, now, cultivation_id),
        )
        connection.execute(
            """
            UPDATE zones
            SET active_cultivation_id = NULL, active_recipe_id = NULL,
                active_recipe_version = NULL, current_phase = NULL,
                cultivation_completed = 0, time_scale = 1.0,
                lifecycle_state = 'Idle'
            WHERE id = ?
            """,
            (zone_id,),
        )


def apply_edge_projection(
    connection: sqlite3.Connection,
    zone_id: str,
    *,
    lifecycle_state: str | None = None,
    recipe_completed: bool | None = None,
) -> None:
    row = connection.execute(
        "SELECT active_cultivation_id FROM zones WHERE id = ?",
        (zone_id,),
    ).fetchone()
    cultivation_id = row[0] if row is not None else None
    if cultivation_id is None:
        return
    if recipe_completed is not None:
        connection.execute(
            "UPDATE cultivations SET recipe_completed = ? WHERE id = ?",
            (int(recipe_completed), cultivation_id),
        )
    state_map = {"Running": "running", "Paused": "paused", "Error": "error"}
    if lifecycle_state in state_map:
        connection.execute(
            "UPDATE cultivations SET state = ? WHERE id = ?",
            (state_map[lifecycle_state], cultivation_id),
        )
    elif lifecycle_state == "Idle":
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            UPDATE cultivations
            SET state = 'archived', ended_at = COALESCE(ended_at, ?),
                archived_at = COALESCE(archived_at, ?)
            WHERE id = ?
            """,
            (now, now, cultivation_id),
        )
        connection.execute(
            "UPDATE zones SET active_cultivation_id = NULL WHERE id = ?",
            (zone_id,),
        )
