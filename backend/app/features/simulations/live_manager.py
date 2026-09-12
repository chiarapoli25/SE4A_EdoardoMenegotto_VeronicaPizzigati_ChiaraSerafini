"""Motore di riproduzione live dell'intera serra.

Stessa fisica del batch (manager.py, "Simula l'intera serra"): la stessa
identica chiamata all'Edge, deterministica e priva di qualunque dipendenza
dal tempo reale. La differenza e' COME viene calcolata: il batch calcola un
arco FISSO in un colpo solo e lo mostra tutto insieme a fine calcolo,
questo motore invece calcola SOLO IL PRIMO PEZZO — dura quanto la prima
fase di coltivazione, vedi initial_steps_per_target() — e continua a
calcolarne altro DAVVERO MENTRE VIENE MOSTRATO, un pezzo alla volta, sempre
restando appena un passo avanti a quanto rivelato al ritmo scelto
dall'utente (di default 600 secondi simulati per ogni secondo reale — "un
secondo reale sono 10 minuti") — vedi _maybe_extend. Non e' quindi mai "gia'
tutto calcolato in anticipo, solo rivelato con calma": il calcolo avviene
per davvero via via che il tempo simulato avanza. Non ha un modo naturale
di finire: si ferma solo quando il dashboard lascia la pagina Simulatore
(discardActiveLiveSimulationIfAny lato frontend) o quando l'utente preme
Reset.

Perche' NON un vero step-by-step (una chiamata Edge per ogni singolo nuovo
valore): l'eseguibile Edge e' uno strumento batch senza un modo di
"riprendere da dove era rimasto" — ogni chiamata riparte dal tempo zero.
Chiedergli un valore alla volta vorrebbe dire ricalcolare da capo TUTTA la
storia gia' calcolata ad ogni singolo nuovo valore, un costo che cresce
linearmente con quanto la riproduzione e' gia' andata avanti: dopo qualche
giorno di riproduzione, ogni nuovo valore costerebbe quanto ricalcolare
l'intera storia fin li'. La soluzione qui e' calcolare in PEZZI che
raddoppiano (mai un incremento fisso): appena il tempo simulato raggiunge
META' del pezzo gia' calcolato, un ricalcolo con pezzo RADDOPPIATO parte in
background (stesso seed meteo, stessa ricetta) — dato che l'Edge e'
deterministico, il prefisso ricalcolato e' sempre identico byte-per-byte a
quello gia' mostrato (verificato: stessi seed+ricetta, chiedere piu' step
non cambia i primi), quindi lo scambio e' invisibile, mai un salto o un
azzeramento nel grafico. Raddoppiare tiene il numero di ricalcoli
logaritmico nel tempo trascorso, cosi' il costo totale resta lineare
nell'orizzonte finale anche su una sessione lunghissima — lo stesso
principio di un array dinamico che raddoppia la propria capacita' invece di
crescere di uno alla volta.

La posizione nel tempo simulato, se e' in play o in pausa e a che velocita'
e' stato AUTORITATIVO SUL BACKEND, non nella scheda del browser che la
osserva: chiudere il pop-up o ricaricare la pagina non la resetta, e due
schede che guardano lo stesso run vedono la stessa posizione — esattamente
come lo stato di un job batch e' oggi condiviso da chiunque lo interroghi.
Un solo run live alla volta, un worker dedicato separato da quello del
batch (SimulationManager._executor): i due sistemi non condividono nulla,
cosi' un batch "Simula l'intera serra" e una riproduzione live possono
coesistere senza contendersi lo stesso slot.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..recipes.models import Recipe
from .manager import (
    SimulationBusy,
    SimulationInvalid,
    SimulationMissing,
    SimulationNotReady,
    _actuator_intervals,
    _active_actuators,
    _phase_targets,
    _reduce_series,
    _summary,
    _Target,
    execute_edge_steps,
)
from .models import (
    STEP_SECONDS,
    LiveSimulationCreate,
    LiveSimulationJob,
    LiveSimulationStatus,
    LiveZoneSnapshot,
    SimulationPreview,
)


EXPIRY_MINUTES = 30
_ACTIVE_STATUSES = {
    LiveSimulationStatus.COMPUTING,
    LiveSimulationStatus.PLAYING,
    LiveSimulationStatus.PAUSED,
}


def initial_steps_per_target(targets: list[_Target]) -> int:
    """Quanti step forma il primo pezzo calcolato alla creazione — dura
    quanto la PRIMA fase (fasi[0]) piu' lunga fra i settori coinvolti in
    questo run, cosi' ogni settore la vede per intero fin da subito, mai
    tagliata a meta' (una ricetta con fase iniziale piu' corta arriva
    quindi gia' oltre la propria, dentro la seconda — normale, non e'
    lei il riferimento). Le fasi successive, sempre piu' lunghe (vedi il
    catalogo: da ~14-21 giorni la prima fase fino a 60-120 giorni le
    ultime), restano fuori dal primo blocco: le raggiunge l'estensione
    automatica (_maybe_extend), che raddoppia da li' in poi esattamente
    come faceva prima — qui cambia solo IL PUNTO DI PARTENZA, non come
    cresce dopo. Funzione a livello di modulo (non un valore fisso) cosi'
    i test possono monkeypatcharla, come gia' fatto con
    BATCH_TIMEOUT_SECONDS in manager.py."""
    first_phase_seconds = max(
        target.recipe.phases[0].duration_hours * 3600.0 for target in targets
    )
    return max(1, math.ceil(first_phase_seconds / STEP_SECONDS))

# Frazione dell'orizzonte corrente raggiunta la quale parte in background il
# ricalcolo raddoppiato — ampio apposta: anche a velocita' massima il
# ricalcolo Edge (qualche secondo, anche su orizzonti grandi) deve sempre
# finire ben PRIMA che la riproduzione raggiunga davvero l'ultimo step
# calcolato, altrimenti la riproduzione (mai la posizione: quella continua
# a scorrere) resterebbe visivamente ferma sull'ultimo dato disponibile
# finche' il ricalcolo non finisce.
EXTEND_THRESHOLD_FRACTION = 0.5


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _LiveRecord:
    job_id: str
    targets: list[_Target]
    steps_per_target: int
    horizon_seconds: int
    created_at: datetime
    speed_multiplier: float
    # Seed meteo condiviso da ogni settore di questa run — stessa idea di
    # _Record.environment_seed in manager.py, vedi li' per il perche'. Fisso
    # per tutta la vita del run (anche attraverso le estensioni): e' proprio
    # perche' resta lo stesso che un ricalcolo con piu' step riproduce un
    # prefisso identico, vedi il modulo docstring.
    environment_seed: int = field(default_factory=lambda: secrets.randbits(32))
    cancel: threading.Event = field(default_factory=threading.Event)
    process: Any = None
    status: LiveSimulationStatus = LiveSimulationStatus.COMPUTING
    computing_completed_steps: int = 0
    # Uno per target, stesso ordine di `targets` — None finche' il primo
    # calcolo Edge non e' completato per intero (nessuna riproduzione
    # parziale al primo giro: la posizione nel tempo puo' gia' avanzare
    # mentalmente, ma non c'e' nulla da rivelare finche' l'ultimo target non
    # ha finito). Rimpiazzato per intero (mai modificato in place) quando
    # un'estensione in background finisce, vedi _extend.
    computed_steps: list[list[dict[str, Any]]] | None = None
    # True mentre un ricalcolo con orizzonte raddoppiato gira in background
    # (vedi _extend) — evita di sottometterne un secondo prima che il primo
    # sia finito.
    extending: bool = False
    error: str | None = None
    # Secondi simulati accumulati fino all'ultima pausa/cambio velocita' —
    # cresce senza limite mentre resta in play, mai avvolto su un ciclo (la
    # riproduzione non si ripete piu': l'orizzonte calcolato si estende
    # invece di tornare a zero, vedi il modulo docstring). Vedi
    # _elapsed_seconds.
    frozen_elapsed_seconds: float = 0.0
    resumed_at_monotonic: float | None = None
    started_playing_at: datetime | None = None
    stopped_at: datetime | None = None
    expires_at: datetime | None = None


class LiveSimulationManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _LiveRecord] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-simulation")

    def _cleanup(self) -> None:
        expired = [
            run_id
            for run_id, record in self._records.items()
            if record.expires_at is not None and record.expires_at <= _now()
        ]
        for run_id in expired:
            self._records.pop(run_id, None)

    def create_greenhouse(
        self,
        request: LiveSimulationCreate,
        targets: list[tuple[str, Recipe]],
    ) -> LiveSimulationJob:
        if not targets:
            raise SimulationInvalid(
                "no zone has an assigned recipe: nothing to simulate"
            )
        with self._lock:
            self._cleanup()
            if any(record.status in _ACTIVE_STATUSES for record in self._records.values()):
                raise SimulationBusy("another live simulation is already running")
            run_id = f"live-simulation-{uuid4().hex}"
            live_targets = [_Target(recipe=recipe, zone_id=zone_id) for zone_id, recipe in targets]
            steps_per_target = initial_steps_per_target(live_targets)
            record = _LiveRecord(
                job_id=run_id,
                targets=live_targets,
                steps_per_target=steps_per_target,
                horizon_seconds=steps_per_target * STEP_SECONDS,
                created_at=_now(),
                speed_multiplier=request.speed_multiplier,
            )
            self._records[run_id] = record
            self._executor.submit(self._compute, run_id)
            return self._job(record)

    def _get_record(self, run_id: str) -> _LiveRecord:
        self._cleanup()
        record = self._records.get(run_id)
        if record is None:
            raise SimulationMissing("live simulation not found or expired")
        return record

    def get(self, run_id: str) -> LiveSimulationJob:
        with self._lock:
            record = self._get_record(run_id)
            self._maybe_extend(record)
            return self._job(record)

    def control(
        self,
        run_id: str,
        action: str,
        speed_multiplier: float | None,
    ) -> LiveSimulationJob:
        with self._lock:
            record = self._get_record(run_id)
            if record.status is LiveSimulationStatus.COMPUTING:
                raise SimulationNotReady("live simulation is still computing")
            if record.status not in {LiveSimulationStatus.PLAYING, LiveSimulationStatus.PAUSED}:
                return self._job(record)  # gia' fallita/annullata nel frattempo
            # Congela la posizione attuale PRIMA di cambiare stato/velocita':
            # e' cosi' che pausa e cambio velocita' non creano un salto nel
            # tempo simulato (vedi _elapsed_seconds).
            record.frozen_elapsed_seconds = self._elapsed_seconds(record)
            if speed_multiplier is not None:
                record.speed_multiplier = speed_multiplier
            if action == "play":
                record.status = LiveSimulationStatus.PLAYING
                record.resumed_at_monotonic = time.monotonic()
                if record.started_playing_at is None:
                    record.started_playing_at = _now()
            elif action == "pause":
                record.status = LiveSimulationStatus.PAUSED
                record.resumed_at_monotonic = None
            self._maybe_extend(record)
            return self._job(record)

    def result(self, run_id: str) -> list[SimulationPreview]:
        """Anteprima per settore, tagliata a quanto e' gia' stato rivelato
        — stessa forma della SimulationPreview del batch, cosi' il
        dashboard riusa lo stesso codice di disegno del grafico. Cresce ad
        ogni chiamata finche' la riproduzione avanza, e non si azzera mai:
        l'orizzonte calcolato si estende invece di ripartire da capo (vedi
        il modulo docstring)."""
        with self._lock:
            record = self._get_record(run_id)
            if record.computed_steps is None:
                raise SimulationNotReady("live simulation is still computing")
            self._maybe_extend(record)
            revealed_count = self._revealed_step_count(record)
            previews: list[SimulationPreview] = []
            for target, all_steps in zip(record.targets, record.computed_steps, strict=True):
                steps = all_steps[:revealed_count]
                intervals = _actuator_intervals(steps)
                previews.append(
                    SimulationPreview(
                        job_id=run_id,
                        zone_id=target.zone_id,
                        recipe={
                            "id": target.recipe.id,
                            "plant_type": target.recipe.plant_type,
                            "version": target.recipe.version,
                            "strategies": {
                                controller.variable.value:
                                    controller.selected_strategy.value
                                for controller in target.recipe.controllers
                            },
                        },
                        duration_seconds=record.horizon_seconds,
                        series=_reduce_series(steps),
                        actuator_intervals=intervals,
                        summary=_summary(steps, intervals),
                        phases=_phase_targets(target.recipe),
                    )
                )
            return previews

    def cancel_or_discard(self, run_id: str) -> None:
        with self._lock:
            record = self._get_record(run_id)
            if record.status in _ACTIVE_STATUSES:
                record.cancel.set()
                if record.process is not None:
                    record.process.terminate()
                if record.status is not LiveSimulationStatus.COMPUTING:
                    # Il calcolo e' gia' finito: non c'e' alcun worker in
                    # esecuzione che possa reagire al cancel event, quindi
                    # va chiuso qui, subito, invece che restare "vivo" fino
                    # alla scadenza. Un'eventuale estensione in background
                    # (record.extending) vede comunque cancel.is_set() e si
                    # ferma da sola, vedi _extend.
                    record.status = LiveSimulationStatus.CANCELLED
                    record.stopped_at = _now()
                    record.expires_at = record.stopped_at + timedelta(minutes=EXPIRY_MINUTES)
            else:
                self._records.pop(run_id, None)

    # -- interno --------------------------------------------------------

    def _elapsed_seconds(self, record: _LiveRecord) -> float:
        """Secondi simulati trascorsi da quando la riproduzione e' partita
        la prima volta — cresce senza limite mentre e' in play, mai
        avvolto: e' questo il valore mostrato dal dashboard e congelato in
        pausa/cambio velocita'."""
        if record.status is LiveSimulationStatus.PLAYING and record.resumed_at_monotonic is not None:
            elapsed = record.frozen_elapsed_seconds + (
                time.monotonic() - record.resumed_at_monotonic
            ) * record.speed_multiplier
        else:
            elapsed = record.frozen_elapsed_seconds
        return max(0.0, elapsed)

    def _revealed_step_count(self, record: _LiveRecord) -> int:
        """Quanti step del giro CALCOLATO sono gia' rivelabili — se il
        tempo simulato ha gia' superato quanto e' stato calcolato finora
        (un'estensione in ritardo rispetto a una velocita' molto alta),
        resta fermo sull'ultimo step disponibile invece di sforare
        l'indice: la riproduzione VISIVA fa una breve pausa li', ma la
        posizione nel tempo (_elapsed_seconds) continua a scorrere per
        conto suo e la raggiunge non appena l'estensione finisce."""
        elapsed = self._elapsed_seconds(record)
        return max(1, min(record.steps_per_target, int(elapsed // STEP_SECONDS) + 1))

    def _maybe_extend(self, record: _LiveRecord) -> None:
        """Sottomette un ricalcolo con orizzonte raddoppiato quando il
        tempo simulato ha raggiunto EXTEND_THRESHOLD_FRACTION di quanto e'
        gia' stato calcolato — vedi il modulo docstring per il perche' del
        raddoppio invece di un incremento fisso. No-op se il primo calcolo
        non e' ancora finito, se una run non e' piu' attiva, o se
        un'estensione e' gia' in corso."""
        if record.computed_steps is None or record.extending:
            return
        if record.status not in {LiveSimulationStatus.PLAYING, LiveSimulationStatus.PAUSED}:
            return
        if self._elapsed_seconds(record) < record.horizon_seconds * EXTEND_THRESHOLD_FRACTION:
            return
        record.extending = True
        self._executor.submit(self._extend, record.job_id)

    def _zone_snapshots(self, record: _LiveRecord) -> list[LiveZoneSnapshot]:
        if record.computed_steps is None:
            return []
        index = self._revealed_step_count(record) - 1
        snapshots: list[LiveZoneSnapshot] = []
        for target, all_steps in zip(record.targets, record.computed_steps, strict=True):
            step = all_steps[index]
            snapshots.append(
                LiveZoneSnapshot(
                    zone_id=target.zone_id,  # type: ignore[arg-type]  # sempre valorizzato: "l'intera serra"
                    recipe={
                        "id": target.recipe.id,
                        "plant_type": target.recipe.plant_type,
                        "version": target.recipe.version,
                    },
                    phase_name=step.get("phase_name"),
                    sensors=step.get("sensors", {}),
                    models=step.get("models", {}),
                    active_actuators=_active_actuators(step),
                    timestamp_seconds=float(step.get("start_time_seconds") or 0.0),
                )
            )
        return snapshots

    def _job(self, record: _LiveRecord) -> LiveSimulationJob:
        total_computing_steps = record.steps_per_target * len(record.targets)
        computing_percent = (
            100.0
            if record.status is not LiveSimulationStatus.COMPUTING
            else round(record.computing_completed_steps / total_computing_steps * 100.0, 1)
            if total_computing_steps
            else 0.0
        )
        return LiveSimulationJob(
            id=record.job_id,
            zone_ids=[t.zone_id for t in record.targets],  # type: ignore[misc]
            horizon_seconds=record.horizon_seconds,
            status=record.status,
            speed_multiplier=record.speed_multiplier,
            elapsed_seconds=self._elapsed_seconds(record),
            computing_progress_percent=computing_percent,
            zones=self._zone_snapshots(record),
            created_at=record.created_at,
            started_playing_at=record.started_playing_at,
            stopped_at=record.stopped_at,
            expires_at=record.expires_at,
            error=record.error,
        )

    def _compute(self, run_id: str) -> None:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                return
            if record.cancel.is_set():
                record.status = LiveSimulationStatus.CANCELLED
                record.stopped_at = _now()
                record.expires_at = record.stopped_at + timedelta(minutes=EXPIRY_MINUTES)
                return

        try:
            all_steps: list[list[dict[str, Any]]] = []
            for index, target in enumerate(record.targets):
                def on_process(process: Any, record: _LiveRecord = record) -> None:
                    with self._lock:
                        record.process = process

                def progress_cb(
                    completed: int, record: _LiveRecord = record, offset: int = index * record.steps_per_target
                ) -> None:
                    with self._lock:
                        record.computing_completed_steps = offset + completed

                steps = execute_edge_steps(
                    target.recipe,
                    record.steps_per_target,
                    cancel_event=record.cancel,
                    progress_cb=progress_cb,
                    on_process=on_process,
                    environment_seed=record.environment_seed,
                )
                all_steps.append(steps)
            with self._lock:
                record.computed_steps = all_steps
                record.computing_completed_steps = record.steps_per_target * len(record.targets)
                record.status = LiveSimulationStatus.PLAYING
                record.resumed_at_monotonic = time.monotonic()
                record.started_playing_at = _now()
                record.process = None
        except InterruptedError:
            with self._lock:
                record.status = LiveSimulationStatus.CANCELLED
                record.stopped_at = _now()
                record.expires_at = record.stopped_at + timedelta(minutes=EXPIRY_MINUTES)
                record.process = None
        except Exception as error:
            with self._lock:
                record.status = LiveSimulationStatus.FAILED
                record.error = str(error)[:1000]
                record.stopped_at = _now()
                record.expires_at = record.stopped_at + timedelta(minutes=EXPIRY_MINUTES)
                record.process = None

    def _extend(self, run_id: str) -> None:
        """Ricalcola OGNI target da capo con il doppio degli step —
        deterministico a parita' di ricetta+seed (verificato: chiedere piu'
        step riproduce un prefisso identico), quindi lo scambio finale
        (sotto lock, un solo assegnamento) e' invisibile lato client: il
        prefisso gia' rivelato non cambia mai valore, solo la coda cresce.
        Un errore qui non tocca lo status della run (resta PLAYING/PAUSED
        sul suo orizzonte attuale): riprovera' al prossimo _maybe_extend."""
        with self._lock:
            record = self._records.get(run_id)
            if record is None or record.cancel.is_set():
                return
            targets = record.targets
            new_steps_per_target = record.steps_per_target * 2

        # Nessun progress_cb/on_process qui: a differenza del primo calcolo
        # (_compute, dove COMPUTING e' l'unico stato mostrato — vedi
        # renderLiveSimulationComputing lato frontend) un'estensione e'
        # invisibile per costruzione, e cancel_event basta da solo per
        # rispondere a un annullamento (execute_edge_steps lo controlla
        # comunque ogni 0.1s internamente).
        try:
            all_steps: list[list[dict[str, Any]]] = []
            for target in targets:
                if record.cancel.is_set():
                    raise InterruptedError("simulation cancelled")
                steps = execute_edge_steps(
                    target.recipe,
                    new_steps_per_target,
                    cancel_event=record.cancel,
                    environment_seed=record.environment_seed,
                )
                all_steps.append(steps)
            with self._lock:
                if record.cancel.is_set():
                    return
                record.computed_steps = all_steps
                record.steps_per_target = new_steps_per_target
                record.horizon_seconds = new_steps_per_target * STEP_SECONDS
                record.extending = False
        except Exception:
            # InterruptedError (annullata nel frattempo) o un guasto Edge
            # qualunque: la run resta comunque sul suo orizzonte attuale,
            # _maybe_extend riprovera' al prossimo poll.
            with self._lock:
                record.extending = False


live_simulation_manager = LiveSimulationManager()
