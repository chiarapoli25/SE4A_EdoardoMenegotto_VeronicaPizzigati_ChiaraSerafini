"""@file repository.py
@brief Persistenza SQLite delle coltivazioni.

@details Le query di conferma, attivazione, pausa e conclusione sono
transazionali: ogni funzione legge lo stato corrente, verifica la
transizione richiesta e scrive il nuovo stato nella stessa chiamata, cosi da
non lasciare mai una coltivazione in uno stato intermedio incoerente.
L'indice univoco parziale creato in `core.database` garantisce comunque,
anche in presenza di richieste concorrenti, che un settore non abbia mai
piu di una coltivazione non conclusa.

L'attivazione vera e propria non e simulata da questo modulo: la conferma
accoda un comando `ActivateCultivation` sulla stessa coda comandi (Edge)
gia usata dalle altre feature (`features.commands`), con la ricetta pinnata
incorporata nel payload cosi che l'Edge non debba mai risolvere una versione
diversa da quella confermata. L'esito arriva quando l'Edge riporta il
risultato del comando tramite l'endpoint gia esistente
`POST /zones/{zone_id}/commands/{command_id}/result`; la riconciliazione
avviene in `features.commands.routes`, che richiama
`apply_activation_command_result` qui sotto.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from ..commands.models import CommandStatus, CommandType, RuntimeCommandCreate
from ..commands.repository import RuntimeCommandConflict, create_command
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

## @brief Chiavi del risultato strutturato usate dalla riconciliazione.
_RESULT_APPLIED_TIME_SCALE = "applied_time_scale"
_RESULT_CURRENT_PHASE = "current_phase"


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
    "current_phase, error_message, activation_command_id, confirmed_by"
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
        current_phase=row[14],
        error_message=row[15],
        activation_command_id=row[16],
        confirmed_by=row[17],
    )


def get_cultivation(
    connection: sqlite3.Connection, cultivation_id: str
) -> Cultivation | None:
    """@brief Recupera una coltivazione tramite identificativo."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM cultivations WHERE id = ?", (cultivation_id,)
    ).fetchone()
    return None if row is None else _cultivation_from_row(row)


