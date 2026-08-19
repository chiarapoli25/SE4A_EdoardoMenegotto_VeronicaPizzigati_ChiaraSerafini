"""@file repository.py
@brief Persistenza SQLite del log di audit.
"""

import json
import sqlite3
from datetime import datetime, timezone

from .models import AuditLogEntry, AuditOutcome

_COLUMNS = (
    "id, occurred_at, actor_username, actor_role, action, outcome, "
    "resource_type, resource_id, detail_data"
)


def _entry_from_row(row: tuple) -> AuditLogEntry:
    return AuditLogEntry(
        id=row[0],
        occurred_at=row[1],
        actor_username=row[2],
        actor_role=row[3],
        action=row[4],
        outcome=AuditOutcome(row[5]),
        resource_type=row[6],
        resource_id=row[7],
        detail=None if row[8] is None else json.loads(row[8]),
    )


def record_audit_event(
    connection: sqlite3.Connection,
    *,
    action: str,
    outcome: AuditOutcome,
    actor_username: str | None = None,
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
) -> AuditLogEntry:
    """@brief Registra una voce nel log di audit.

    @details Non solleva mai un'eccezione di dominio: un log fallito non deve
    impedire l'operazione che sta documentando. E' comunque responsabilita
    del chiamante decidere se propagare un errore SQLite imprevisto (qui non
    viene intercettato, cosi resta visibile durante lo sviluppo/i test).

    @param action Azione eseguita, convenzionalmente `<dominio>.<verbo>`.
    @param outcome Esito dell'operazione.
    @param actor_username Utente autenticato che ha agito, se noto.
    @param actor_role Ruolo dell'utente al momento dell'azione, se noto.
    @param resource_type Tipo dell'oggetto coinvolto, se pertinente.
    @param resource_id Identificativo dell'oggetto coinvolto, se pertinente.
    @param detail Contesto aggiuntivo libero, serializzato come JSON.
    @return La voce appena persistita, con `id` e `occurred_at` assegnati.
    """
    occurred_at = datetime.now(timezone.utc)
    detail_data = (
        None if detail is None
        else json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)
    )
    cursor = connection.execute(
        """
        INSERT INTO audit_log (
            occurred_at, actor_username, actor_role, action, outcome,
            resource_type, resource_id, detail_data
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occurred_at.isoformat(),
            actor_username,
            actor_role,
            action,
            outcome.value,
            resource_type,
            resource_id,
            detail_data,
        ),
    )
    connection.commit()
    return AuditLogEntry(
        id=cursor.lastrowid,
        occurred_at=occurred_at,
        actor_username=actor_username,
        actor_role=actor_role,
        action=action,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )


def list_audit_events(
    connection: sqlite3.Connection,
    *,
    actor_username: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: AuditOutcome | None = None,
    limit: int = 100,
) -> list[AuditLogEntry]:
    """@brief Elenca le voci di audit, piu recenti prima, con filtri opzionali.

    @details Tutti i filtri sono in AND fra loro e ignorati se `None`.
    """
    conditions = []
    parameters: list = []
    if actor_username is not None:
        conditions.append("actor_username = ?")
        parameters.append(actor_username)
    if action is not None:
        conditions.append("action = ?")
        parameters.append(action)
    if resource_type is not None:
        conditions.append("resource_type = ?")
        parameters.append(resource_type)
    if resource_id is not None:
        conditions.append("resource_id = ?")
        parameters.append(resource_id)
    if outcome is not None:
        conditions.append("outcome = ?")
        parameters.append(outcome.value)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS} FROM audit_log
        {where_clause}
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [_entry_from_row(row) for row in rows]
