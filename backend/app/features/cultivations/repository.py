"""@file repository.py
@brief Persistenza SQLite delle coltivazioni.

@details Le query di conferma, attivazione, pausa e conclusione sono
transazionali: ogni funzione legge lo stato corrente, verifica la
transizione richiesta e scrive il nuovo stato nella stessa chiamata, cosi da
non lasciare mai una coltivazione in uno stato intermedio incoerente.
L'indice univoco parziale creato in `core.database` garantisce comunque,
anche in presenza di richieste concorrenti, che un settore non abbia mai
piu di una coltivazione non conclusa.
"""

import sqlite3
from datetime import datetime, timezone

from ..recipes.repository import get_recipe
from ..zones.repository import get_zone
from .models import (
    Cultivation,
    CultivationActivationResult,
    CultivationConfirm,
    CultivationCreate,
    CultivationProgress,
    CultivationStatus,
)


class CultivationConflict(Exception):
    """@brief Segnala un identificativo gia usato o un settore gia occupato."""


class CultivationCompatibilityError(Exception):
    """@brief Segnala settore, specie, ricetta o substrato incompatibili."""


class CultivationStateError(Exception):
    """@brief Segnala una transizione di stato non ammessa."""


_COLUMNS = (
    "id, zone_id, plant_species, recipe_id, recipe_version, status, "
    "created_by, created_at, confirmed_at, started_at, completed_at, "
    "elapsed_simulation_seconds, requested_time_scale, applied_time_scale, "
    "error_message"
)

## @brief Stati che non occupano piu in modo esclusivo il settore.
_CONCLUDED_STATUSES = (CultivationStatus.COMPLETED.value, CultivationStatus.FAILED.value)

## @brief Stati per i quali una ricetta e stata davvero eseguita sul settore.
_RUN_STATUSES = (
    CultivationStatus.ACTIVE.value,
    CultivationStatus.PAUSED.value,
    CultivationStatus.COMPLETED.value,
)


def _cultivation_from_row(row: tuple) -> Cultivation:
    """@brief Converte una riga SQLite nel modello di coltivazione."""
    return Cultivation(
        id=row[0],
        zone_id=row[1],
        plant_species=row[2],
        recipe_id=row[3],
        recipe_version=row[4],
        status=CultivationStatus(row[5]),
        created_by=row[6],
        created_at=row[7],
        confirmed_at=row[8],
        started_at=row[9],
        completed_at=row[10],
        elapsed_simulation_seconds=row[11],
        requested_time_scale=row[12],
        applied_time_scale=row[13],
        error_message=row[14],
    )