def _get_cultivation_by_activation_command(
    connection: sqlite3.Connection, command_id: str
) -> Cultivation | None:
    """@brief Recupera la coltivazione legata a un comando di attivazione."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM cultivations WHERE activation_command_id = ?",
        (command_id,),
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


def _require_status(
    cultivation: Cultivation, expected: CultivationStatus, *, verb: str
) -> None:
    """@brief Impone che la coltivazione sia nello stato atteso.

    @details Fattorizza il controllo ripetuto in ogni transizione a stato
    singolo (conferma, esito attivazione, pausa, ripresa). `complete_cultivation`
    ammette due stati di partenza e non usa questo helper.

    @param verb Participio usato nel messaggio d'errore (es. "confirmed").
    @throws CultivationStateError Se lo stato attuale non e quello atteso.
    """
    if cultivation.status is not expected:
        raise CultivationStateError(
            f"cultivation {cultivation.id!r} is {cultivation.status.value!r}; "
            f"only a {expected.value} cultivation can be {verb}"
        )


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
    connection: sqlite3.Connection, zone_id: str, cultivation: CultivationCreate
) -> Cultivation:
    """@brief Salva una nuova bozza di coltivazione.

    @param connection Connessione SQLite sulla quale salvare.
    @param zone_id Settore che ospitera la coltivazione (dal path HTTP).
    @param cultivation Specie e ricetta desiderati per il ciclo.
    @return Coltivazione persistita con stato iniziale `draft`.
    @throws CultivationConflict Se l'id e gia usato o il settore ha gia una
        coltivazione non conclusa.
    """
    created_at = datetime.now(timezone.utc)
    stored = Cultivation(
        **cultivation.model_dump(), zone_id=zone_id, created_at=created_at
    )
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
            f"{zone_id!r} already has a non-concluded cultivation"
        ) from error
    connection.commit()
    return stored


def confirm_cultivation(
    connection: sqlite3.Connection,
    zone_id: str,
    cultivation_id: str,
    confirm: CultivationConfirm,
    confirmed_by: str | None = None,
) -> Cultivation | None:
    """@brief Conferma una bozza, fissa la ricetta e accoda l'attivazione Edge.

    @details Dopo i controlli di compatibilita, incorpora la ricetta pinnata
    per intero nel payload del comando `ActivateCultivation` (oltre a
    `recipe_id`, letto dalla sincronizzazione gia esistente in
    `features.commands`, e a `recipe_version`, usato dall'Edge se dovesse
    comunque risolvere la ricetta da `recipe_id`): l'Edge riceve cosi
    esattamente la versione confermata qui, anche se nel frattempo viene
    pubblicata una versione successiva.

    @param connection Connessione SQLite sulla quale operare.
    @param zone_id Settore atteso dal path HTTP `/zones/{zone_id}/...`.
    @param cultivation_id Identificativo della coltivazione da confermare.
    @param confirm Richiesta di conferma.
    @param confirmed_by Utente autenticato che ha eseguito la conferma (vedi
        `features.auth`); `None` se la richiesta non e autenticata.
    @return Coltivazione aggiornata allo stato `starting`, oppure `None` se
        l'identificativo non esiste o non appartiene a `zone_id`.
    @throws CultivationStateError Se la coltivazione non e in stato `draft`.
    @throws CultivationCompatibilityError Se settore, specie, ricetta o
        substrato non sono compatibili.
    """
    del confirm  # nessun dato proprio: la conferma opera sulla bozza salvata.

    existing = get_cultivation(connection, cultivation_id)
    if existing is None or existing.zone_id != zone_id:
        return None
    _require_status(existing, CultivationStatus.DRAFT, verb="confirmed")

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

    activation_command_id = f"{cultivation_id}-activate"
    create_command(
        connection,
        existing.zone_id,
        RuntimeCommandCreate(
            command_id=activation_command_id,
            command_type=CommandType.ACTIVATE_CULTIVATION,
            payload={
                "cultivation_id": cultivation_id,
                "recipe_id": recipe.id,
                "recipe_version": recipe.version,
                "initial_time_scale": existing.requested_time_scale,
                # Ricetta incorporata per intero: evita che l'Edge debba
                # risolvere di nuovo `recipe_id`/`recipe_version` (vedi
                # l'auto-fetch versionato in `http_backend_client.cpp` come
                # solo fallback per i comandi che non la incorporano).
                "recipe": recipe.model_dump(mode="json"),
            },
        ),
    )

    confirmed_at = datetime.now(timezone.utc)
    connection.execute(
        """
        UPDATE cultivations
        SET status = ?, confirmed_at = ?, activation_command_id = ?,
            confirmed_by = ?
        WHERE id = ?
        """,
        (
            CultivationStatus.STARTING.value,
            confirmed_at.isoformat(),
            activation_command_id,
            confirmed_by,
            cultivation_id,
        ),
    )
    connection.commit()
    # Evita una SELECT di ri-lettura: i soli campi cambiati dall'UPDATE sono
    # gia noti, quindi bastano per ricostruire lo stato aggiornato.
    return existing.model_copy(
        update={
            "status": CultivationStatus.STARTING,
            "confirmed_at": confirmed_at,
            "activation_command_id": activation_command_id,
            "confirmed_by": confirmed_by,
        }
    )


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
    invia mai comandi di attivazione direttamente, solo tramite la coda.

    @return Coltivazione aggiornata, oppure `None` se l'identificativo non
        esiste.
    @throws CultivationStateError Se la coltivazione non e in stato
        `starting`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None:
        return None
    _require_status(
        existing,
        CultivationStatus.STARTING,
        verb="updated with an activation result",
    )

    now = datetime.now(timezone.utc)
    if result.success:
        applied_time_scale = (
            result.applied_time_scale
            if result.applied_time_scale is not None
            else existing.requested_time_scale
        )
        current_phase = (
            result.current_phase
            if result.current_phase is not None
            else existing.current_phase
        )
        connection.execute(
            """
            UPDATE cultivations
            SET status = ?, started_at = ?, applied_time_scale = ?,
                current_phase = ?, error_message = NULL
            WHERE id = ?
            """,
            (
                CultivationStatus.ACTIVE.value,
                now.isoformat(),
                applied_time_scale,
                current_phase,
                cultivation_id,
            ),
        )
        connection.commit()
        return existing.model_copy(
            update={
                "status": CultivationStatus.ACTIVE,
                "started_at": now,
                "applied_time_scale": applied_time_scale,
                "current_phase": current_phase,
                "error_message": None,
            }
        )

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
    return existing.model_copy(
        update={
            "status": CultivationStatus.FAILED,
            "completed_at": now,
            "error_message": result.error_message,
        }
    )


def apply_activation_command_result(
    connection: sqlite3.Connection,
    command_id: str,
    status: CommandStatus,
    message: str | None,
    result: dict | None = None,
) -> Cultivation | None:
    """@brief Riconcilia l'esito di un comando `ActivateCultivation` completato.

    @details Chiamata da `features.commands.routes` subito dopo che un
    comando e stato completato, cosi che l'unico canale con cui l'Edge
    riporta un esito (`POST .../commands/{id}/result`) sia anche l'unico che
    fa avanzare lo stato della coltivazione. E' tollerante verso i replay
    idempotenti del comando: se la coltivazione non e (piu) in stato
    `starting` non solleva errori, restituisce semplicemente lo stato
    corrente.

    @param command_id Identificativo del comando appena completato.
    @param status Stato finale del comando (`succeeded` o `rejected`).
    @param message Messaggio riportato dall'Edge, usato come motivo in caso
        di fallimento (i "risultati strutturati" non hanno un campo testuale
        proprio per l'errore).
    @param result Dettagli strutturati opzionali riportati dall'Edge, ad
        esempio `{"applied_time_scale": 1.0, "current_phase": "Germinazione"}`.
        Valori non riconosciuti o del tipo sbagliato sono ignorati.
    @return La coltivazione coinvolta, oppure `None` se `command_id` non
        corrisponde a nessuna attivazione di coltivazione.
    """
    existing = _get_cultivation_by_activation_command(connection, command_id)
    if existing is None or existing.status is not CultivationStatus.STARTING:
        return existing
    result = result or {}
    applied_time_scale = result.get(_RESULT_APPLIED_TIME_SCALE)
    if not isinstance(applied_time_scale, (int, float)) or applied_time_scale <= 0:
        applied_time_scale = None
    current_phase = result.get(_RESULT_CURRENT_PHASE)
    if not isinstance(current_phase, str) or not current_phase:
        current_phase = None
    activation_result = CultivationActivationResult(
        success=status is CommandStatus.SUCCEEDED,
        applied_time_scale=applied_time_scale,
        current_phase=current_phase if status is CommandStatus.SUCCEEDED else None,
        error_message=(
            None
            if status is CommandStatus.SUCCEEDED
            else (message or "activation rejected by the Edge")
        ),
    )
    return record_activation_result(connection, existing.id, activation_result)


def _enqueue_lifecycle_command(
    connection: sqlite3.Connection,
    zone_id: str,
    cultivation_id: str,
    command_type: CommandType,
    payload: dict | None = None,
) -> None:
    """@brief Notifica un cambio di lifecycle alla stessa coda Edge.

    @details A differenza dell'attivazione, pausa/ripresa/conclusione non
    attendono un esito: lo stato della coltivazione cambia subito in questa
    tabella storica, mentre il comando accodato tiene allineata la proiezione
    corrente sulla zona gia gestita da `features.commands`/`features.zones`.
    Il fallimento dell'accodamento (identificativo gia usato con dati
    diversi, caso limite) non deve impedire la transizione locale, quindi
    viene ignorato.

    @param payload Corpo del comando; contiene sempre almeno `cultivation_id`
        (vedi contratti `PauseCultivation`/`ResumeCultivation`/
        `StopCultivation`/`SetSimulationSpeed`).
    """
    command_id = f"{cultivation_id}-{command_type.value.lower()}-{uuid.uuid4().hex[:8]}"
    try:
        create_command(
            connection,
            zone_id,
            RuntimeCommandCreate(
                command_id=command_id,
                command_type=command_type,
                payload=payload or {"cultivation_id": cultivation_id},
            ),
        )
    except (RuntimeCommandConflict, sqlite3.Error):
        pass


def _resolved_elapsed_seconds(
    existing: Cultivation, progress: CultivationProgress
) -> float:
    """@brief Secondi di simulazione da persistere, riportati o invariati."""
    return (
        progress.elapsed_simulation_seconds
        if progress.elapsed_simulation_seconds is not None
        else existing.elapsed_simulation_seconds
    )


## @brief Tipi di evento Edge interpretati da `apply_cultivation_event`.
CULTIVATION_EVENT_TYPES = frozenset(
    {
        "CultivationActivationStarted",
        "CultivationActivated",
        "CultivationActivationFailed",
        "CultivationPaused",
        "CultivationResumed",
        "CultivationStopped",
    }
)


def apply_cultivation_event(
    connection: sqlite3.Connection,
    zone_id: str,
    cultivation_id: str,
    event_type: str,
    payload: dict,
) -> Cultivation | None:
    """@brief Riconcilia in modo tollerante un evento di lifecycle riportato dall'Edge.

    @details Usata da `features.events.repository` per i tipi elencati in
    `CULTIVATION_EVENT_TYPES`. A differenza dei comandi (dove il backend e
    sempre l'iniziatore e attende un esito), questi sono notifiche
    Edge -> backend: non accodano mai un nuovo comando, perche l'Edge ha gia
    agito autonomamente. Sono no-op silenziosi (nessuna eccezione, nessuna
    scrittura) quando l'identificativo non esiste nella zona indicata o
    quando lo stato attuale non ammette piu la transizione corrispondente
    (replay, evento duplicato, oppure arrivato dopo che il comando
    equivalente e gia stato riconciliato).

    @return La coltivazione coinvolta (aggiornata o invariata), oppure
        `None` se l'identificativo non esiste nella zona.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None or existing.zone_id != zone_id:
        return None

    if (
        event_type == "CultivationActivated"
        and existing.status is CultivationStatus.STARTING
    ):
        applied_time_scale = payload.get(_RESULT_APPLIED_TIME_SCALE)
        if not isinstance(applied_time_scale, (int, float)) or applied_time_scale <= 0:
            applied_time_scale = None
        current_phase = payload.get(_RESULT_CURRENT_PHASE)
        if not isinstance(current_phase, str) or not current_phase:
            current_phase = None
        return record_activation_result(
            connection,
            cultivation_id,
            CultivationActivationResult(
                success=True,
                applied_time_scale=applied_time_scale,
                current_phase=current_phase,
            ),
        )

    if (
        event_type == "CultivationActivationFailed"
        and existing.status is CultivationStatus.STARTING
    ):
        error_message = payload.get("error_message") or payload.get("reason")
        if not isinstance(error_message, str) or not error_message:
            error_message = "activation reported as failed by the Edge"
        return record_activation_result(
            connection,
            cultivation_id,
            CultivationActivationResult(success=False, error_message=error_message),
        )

    if event_type == "CultivationPaused" and existing.status is CultivationStatus.ACTIVE:
        connection.execute(
            "UPDATE cultivations SET status = ? WHERE id = ?",
            (CultivationStatus.PAUSED.value, cultivation_id),
        )
        connection.commit()
        return existing.model_copy(update={"status": CultivationStatus.PAUSED})

    if event_type == "CultivationResumed" and existing.status is CultivationStatus.PAUSED:
        connection.execute(
            "UPDATE cultivations SET status = ? WHERE id = ?",
            (CultivationStatus.ACTIVE.value, cultivation_id),
        )
        connection.commit()
        return existing.model_copy(update={"status": CultivationStatus.ACTIVE})

    if event_type == "CultivationStopped" and existing.status in (
        CultivationStatus.ACTIVE,
        CultivationStatus.PAUSED,
    ):
        completed_at = datetime.now(timezone.utc)
        connection.execute(
            """
            UPDATE cultivations SET status = ?, completed_at = ?
            WHERE id = ?
            """,
            (CultivationStatus.COMPLETED.value, completed_at.isoformat(), cultivation_id),
        )
        connection.commit()
        return existing.model_copy(
            update={
                "status": CultivationStatus.COMPLETED,
                "completed_at": completed_at,
            }
        )

    # `CultivationActivationStarted` e qualunque evento non compatibile con
    # lo stato corrente restano puramente informativi: sono comunque
    # conservati nello storico `edge_events` da `features.events`.
    return existing


def pause_cultivation(
    connection: sqlite3.Connection,
    zone_id: str,
    cultivation_id: str,
    progress: CultivationProgress,
) -> Cultivation | None:
    """@brief Sospende una coltivazione attiva senza concluderla.

    @throws CultivationStateError Se la coltivazione non e in stato `active`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None or existing.zone_id != zone_id:
        return None
    _require_status(existing, CultivationStatus.ACTIVE, verb="paused")
    elapsed = _resolved_elapsed_seconds(existing, progress)
    connection.execute(
        """
        UPDATE cultivations SET status = ?, elapsed_simulation_seconds = ?
        WHERE id = ?
        """,
        (CultivationStatus.PAUSED.value, elapsed, cultivation_id),
    )
    connection.commit()
    _enqueue_lifecycle_command(
        connection,
        existing.zone_id,
        cultivation_id,
        CommandType.PAUSE_CULTIVATION,
        {"cultivation_id": cultivation_id},
    )
    return existing.model_copy(
        update={
            "status": CultivationStatus.PAUSED,
            "elapsed_simulation_seconds": elapsed,
        }
    )


def resume_cultivation(
    connection: sqlite3.Connection,
    zone_id: str,
    cultivation_id: str,
    time_scale: float | None = None,
) -> Cultivation | None:
    """@brief Riprende una coltivazione sospesa.

    @param time_scale Nuova velocita da riapplicare alla ripresa, oppure
        `None` per mantenere l'ultima velocita applicata (comando Edge
        `ResumeCultivation(cultivation_id, time_scale?)`).
    @throws CultivationStateError Se la coltivazione non e in stato `paused`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None or existing.zone_id != zone_id:
        return None
    _require_status(existing, CultivationStatus.PAUSED, verb="resumed")
    connection.execute(
        "UPDATE cultivations SET status = ? WHERE id = ?",
        (CultivationStatus.ACTIVE.value, cultivation_id),
    )
    connection.commit()
    payload: dict = {"cultivation_id": cultivation_id}
    if time_scale is not None:
        payload["time_scale"] = time_scale
    _enqueue_lifecycle_command(
        connection,
        existing.zone_id,
        cultivation_id,
        CommandType.RESUME_CULTIVATION,
        payload,
    )
    return existing.model_copy(update={"status": CultivationStatus.ACTIVE})


