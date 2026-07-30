"""@file repository.py
@brief Persistenza SQLite degli snapshot degli attuatori.
"""

import sqlite3
from datetime import datetime, timezone

from .models import (
    ActuatorCommandState,
    ActuatorPhysicalOutput,
    ActuatorSnapshot,
    ActuatorSnapshotCreate,
)


class ActuatorSnapshotConflict(Exception):
    """@brief Segnala un progressivo attuatori gia ricevuto."""


def save_actuator_snapshot(
    connection: sqlite3.Connection,
    zone_id: str,
    snapshot: ActuatorSnapshotCreate,
) -> ActuatorSnapshot:
    """@brief Salva comando e uscita fisica degli attuatori.

    @param connection Connessione SQLite sulla quale scrivere.
    @param zone_id Zona che ha prodotto lo snapshot.
    @param snapshot Comando e uscita validati.
    @return Snapshot persistito con identificativi e tempo di ricezione.
    @throws ActuatorSnapshotConflict Se il progressivo e gia presente.
    """
    recorded_at = snapshot.recorded_at.astimezone(timezone.utc)
    received_at = datetime.now(timezone.utc)
    try:
        cursor = connection.execute(
            """
            INSERT INTO actuator_snapshots (
                zone_id, boot_id, sequence_number, timestamp_seconds, recorded_at,
                received_at, command_data, output_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone_id,
                snapshot.boot_id,
                snapshot.sequence_number,
                snapshot.timestamp_seconds,
                recorded_at.isoformat(),
                received_at.isoformat(),
                snapshot.command.model_dump_json(by_alias=True),
                snapshot.output.model_dump_json(by_alias=True),
            ),
        )
    except sqlite3.IntegrityError as error:
        existing = connection.execute(
            """
            SELECT id, zone_id, boot_id, sequence_number, timestamp_seconds,
                   recorded_at, received_at, command_data, output_data
            FROM actuator_snapshots
            WHERE zone_id = ? AND boot_id = ? AND sequence_number = ?
            """,
            (zone_id, snapshot.boot_id, snapshot.sequence_number),
        ).fetchone()
        if existing is not None:
            stored = _snapshot_from_row(existing)
            comparable = stored.model_dump(
                exclude={"snapshot_id", "zone_id", "received_at"},
            )
            incoming = snapshot.model_dump()
            comparable["recorded_at"] = stored.recorded_at
            incoming["recorded_at"] = recorded_at
            if comparable == incoming:
                return stored
        raise ActuatorSnapshotConflict(
            f"actuator sequence {snapshot.sequence_number} already exists "
            f"for zone {zone_id!r}"
        ) from error

    connection.execute(
        """
        UPDATE zones
        SET status = 'online', last_edge_contact = ?
        WHERE id = ?
        """,
        (received_at.isoformat(), zone_id),
    )
    connection.commit()
    stored_data = snapshot.model_dump()
    stored_data["recorded_at"] = recorded_at
    return ActuatorSnapshot(
        **stored_data,
        snapshot_id=cursor.lastrowid,
        zone_id=zone_id,
        received_at=received_at,
    )


def _snapshot_from_row(row: tuple) -> ActuatorSnapshot:
    """@brief Converte una riga SQLite in uno snapshot degli attuatori."""
    return ActuatorSnapshot(
        snapshot_id=row[0],
        zone_id=row[1],
        boot_id=row[2],
        sequence_number=row[3],
        timestamp_seconds=row[4],
        recorded_at=row[5],
        received_at=row[6],
        command=ActuatorCommandState.model_validate_json(row[7]),
        output=ActuatorPhysicalOutput.model_validate_json(row[8]),
    )


def get_latest_actuator_snapshot(
    connection: sqlite3.Connection,
    zone_id: str,
) -> ActuatorSnapshot | None:
    """@brief Recupera lo snapshot attuatori piu recente."""
    row = connection.execute(
        """
        SELECT id, zone_id, boot_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, command_data, output_data
        FROM actuator_snapshots
        WHERE zone_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
        """,
        (zone_id,),
    ).fetchone()
    if row is None:
        return None
    return _snapshot_from_row(row)


def list_actuator_snapshots(
    connection: sqlite3.Connection,
    zone_id: str,
    recorded_from: datetime | None = None,
    recorded_to: datetime | None = None,
    limit: int = 100,
) -> list[ActuatorSnapshot]:
    """@brief Legge lo storico recente degli attuatori."""
    conditions = ["zone_id = ?"]
    parameters: list[str | int] = [zone_id]
    if recorded_from is not None:
        conditions.append("recorded_at >= ?")
        parameters.append(recorded_from.astimezone(timezone.utc).isoformat())
    if recorded_to is not None:
        conditions.append("recorded_at <= ?")
        parameters.append(recorded_to.astimezone(timezone.utc).isoformat())
    parameters.append(limit)

    rows = connection.execute(
        f"""
        SELECT id, zone_id, boot_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, command_data, output_data
        FROM actuator_snapshots
        WHERE {" AND ".join(conditions)}
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [_snapshot_from_row(row) for row in reversed(rows)]