def get_cultivation(
    connection: sqlite3.Connection, cultivation_id: str
) -> Cultivation | None:
    """@brief Recupera una coltivazione tramite identificativo."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM cultivations WHERE id = ?", (cultivation_id,)
    ).fetchone()
    return None if row is None else _cultivation_from_row(row)


def list_cultivations(
    connection: sqlite3.Connection, zone_id: str | None = None
) -> list[Cultivation]:
    """@brief Elenca le coltivazioni, opzionalmente filtrate per settore.

    @param zone_id Settore da filtrare, oppure `None` per tutte le zone.
    @return Coltivazioni ordinate per data di creazione.
    """
    if zone_id is None:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM cultivations ORDER BY created_at, id"
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM cultivations "
            "WHERE zone_id = ? ORDER BY created_at, id",
            (zone_id,),
        ).fetchall()
    return [_cultivation_from_row(row) for row in rows]


def get_active_cultivation_for_zone(
    connection: sqlite3.Connection, zone_id: str
) -> Cultivation | None:
    """@brief Restituisce la coltivazione non conclusa del settore, se presente.

    @details Corrisponde alla stessa condizione applicata dall'indice
    univoco parziale su `cultivations(zone_id)`.
    """
    row = connection.execute(
        f"""
        SELECT {_COLUMNS} FROM cultivations
        WHERE zone_id = ? AND status NOT IN (?, ?)
        """,
        (zone_id, *_CONCLUDED_STATUSES),
    ).fetchone()
    return None if row is None else _cultivation_from_row(row)


def _last_run_substrate(
    connection: sqlite3.Connection, zone_id: str, exclude_cultivation_id: str
) -> str | None:
    """@brief Substrato dell'ultima coltivazione realmente eseguita sul settore.

    @return Il valore `substrate` della ricetta associata, oppure `None` se
        il settore non ha ancora eseguito nessuna coltivazione.
    """
    row = connection.execute(
        f"""
        SELECT recipe_id, recipe_version FROM cultivations
        WHERE zone_id = ? AND id != ? AND status IN (?, ?, ?)
        ORDER BY COALESCE(started_at, confirmed_at, created_at) DESC
        LIMIT 1
        """,
        (zone_id, exclude_cultivation_id, *_RUN_STATUSES),
    ).fetchone()
    if row is None:
        return None
    previous_recipe = get_recipe(connection, row[0], row[1])
    return None if previous_recipe is None else previous_recipe.substrate.value


def create_cultivation(
    connection: sqlite3.Connection, cultivation: CultivationCreate
) -> Cultivation:
    """@brief Salva una nuova bozza di coltivazione.

    @param connection Connessione SQLite sulla quale salvare.
    @param cultivation Settore, specie e ricetta desiderati per il ciclo.
    @return Coltivazione persistita con stato iniziale `draft`.
    @throws CultivationConflict Se l'id e gia usato o il settore ha gia una
        coltivazione non conclusa.
    """
    created_at = datetime.now(timezone.utc)
    stored = Cultivation(**cultivation.model_dump(), created_at=created_at)
    try:
        connection.execute(
            """
            INSERT INTO cultivations (
                id, zone_id, plant_species, recipe_id, recipe_version,
                status, created_by, created_at, elapsed_simulation_seconds,
                requested_time_scale
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stored.id,
                stored.zone_id,
                stored.plant_species,
                stored.recipe_id,
                stored.recipe_version,
                stored.status.value,
                stored.created_by,
                stored.created_at.isoformat(),
                stored.elapsed_simulation_seconds,
                stored.requested_time_scale,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise CultivationConflict(
            f"cultivation id {cultivation.id!r} already exists, or zone "
            f"{cultivation.zone_id!r} already has a non-concluded cultivation"
        ) from error
    connection.commit()
    return stored


def confirm_cultivation(
    connection: sqlite3.Connection,
    cultivation_id: str,
    confirm: CultivationConfirm,
) -> Cultivation | None:
    """@brief Conferma una bozza e fissa definitivamente la versione della ricetta.

    @param connection Connessione SQLite sulla quale operare.
    @param cultivation_id Identificativo della coltivazione da confermare.
    @param confirm Richiesta di conferma.
    @return Coltivazione aggiornata allo stato `confirmed`, oppure `None` se
        l'identificativo non esiste.
    @throws CultivationStateError Se la coltivazione non e in stato `draft`.
    @throws CultivationCompatibilityError Se settore, specie, ricetta o
        substrato non sono compatibili.
    """
    del confirm  # nessun dato proprio: la conferma opera sulla bozza salvata.

    existing = get_cultivation(connection, cultivation_id)
    if existing is None:
        return None
    if existing.status is not CultivationStatus.DRAFT:
        raise CultivationStateError(
            f"cultivation {cultivation_id!r} is {existing.status.value!r}; "
            "only a draft can be confirmed"
        )

    zone = get_zone(connection, existing.zone_id)
    if zone is None:
        raise CultivationCompatibilityError(
            f"zone {existing.zone_id!r} no longer exists"
        )
    if zone.plant_species != existing.plant_species:
        raise CultivationCompatibilityError(
            f"zone {existing.zone_id!r} hosts {zone.plant_species!r}, not "
            f"{existing.plant_species!r}"
        )

    recipe = get_recipe(connection, existing.recipe_id, existing.recipe_version)
    if recipe is None:
        raise CultivationCompatibilityError(
            f"recipe {existing.recipe_id!r} version {existing.recipe_version} "
            "does not exist"
        )
    if recipe.plant_type != existing.plant_species:
        raise CultivationCompatibilityError(
            f"recipe {existing.recipe_id!r} targets {recipe.plant_type!r}, "
            f"not {existing.plant_species!r}"
        )

    previous_substrate = _last_run_substrate(
        connection, existing.zone_id, existing.id
    )
    if previous_substrate is not None and previous_substrate != recipe.substrate.value:
        raise CultivationCompatibilityError(
            f"zone {existing.zone_id!r} was last grown with substrate "
            f"{previous_substrate!r}, not {recipe.substrate.value!r}"
        )

    confirmed_at = datetime.now(timezone.utc)
    connection.execute(
        "UPDATE cultivations SET status = ?, confirmed_at = ? WHERE id = ?",
        (CultivationStatus.CONFIRMED.value, confirmed_at.isoformat(), cultivation_id),
    )
    connection.commit()
    return get_cultivation(connection, cultivation_id)


def record_activation_result(
    connection: sqlite3.Connection,
    cultivation_id: str,
    result: CultivationActivationResult,
) -> Cultivation | None:
    """@brief Registra l'esito dell'attivazione riportato dall'Edge.

    @details Un esito positivo porta la coltivazione ad `active` e fissa
    `started_at` e `applied_time_scale`. Un fallimento conserva il motivo in
    `error_message`, porta la coltivazione a `failed` e libera il settore per
    una nuova bozza; gli attuatori restano spenti perche questo modulo non
    invia mai comandi di attivazione.

    @return Coltivazione aggiornata, oppure `None` se l'identificativo non
        esiste.
    @throws CultivationStateError Se la coltivazione non e in stato
        `confirmed`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None:
        return None
    if existing.status is not CultivationStatus.CONFIRMED:
        raise CultivationStateError(
            f"cultivation {cultivation_id!r} is {existing.status.value!r}; "
            "only a confirmed cultivation can receive an activation result"
        )

    now = datetime.now(timezone.utc)
    if result.success:
        applied_time_scale = (
            result.applied_time_scale
            if result.applied_time_scale is not None
            else existing.requested_time_scale
        )
        connection.execute(
            """
            UPDATE cultivations
            SET status = ?, started_at = ?, applied_time_scale = ?,
                error_message = NULL
            WHERE id = ?
            """,
            (
                CultivationStatus.ACTIVE.value,
                now.isoformat(),
                applied_time_scale,
                cultivation_id,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE cultivations
            SET status = ?, completed_at = ?, error_message = ?
            WHERE id = ?
            """,
            (
                CultivationStatus.FAILED.value,
                now.isoformat(),
                result.error_message,
                cultivation_id,
            ),
        )
    connection.commit()
    return get_cultivation(connection, cultivation_id)


