"""Esecutore singolo per anteprime batch isolate dal runtime operativo."""

from __future__ import annotations

import json
import math
import queue
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...edge_runner import configured_edge_executable, edge_is_ready
from ..recipes.models import Recipe
from .models import (
    STEP_SECONDS,
    SimulationCreate,
    SimulationJob,
    SimulationPreview,
    SimulationStatus,
)


EXPIRY_MINUTES = 30
MAX_CHART_POINTS = 1000
BATCH_TIMEOUT_SECONDS = 60.0


class SimulationBusy(Exception):
    """Un altro scenario sta gia usando l'unico worker batch."""


class SimulationInvalid(Exception):
    """La richiesta non individua nulla di simulabile (es. serra intera
    senza alcun settore con una ricetta assegnata)."""


class SimulationMissing(Exception):
    """Il job non esiste o la sua anteprima e scaduta."""


class SimulationNotReady(Exception):
    """Il risultato non e ancora disponibile."""


@dataclass(frozen=True)
class _Target:
    """Una singola ricetta da simulare — un settore reale in modalita' serra
    intera (zone_id valorizzato), oppure l'unica ricetta scelta a mano nella
    modalita' storica per singolo settore (zone_id None)."""

    recipe: Recipe
    zone_id: str | None = None


@dataclass
class _Record:
    job: SimulationJob
    targets: list[_Target]
    cancel: threading.Event = field(default_factory=threading.Event)
    # Un esito per target, nello stesso ordine di `targets`. La modalita'
    # storica per singolo settore ha sempre esattamente un target: result()
    # la spacchetta in un oggetto singolo per non cambiare il contratto HTTP
    # esistente (vedi SimulationManager.result).
    results: list[SimulationPreview] | None = None
    process: subprocess.Popen[str] | None = None

    @property
    def is_greenhouse(self) -> bool:
        return len(self.targets) != 1 or self.targets[0].zone_id is not None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fertilizer_keys() -> tuple[str, ...]:
    return ("nitrogen", "phosphorus", "potassium", "ph-up", "ph-down")


def _active_actuators(step: dict[str, Any]) -> dict[str, bool]:
    output = step.get("actuators", {}).get("output", {})
    valves = output.get("fertilizer_valves_open", {})
    return {
        "water_pump": bool(output.get("water_pump_on")),
        "lighting": float(output.get("lighting_power_watts") or 0.0) > 0.0,
        **{f"valve_{key}": bool(valves.get(key)) for key in _fertilizer_keys()},
    }