def complete_cultivation(
    connection: sqlite3.Connection,
    zone_id: str,
    cultivation_id: str,
    progress: CultivationProgress,
    reason: str | None = None,
) -> Cultivation | None:
    """@brief Conclude regolarmente una coltivazione attiva o sospesa.

    @details Conclude la coltivazione e libera il settore, che torna
    disponibile per una nuova bozza. Ammette due stati di partenza (`active`
    o `paused`), quindi non usa `_require_status`.

    @param reason Motivo dell'arresto, riportato all'Edge nel comando
        `StopCultivation(cultivation_id, reason)` e conservato per l'audit.
    @throws CultivationStateError Se la coltivazione non e `active` o `paused`.
    """
    existing = get_cultivation(connection, cultivation_id)
    if existing is None or existing.zone_id != zone_id:
        return None
    if existing.status not in (CultivationStatus.ACTIVE, CultivationStatus.PAUSED):
        raise CultivationStateError(
            f"cultivation {cultivation_id!r} is {existing.status.value!r}; "
            "only an active or paused cultivation can be completed"
        )
    elapsed = _resolved_elapsed_seconds(existing, progress)
    completed_at = datetime.now(timezone.utc)
    connection.execute(
        """
        UPDATE cultivations
        SET status = ?, completed_at = ?, elapsed_simulation_seconds = ?
        WHERE id = ?
        """,
        (
            CultivationStatus.COMPLETED.value,
            completed_at.isoformat(),
            elapsed,
            cultivation_id,
        ),
    )
    connection.commit()
    _enqueue_lifecycle_command(
        connection,
        existing.zone_id,
        cultivation_id,
        CommandType.STOP_CULTIVATION,
        {"cultivation_id": cultivation_id, "reason": reason},
    )
    return existing.model_copy(
        update={
            "status": CultivationStatus.COMPLETED,
            "completed_at": completed_at,
            "elapsed_simulation_seconds": elapsed,
        }
    )