def _apply_progress(
    connection: sqlite3.Connection, cultivation_id: str, progress: CultivationProgress
) -> None:
    """@brief Aggiorna i secondi di simulazione se il chiamante li riporta."""
    if progress.elapsed_simulation_seconds is not None:
        connection.execute(
            "UPDATE cultivations SET elapsed_simulation_seconds = ? WHERE id = ?",
            (progress.elapsed_simulation_seconds, cultivation_id),
        )


def pause_cultivation(
    connection: sqlite3.Connection,
    cultivation_id: str,
    progress: CultivationProgress,
) -> Cultivation | None:
    """@brief Sospende una coltivazione attiva senza concluderla.

    @throws CultivationStateError Se la coltivazione non e in stato `active`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None:
        return None
    if existing.status is not CultivationStatus.ACTIVE:
        raise CultivationStateError(
            f"cultivation {cultivation_id!r} is {existing.status.value!r}; "
            "only an active cultivation can be paused"
        )
    _apply_progress(connection, cultivation_id, progress)
    connection.execute(
        "UPDATE cultivations SET status = ? WHERE id = ?",
        (CultivationStatus.PAUSED.value, cultivation_id),
    )
    connection.commit()
    return get_cultivation(connection, cultivation_id)


def resume_cultivation(
    connection: sqlite3.Connection, cultivation_id: str
) -> Cultivation | None:
    """@brief Riprende una coltivazione sospesa.

    @throws CultivationStateError Se la coltivazione non e in stato `paused`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None:
        return None
    if existing.status is not CultivationStatus.PAUSED:
        raise CultivationStateError(
            f"cultivation {cultivation_id!r} is {existing.status.value!r}; "
            "only a paused cultivation can be resumed"
        )
    connection.execute(
        "UPDATE cultivations SET status = ? WHERE id = ?",
        (CultivationStatus.ACTIVE.value, cultivation_id),
    )
    connection.commit()
    return get_cultivation(connection, cultivation_id)


def complete_cultivation(
    connection: sqlite3.Connection,
    cultivation_id: str,
    progress: CultivationProgress,
) -> Cultivation | None:
    """@brief Conclude regolarmente una coltivazione attiva o sospesa.

    @details Conclude la coltivazione e libera il settore, che torna
    disponibile per una nuova bozza.

    @throws CultivationStateError Se la coltivazione non e `active` o `paused`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None:
        return None
    if existing.status not in (CultivationStatus.ACTIVE, CultivationStatus.PAUSED):
        raise CultivationStateError(
            f"cultivation {cultivation_id!r} is {existing.status.value!r}; "
            "only an active or paused cultivation can be completed"
        )
    _apply_progress(connection, cultivation_id, progress)
    completed_at = datetime.now(timezone.utc)
    connection.execute(
        "UPDATE cultivations SET status = ?, completed_at = ? WHERE id = ?",
        (CultivationStatus.COMPLETED.value, completed_at.isoformat(), cultivation_id),
    )
    connection.commit()
    return get_cultivation(connection, cultivation_id)
