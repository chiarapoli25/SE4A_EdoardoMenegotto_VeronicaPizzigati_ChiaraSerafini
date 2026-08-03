"""@file repository.py
@brief Persistenza SQLite dei reparti e dei settori.
"""

import sqlite3

from .models import Zone, ZoneCreate, ZoneStatus


class ZoneConflict(Exception):
    """@brief Segnala un identificativo o settore fisico gia occupato."""


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
                last_edge_contact, current_phase
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    )


def get_zone(connection: sqlite3.Connection, zone_id: str) -> Zone | None:
    """@brief Recupera un settore tramite identificativo."""
    row = connection.execute(
        """
        SELECT id, name, department_number, sector_number, plant_species,
               assigned_edge_id, status, active_recipe_id,
               last_edge_contact, current_phase
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
               last_edge_contact, current_phase
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
               last_edge_contact, current_phase
        FROM zones
        WHERE assigned_edge_id = ?
        ORDER BY department_number, sector_number
        """,
        (edge_id,),
    ).fetchall()
    return [_zone_from_row(row) for row in rows]