def _actuator_intervals(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    open_since: dict[str, float] = {}
    previous_end = 0.0
    for step in steps:
        start = float(step.get("start_time_seconds") or previous_end)
        duration = float(step.get("duration_seconds") or STEP_SECONDS)
        end = start + duration
        active = _active_actuators(step)
        for actuator, is_on in active.items():
            if is_on and actuator not in open_since:
                open_since[actuator] = start
            elif not is_on and actuator in open_since:
                intervals.append(
                    {
                        "actuator": actuator,
                        "start_seconds": open_since.pop(actuator),
                        "end_seconds": start,
                    }
                )
        previous_end = end
    for actuator, start in open_since.items():
        intervals.append(
            {
                "actuator": actuator,
                "start_seconds": start,
                "end_seconds": previous_end,
            }
        )
    return intervals


def _numeric_values(step: dict[str, Any]) -> dict[str, float]:
    merged = {
        **step.get("sensors", {}),
        **step.get("models", {}),
    }
    return {
        key: float(value)
        for key, value in merged.items()
        if key != "timestamp_seconds" and isinstance(value, (int, float))
    }


def _reduce_series(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not steps:
        return []
    segments: list[list[dict[str, Any]]] = []
    for step in steps:
        if not segments or segments[-1][-1].get("phase_name") != step.get("phase_name"):
            segments.append([])
        segments[-1].append(step)
    available = max(MAX_CHART_POINTS - len(segments), 0)
    allocations = [
        min(
            len(segment),
            1 + math.floor(available * len(segment) / len(steps)),
        )
        for segment in segments
    ]
    reduced: list[dict[str, Any]] = []
    for segment, allocation in zip(segments, allocations, strict=True):
        bucket_size = max(1, math.ceil(len(segment) / max(allocation, 1)))
        for offset in range(0, len(segment), bucket_size):
            bucket = segment[offset:offset + bucket_size]
            values = [_numeric_values(step) for step in bucket]
            keys = sorted({key for point in values for key in point})
            average: dict[str, float] = {}
            minimum: dict[str, float] = {}
            maximum: dict[str, float] = {}
            for key in keys:
                observed = [point[key] for point in values if key in point]
                if observed:
                    average[key] = sum(observed) / len(observed)
                    minimum[key] = min(observed)
                    maximum[key] = max(observed)
            first = bucket[0]
            last = bucket[-1]
            start = float(first.get("start_time_seconds") or 0.0)
            end = float(last.get("start_time_seconds") or start) + float(
                last.get("duration_seconds") or STEP_SECONDS
            )
            reduced.append(
                {
                    "start_seconds": start,
                    "end_seconds": end,
                    "phase_name": last.get("phase_name"),
                    "average": average,
                    "minimum": minimum,
                    "maximum": maximum,
                    "sample_count": len(bucket),
                }
            )
    return reduced


def _phase_targets(recipe: Recipe) -> list[dict[str, Any]]:
    cursor = 0.0
    result: list[dict[str, Any]] = []
    for phase in recipe.phases:
        end = cursor + phase.duration_hours * 3600.0
        result.append(
            {
                "name": phase.name,
                "start_seconds": cursor,
                "end_seconds": end,
                "targets": {
                    target.variable.value: {
                        "setpoint": target.setpoint,
                        "allowed_minimum": target.allowed_range.minimum,
                        "allowed_maximum": target.allowed_range.maximum,
                        "safety_minimum": target.safety_range.minimum,
                        "safety_maximum": target.safety_range.maximum,
                    }
                    for target in phase.targets
                },
            }
        )
        cursor = end
    return result


def _summary(steps: list[dict[str, Any]], intervals: list[dict[str, Any]]) -> dict[str, Any]:
    water = sum(float(step.get("delivered", {}).get("water_liters") or 0.0) for step in steps)
    fertilizer = {key: 0.0 for key in _fertilizer_keys()}
    for step in steps:
        delivered = step.get("delivered", {}).get("fertilizer_milliliters", {})
        for key in fertilizer:
            fertilizer[key] += float(delivered.get(key) or 0.0)
    active_seconds: dict[str, float] = {}
    for interval in intervals:
        actuator = str(interval["actuator"])
        active_seconds[actuator] = active_seconds.get(actuator, 0.0) + (
            float(interval["end_seconds"]) - float(interval["start_seconds"])
        )
    return {
        "delivered_water_liters": water,
        "delivered_fertilizer_milliliters": fertilizer,
        "actuator_active_seconds": active_seconds,
        "control_cycles": len(steps),
    }


class SimulationManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="batch-simulation")

    def _cleanup(self) -> None:
        now = _now()
        expired = [
            run_id
            for run_id, record in self._records.items()
            if record.job.expires_at is not None and record.job.expires_at <= now
        ]
        for run_id in expired:
            self._records.pop(run_id, None)

    def create(self, request: SimulationCreate, recipe: Recipe) -> SimulationJob:
        """Anteprima isolata di una singola ricetta, mai legata a un settore
        reale — il percorso storico, invariato. Vedi create_greenhouse per
        l'intera serra."""
        return self._create([_Target(recipe=recipe)], request)

    def create_greenhouse(
        self,
        request: SimulationCreate,
        targets: list[tuple[str, Recipe]],
    ) -> SimulationJob:
        """Un'unica simulazione che copre ogni settore produttivo con una
        ricetta assegnata, tutti sullo stesso arco temporale — "il tempo
        passa per tutti allo stesso modo". Ogni settore resta comunque un
        run dell'Edge indipendente (nessuna interazione fisica fra settori
        nel modello attuale, vedi GreenhouseManager lato Edge): eseguirli in
        sequenza con lo stesso step_seconds/duration_seconds produce lo
        stesso risultato di un'unica esecuzione multi-zona, senza dover
        toccare il formato di output del simulatore batch C++.
        """
        if not targets:
            raise SimulationInvalid(
                "no zone has an assigned recipe: nothing to simulate"
            )
        return self._create(
            [_Target(recipe=recipe, zone_id=zone_id) for zone_id, recipe in targets],
            request,
        )

    def _create(self, targets: list[_Target], request: SimulationCreate) -> SimulationJob:
        with self._lock:
            self._cleanup()
            if any(
                record.job.status in {SimulationStatus.QUEUED, SimulationStatus.RUNNING}
                for record in self._records.values()
            ):
                raise SimulationBusy("another batch simulation is already running")
            run_id = f"simulation-{uuid4().hex}"
            steps_per_target = request.duration_seconds // STEP_SECONDS
            is_greenhouse = len(targets) != 1 or targets[0].zone_id is not None
            job = SimulationJob(
                id=run_id,
                recipe_id=None if is_greenhouse else targets[0].recipe.id,
                zone_ids=[t.zone_id for t in targets] if is_greenhouse else None,
                duration_seconds=request.duration_seconds,
                total_steps=steps_per_target * len(targets),
                completed_steps=0,
                progress_percent=0.0,
                status=SimulationStatus.QUEUED,
                created_at=_now(),
            )
            record = _Record(job=job, targets=targets)
            self._records[run_id] = record
            self._executor.submit(self._execute, run_id)
            return job.model_copy(deep=True)

    def get(self, run_id: str) -> SimulationJob:
        with self._lock:
            self._cleanup()
            record = self._records.get(run_id)
            if record is None:
                raise SimulationMissing("simulation preview not found or expired")
            return record.job.model_copy(deep=True)

    def result(self, run_id: str) -> SimulationPreview | list[SimulationPreview]:
        """Un oggetto singolo per la modalita' storica per singolo settore
        (contratto HTTP invariato), una lista — un elemento per settore,
        stesso ordine di creazione — per la modalita' serra intera."""
        with self._lock:
            self._cleanup()
            record = self._records.get(run_id)
            if record is None:
                raise SimulationMissing("simulation preview not found or expired")
            if record.job.status is not SimulationStatus.SUCCEEDED or record.results is None:
                raise SimulationNotReady("simulation result is not ready")
            if record.is_greenhouse:
                return [preview.model_copy(deep=True) for preview in record.results]
            return record.results[0].model_copy(deep=True)

    def cancel_or_discard(self, run_id: str) -> None:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise SimulationMissing("simulation preview not found or expired")
            if record.job.status in {SimulationStatus.QUEUED, SimulationStatus.RUNNING}:
                record.cancel.set()
                if record.process is not None:
                    record.process.terminate()
            else:
                self._records.pop(run_id, None)

    def _update_progress(self, record: _Record, completed: int) -> None:
        record.job.completed_steps = min(completed, record.job.total_steps)
        record.job.progress_percent = round(
            record.job.completed_steps / record.job.total_steps * 100.0,
            1,
        )

    def _run_target(
        self,
        run_id: str,
        record: _Record,
        target: _Target,
        *,
        steps_per_target: int,
        step_offset: int,
    ) -> SimulationPreview:
        """Un'esecuzione Edge per un singolo target (settore o ricetta
        isolata). step_offset e' quanti step di ALTRI target precedenti sono
        gia' stati completati in questa stessa esecuzione — serve solo a far
        avanzare il progresso complessivo del job, non incide sul contenuto
        del risultato di questo target.
        """
        executable = configured_edge_executable()
        if not edge_is_ready(executable):
            raise RuntimeError(f"Edge simulator is not available at {executable}")
        with tempfile.TemporaryDirectory(prefix="smarthydro-simulation-") as directory:
            recipe_path = Path(directory) / "recipe.json"
            output_path = Path(directory) / "result.json"
            recipe_path.write_text(
                target.recipe.model_dump_json(by_alias=True),
                encoding="utf-8",
            )
            command = [
                str(executable),
                "--recipe", str(recipe_path),
                "--steps", str(steps_per_target),
                "--step-seconds", str(STEP_SECONDS),
                "--output", "json",
                "--progress",
            ]
            with output_path.open("w", encoding="utf-8") as output:
                deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
                process = subprocess.Popen(
                    command,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                with self._lock:
                    record.process = process
                assert process.stderr is not None
                errors: list[str] = []
                stderr_lines: queue.Queue[str | None] = queue.Queue()

                def read_stderr() -> None:
                    for stderr_line in process.stderr:
                        stderr_lines.put(stderr_line)
                    stderr_lines.put(None)

                threading.Thread(
                    target=read_stderr,
                    name=f"{run_id}-progress",
                    daemon=True,
                ).start()
                while True:
                    if record.cancel.is_set():
                        process.terminate()
                        process.wait(timeout=10)
                        raise InterruptedError("simulation cancelled")
                    if time.monotonic() > deadline:
                        process.terminate()
                        process.wait(timeout=10)
                        raise TimeoutError(
                            f"batch simulation exceeded {BATCH_TIMEOUT_SECONDS:.0f} seconds"
                        )
                    try:
                        line = stderr_lines.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    stripped = line.strip()
                    if stripped.startswith("PROGRESS "):
                        completed_text = stripped.removeprefix("PROGRESS ").split("/", 1)[0]
                        with self._lock:
                            self._update_progress(record, step_offset + int(completed_text))
                    elif stripped:
                        errors.append(stripped)
                return_code = process.wait(timeout=10)
            if record.cancel.is_set():
                raise InterruptedError("simulation cancelled")
            if return_code != 0:
                raise RuntimeError(" · ".join(errors) or "Edge simulation failed")
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        steps = payload.get("steps")
        if not isinstance(steps, list) or len(steps) != steps_per_target:
            raise RuntimeError("Edge returned an incomplete simulation")
        intervals = _actuator_intervals(steps)
        return SimulationPreview(
            job_id=run_id,
            zone_id=target.zone_id,
            recipe={
                "id": target.recipe.id,
                "plant_type": target.recipe.plant_type,
                "version": target.recipe.version,
            },
            duration_seconds=record.job.duration_seconds,
            series=_reduce_series(steps),
            actuator_intervals=intervals,
            summary=_summary(steps, intervals),
            phases=_phase_targets(target.recipe),
        )

    def _execute(self, run_id: str) -> None:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                return
            if record.cancel.is_set():
                record.job.status = SimulationStatus.CANCELLED
                record.job.completed_at = _now()
                record.job.expires_at = record.job.completed_at + timedelta(
                    minutes=EXPIRY_MINUTES
                )
                return
            record.job.status = SimulationStatus.RUNNING
            record.job.started_at = _now()

        try:
            steps_per_target = record.job.total_steps // len(record.targets)
            previews = [
                self._run_target(
                    run_id,
                    record,
                    target,
                    steps_per_target=steps_per_target,
                    step_offset=index * steps_per_target,
                )
                for index, target in enumerate(record.targets)
            ]
            with self._lock:
                record.results = previews
                self._update_progress(record, record.job.total_steps)
                record.job.status = SimulationStatus.SUCCEEDED
                record.job.completed_at = _now()
                record.job.expires_at = record.job.completed_at + timedelta(minutes=EXPIRY_MINUTES)
                record.process = None
        except InterruptedError:
            with self._lock:
                record.job.status = SimulationStatus.CANCELLED
                record.job.completed_at = _now()
                record.job.expires_at = record.job.completed_at + timedelta(minutes=EXPIRY_MINUTES)
                record.process = None
        except Exception as error:
            with self._lock:
                record.job.status = SimulationStatus.FAILED
                record.job.error = str(error)[:1000]
                record.job.completed_at = _now()
                record.job.expires_at = record.job.completed_at + timedelta(minutes=EXPIRY_MINUTES)
                record.process = None


simulation_manager = SimulationManager()
