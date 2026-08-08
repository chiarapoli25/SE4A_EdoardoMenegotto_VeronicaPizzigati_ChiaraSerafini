"""@file repository.py
@brief Persistenza SQLite della telemetria dei sensori.
"""

import json
import sqlite3
from datetime import datetime, timezone

from ..zones.models import ControlSetpoints, ControlStrategies
from .models import TelemetryCreate, TelemetrySample


class TelemetryConflict(Exception):
    """@brief Segnala un progressivo di telemetria gia ricevuto."""


def save_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
    telemetry: TelemetryCreate,
) -> TelemetrySample:
    """@brief Salva un campione e aggiorna lo stato della zona."""
    active_row = connection.execute(
        "SELECT active_cultivation_id FROM zones WHERE id = ?",
        (zone_id,),
    ).fetchone()
    effective = telemetry.model_copy(
        update={
            "cultivation_id": telemetry.cultivation_id
            or (active_row[0] if active_row is not None else None)
        }
    )
    recorded_at = effective.recorded_at.astimezone(timezone.utc)
    received_at = datetime.now(timezone.utc)
    try:
        cursor = connection.execute(
            """
            INSERT INTO telemetry_samples (
                zone_id, boot_id, sequence_number, timestamp_seconds, recorded_at,
                received_at, temperature_c, air_humidity_percent,
                soil_moisture_percent, soil_bulk_ec_ms_cm, soil_ec_ms_cm,
                fertilizer_concentration_mg_per_liter,
                nitrogen_estimate_mg_per_liter,
                phosphorus_estimate_mg_per_liter,
                potassium_estimate_mg_per_liter,
                ph, light_ppfd_umol_m2_s, active_recipe_id,
                active_recipe_version, current_phase, operational_state,
                lifecycle_state, current_strategies, current_setpoints,
                time_scale, cultivation_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone_id,
                effective.boot_id,
                effective.sequence_number,
                effective.timestamp_seconds,
                recorded_at.isoformat(),
                received_at.isoformat(),
                effective.temperature_c,
                effective.air_humidity_percent,
                effective.soil_moisture_percent,
                effective.soil_bulk_ec_ms_cm,
                effective.soil_ec_ms_cm,
                effective.fertilizer_concentration_mg_per_liter,
                effective.nitrogen_estimate_mg_per_liter,
                effective.phosphorus_estimate_mg_per_liter,
                effective.potassium_estimate_mg_per_liter,
                effective.ph,
                effective.light_ppfd_umol_m2_s,
                effective.active_recipe_id,
                effective.active_recipe_version,
                effective.current_phase,
                effective.operational_state.value,
                effective.lifecycle_state.value,
                effective.current_strategies.model_dump_json(),
                effective.current_setpoints.model_dump_json(),
                effective.time_scale,
                effective.cultivation_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        existing = connection.execute(
            """
            SELECT id, zone_id, boot_id, sequence_number, timestamp_seconds,
                   recorded_at, received_at, temperature_c,
                   air_humidity_percent, soil_moisture_percent,
                   soil_bulk_ec_ms_cm, soil_ec_ms_cm,
                   fertilizer_concentration_mg_per_liter,
                   nitrogen_estimate_mg_per_liter,
                   phosphorus_estimate_mg_per_liter,
                   potassium_estimate_mg_per_liter, ph,
                   light_ppfd_umol_m2_s, active_recipe_id,
                   active_recipe_version, current_phase, operational_state,
                   lifecycle_state, current_strategies, current_setpoints,
                   time_scale, cultivation_id
            FROM telemetry_samples
            WHERE zone_id = ? AND boot_id = ? AND sequence_number = ?
            """,
            (zone_id, effective.boot_id, effective.sequence_number),
        ).fetchone()
        if existing is not None:
            stored = _telemetry_from_row(existing)
            comparable = stored.model_dump(
                exclude={"sample_id", "zone_id", "received_at"},
            )
            incoming = effective.model_dump()
            comparable["recorded_at"] = stored.recorded_at
            incoming["recorded_at"] = recorded_at
            if comparable == incoming:
                return stored
        raise TelemetryConflict(
            f"telemetry sequence {effective.sequence_number} already exists "
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
    connection.execute(
        """
        UPDATE zones
        SET lifecycle_state = ?, operational_state = ?,
            active_recipe_id = ?, active_recipe_version = ?,
            current_phase = ?, current_strategies = ?,
            current_setpoints = ?, time_scale = ?,
            projection_updated_at = ?
        WHERE id = ?
          AND (
              projection_updated_at IS NULL
              OR projection_updated_at <= ?
          )
        """,
        (
            effective.lifecycle_state.value,
            effective.operational_state.value,
            effective.active_recipe_id,
            effective.active_recipe_version,
            effective.current_phase,
            effective.current_strategies.model_dump_json(),
            effective.current_setpoints.model_dump_json(),
            effective.time_scale,
            recorded_at.isoformat(),
            zone_id,
            recorded_at.isoformat(),
        ),
    )
    connection.commit()
    stored_data = effective.model_dump()
    stored_data["recorded_at"] = recorded_at
    return TelemetrySample(
        **stored_data,
        sample_id=cursor.lastrowid,
        zone_id=zone_id,
        received_at=received_at,
    )


def _telemetry_from_row(row: tuple) -> TelemetrySample:
    """@brief Converte una riga SQLite in un campione di telemetria."""
    strategies = json.loads(row[23])
    setpoints = json.loads(row[24])
    strategy_snapshot = ControlStrategies.defaults().model_dump()
    strategy_snapshot.update(strategies)
    setpoint_snapshot = ControlSetpoints.zeros().model_dump()
    setpoint_snapshot.update(setpoints)
    return TelemetrySample(
        sample_id=row[0],
        zone_id=row[1],
        boot_id=row[2],
        sequence_number=row[3],
        timestamp_seconds=row[4],
        recorded_at=row[5],
        received_at=row[6],
        temperature_c=row[7],
        air_humidity_percent=row[8],
        soil_moisture_percent=row[9],
        soil_bulk_ec_ms_cm=row[10],
        soil_ec_ms_cm=row[11],
        fertilizer_concentration_mg_per_liter=row[12],
        nitrogen_estimate_mg_per_liter=row[13],
        phosphorus_estimate_mg_per_liter=row[14],
        potassium_estimate_mg_per_liter=row[15],
        ph=row[16],
        light_ppfd_umol_m2_s=row[17],
        active_recipe_id=row[18],
        active_recipe_version=row[19],
        current_phase=row[20],
        operational_state=row[21],
        lifecycle_state=row[22],
        current_strategies=strategy_snapshot,
        current_setpoints=setpoint_snapshot,
        time_scale=row[25],
        cultivation_id=row[26],
    )


def get_latest_telemetry(
    connection: sqlite3.Connection,
    zone_id: str,
) -> TelemetrySample | None:
    """@brief Recupera il campione piu recente di una zona."""
    row = connection.execute(
        """
        SELECT id, zone_id, boot_id, sequence_number, timestamp_seconds, recorded_at,
               received_at, temperature_c, air_humidity_percent,
               soil_moisture_percent, soil_bulk_ec_ms_cm, soil_ec_ms_cm,
               fertilizer_concentration_mg_per_liter,
               nitrogen_estimate_mg_per_liter,
               phosphorus_estimate_mg_per_liter,
               potassium_estimate_mg_per_liter,
               ph, light_ppfd_umol_m2_s, active_recipe_id,
               active_recipe_version, current_phase, operational_state,
               lifecycle_state, current_strategies, current_setpoints,
               time_scale, cultivation_id
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
    cultivation_id: str | None = None,
) -> list[TelemetrySample]:
    """@brief Legge lo storico recente in ordine cronologico."""
    conditions = ["zone_id = ?"]
    parameters: list[str | int] = [zone_id]
    if cultivation_id is not None:
        conditions.append("cultivation_id = ?")
        parameters.append(cultivation_id)
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
               received_at, temperature_c, air_humidity_percent,
               soil_moisture_percent, soil_bulk_ec_ms_cm, soil_ec_ms_cm,
               fertilizer_concentration_mg_per_liter,
               nitrogen_estimate_mg_per_liter,
               phosphorus_estimate_mg_per_liter,
               potassium_estimate_mg_per_liter,
               ph, light_ppfd_umol_m2_s, active_recipe_id,
               active_recipe_version, current_phase, operational_state,
               lifecycle_state, current_strategies, current_setpoints,
               time_scale, cultivation_id
        FROM telemetry_samples
        WHERE {" AND ".join(conditions)}
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [_telemetry_from_row(row) for row in reversed(rows)]
