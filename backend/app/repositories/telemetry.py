"""@file telemetry.py
@brief Persistenza SQLite della telemetria dei sensori.
"""

import sqlite3
from datetime import datetime, timezone

from ..models.telemetry import TelemetryCreate, TelemetrySample


class TelemetryConflict(Exception):
    """@brief Segnala un progressivo di telemetria gia ricevuto."""


def save_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
    telemetry: TelemetryCreate,
) -> TelemetrySample:
    """@brief Salva un campione e aggiorna lo stato della zona."""
    recorded_at = telemetry.recorded_at.astimezone(timezone.utc)
    received_at = datetime.now(timezone.utc)
    try:
        cursor = connection.execute(
            """
            INSERT INTO telemetry_samples (
                zone_id, sequence_number, timestamp_seconds, recorded_at,
                received_at, temperature_c, air_humidity_percent,
                soil_moisture_percent, ph, light_ppfd_umol_m2_s
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone_id,
                telemetry.sequence_number,
                telemetry.timestamp_seconds,
                recorded_at.isoformat(),
                received_at.isoformat(),
                telemetry.temperature_c,
                telemetry.air_humidity_percent,
                telemetry.soil_moisture_percent,
                telemetry.ph,
                telemetry.light_ppfd_umol_m2_s,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise TelemetryConflict(
            f"telemetry sequence {telemetry.sequence_number} already exists "
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
    stored_data = telemetry.model_dump()
    stored_data["recorded_at"] = recorded_at
    return TelemetrySample(
        **stored_data,
        sample_id=cursor.lastrowid,
        zone_id=zone_id,
        received_at=received_at,
    )


def _telemetry_from_row(row: tuple) -> TelemetrySample:
    """@brief Converte una riga SQLite in un campione di telemetria."""
    return TelemetrySample(
        sample_id=row[0],
        zone_id=row[1],
        sequence_number=row[2],
        timestamp_seconds=row[3],
        recorded_at=row[4],
        received_at=row[5],
        temperature_c=row[6],
        air_humidity_percent=row[7],
        soil_moisture_percent=row[8],
        ph=row[9],
        light_ppfd_umol_m2_s=row[10],
    )


def get_latest_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
) -> TelemetrySample | None:
    """@brief Recupera il campione piu recente di una zona."""
    row = connection.execute(
        """
        SELECT id, zone_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, temperature_c, air_humidity_percent,
               soil_moisture_percent, ph, light_ppfd_umol_m2_s
        FROM telemetry_samples
        WHERE zone_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
        """,
        (zone_id,),
    ).fetchone()
    if row is None:
        return None
    return _telemetry_from_row(row)


def list_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
    recorded_from: datetime | None = None,
    recorded_to: datetime | None = None,
    limit: int = 100,
) -> list[TelemetrySample]:
    """@brief Legge lo storico recente in ordine cronologico."""
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
        SELECT id, zone_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, temperature_c, air_humidity_percent,
               soil_moisture_percent, ph, light_ppfd_umol_m2_s
        FROM telemetry_samples
        WHERE {" AND ".join(conditions)}
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [_telemetry_from_row(row) for row in reversed(rows)]
