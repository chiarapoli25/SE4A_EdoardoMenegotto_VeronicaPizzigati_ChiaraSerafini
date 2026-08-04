"""@file repository.py
@brief Persistenza SQLite dei reparti e dei settori.
"""

import json
import sqlite3

from pydantic import ValidationError

from .models import Zone, ZoneCreate, ZoneStatus, ZoneUpdate


class ZoneConflict(Exception):
    """@brief Segnala un identificativo o settore fisico gia occupato."""


class ZoneUpdateConflict(Exception):
    """@brief Segnala una modifica incompatibile con lo stato corrente."""


class ZoneUpdateInvalid(Exception):
    """@brief Segnala una modifica che viola i vincoli della zona."""


def create_zone(connection: sqlite3.Connection, zone: ZoneCreate) -> Zone:
    """@brief Registra un settore libero della serra.

    @param connection Connessione SQLite sulla quale salvare.
    @param zone Identita, posizione e specie del settore.
    @return Zona persistita con stato iniziale `offline`.
    @throws ZoneConflict Se id o posizione sono gia occupati.
    """
    stored_zone = Zone(**zone.model_dump())
    try:
        connection.execute(
            """
            INSERT INTO zones (
                id, name, department_number, sector_number, plant_species,
                assigned_edge_id, status, active_recipe_id,
                last_edge_contact, current_phase, cultivation_completed,
                administrative_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stored_zone.id,
                stored_zone.name,
                stored_zone.department_number,
                stored_zone.sector_number,
                stored_zone.plant_species,
                stored_zone.assigned_edge_id,
                stored_zone.status.value,
                stored_zone.active_recipe_id,
                stored_zone.last_edge_contact,
                stored_zone.current_phase,
                int(stored_zone.cultivation_completed),
                stored_zone.administrative_status.value,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise ZoneConflict(
            f"zone id {zone.id!r} already exists or department "
            f"{zone.department_number} sector {zone.sector_number} is occupied"
        ) from error
    connection.commit()
    return stored_zone


def _zone_from_row(row: tuple) -> Zone:
    """@brief Converte una riga SQLite nel modello di zona."""
    return Zone(
        id=row[0],
        name=row[1],
        department_number=row[2],
        sector_number=row[3],
        plant_species=row[4],
        assigned_edge_id=row[5],
        status=ZoneStatus(row[6]),
        active_recipe_id=row[7],
        last_edge_contact=row[8],
        current_phase=row[9],
        cultivation_completed=bool(row[10]),
        administrative_status=row[11],
    )


def get_zone(connection: sqlite3.Connection, zone_id: str) -> Zone | None:
    """@brief Recupera un settore tramite identificativo."""
    row = connection.execute(
        """
        SELECT id, name, department_number, sector_number, plant_species,
               assigned_edge_id, status, active_recipe_id,
               last_edge_contact, current_phase, cultivation_completed,
               administrative_status
        FROM zones
        WHERE id = ?
        """,
        (zone_id,),
    ).fetchone()
    if row is None:
        return None
    return _zone_from_row(row)


def list_zones(connection: sqlite3.Connection) -> list[Zone]:
    """@brief Elenca i settori ordinati per reparto e numero."""
    rows = connection.execute(
        """
        SELECT id, name, department_number, sector_number, plant_species,
               assigned_edge_id, status, active_recipe_id,
               last_edge_contact, current_phase, cultivation_completed,
               administrative_status
        FROM zones
        ORDER BY department_number, sector_number
        """
    ).fetchall()
    return [_zone_from_row(row) for row in rows]


def list_zones_for_edge(
    connection: sqlite3.Connection,
    edge_id: str,
) -> list[Zone]:
    """@brief Elenca i settori assegnati a uno specifico Edge."""
    rows = connection.execute(
        """
        SELECT id, name, department_number, sector_number, plant_species,
               assigned_edge_id, status, active_recipe_id,
               last_edge_contact, current_phase, cultivation_completed,
               administrative_status
        FROM zones
        WHERE assigned_edge_id = ?
        ORDER BY department_number, sector_number
        """,
        (edge_id,),
    ).fetchall()
    return [_zone_from_row(row) for row in rows]


def _zone_is_running(connection: sqlite3.Connection, zone_id: str) -> bool:
    """Restituisce lo stato Running osservato nell'ultimo evento lifecycle."""
    row = connection.execute(
        """
        SELECT payload_data
        FROM edge_events
        WHERE zone_id = ? AND event_type = 'ZoneLifecycleChanged'
        ORDER BY recorded_at DESC, received_at DESC, event_id DESC
        LIMIT 1
        """,
        (zone_id,),
    ).fetchone()
    if row is None:
        return False
    try:
        return json.loads(row[0]).get("current_state") == "Running"
    except (json.JSONDecodeError, AttributeError):
        return False


def _has_incompatible_plants(
    connection: sqlite3.Connection,
    zone_id: str,
    plant_species: str,
) -> bool:
    """Verifica esemplari presenti o destinati a tornare nella zona."""
    row = connection.execute(
        """
        SELECT 1
        FROM plants
        WHERE (home_zone_id = ? OR current_zone_id = ?)
          AND species <> ?
        LIMIT 1
        """,
        (zone_id, zone_id, plant_species),
    ).fetchone()
    return row is not None


def update_zone(
    connection: sqlite3.Connection,
    stored_zone: Zone,
    update: ZoneUpdate,
) -> Zone:
    """@brief Applica una modifica parziale dopo i controlli di dominio.

    @throws ZoneUpdateConflict Se specie o assegnazione Edge non sono sicure.
    @throws ZoneUpdateInvalid Se la modifica viola il ruolo del reparto.
    """
    changes = update.model_dump(exclude_unset=True)
    if not changes:
        return stored_zone

    if (
        "assigned_edge_id" in changes
        and changes["assigned_edge_id"] != stored_zone.assigned_edge_id
        and _zone_is_running(connection, stored_zone.id)
    ):
        raise ZoneUpdateConflict(
            "a Running zone cannot be reassigned without stopping it first"
        )

    if (
        "plant_species" in changes
        and changes["plant_species"] != stored_zone.plant_species
        and changes["plant_species"] is not None
        and _has_incompatible_plants(
            connection,
            stored_zone.id,
            changes["plant_species"],
        )
    ):
        raise ZoneUpdateConflict(
            "zone contains plants incompatible with the requested species"
        )

    candidate_data = stored_zone.model_dump()
    candidate_data.update(changes)
    try:
        candidate = Zone.model_validate(candidate_data)
    except ValidationError as error:
        raise ZoneUpdateInvalid(str(error)) from error

    try:
        connection.execute(
            """
            UPDATE zones
            SET name = ?, plant_species = ?, assigned_edge_id = ?,
                active_recipe_id = ?, administrative_status = ?
            WHERE id = ?
            """,
            (
                candidate.name,
                candidate.plant_species,
                candidate.assigned_edge_id,
                candidate.active_recipe_id,
                candidate.administrative_status.value,
                candidate.id,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise ZoneUpdateConflict(
            "the requested zone configuration conflicts with the physical layout"
        ) from error
    connection.commit()
    updated = get_zone(connection, candidate.id)
    assert updated is not None
    return updated
