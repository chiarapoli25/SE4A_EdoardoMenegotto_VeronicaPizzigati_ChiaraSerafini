"""Esecuzione controllata dell'Edge C++ e validazione del suo output JSON."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EDGE_EXECUTABLE = (
    PROJECT_ROOT / "edge" / "build" / "bin" / "edge_simulator"
)
EDGE_TIMEOUT_SECONDS = 20


class EdgeUnavailable(RuntimeError):
    """L'eseguibile Edge non è disponibile o non è avviabile."""


class EdgeTimedOut(RuntimeError):
    """La simulazione non è terminata entro il timeout."""


class EdgeExecutionFailed(RuntimeError):
    """L'Edge ha rifiutato la ricetta o ha terminato con un errore."""


class EdgeOutputInvalid(RuntimeError):
    """L'Edge non ha prodotto il contratto JSON atteso."""


def configured_edge_executable() -> Path:
    """Legge il percorso del simulatore batch, con fallback locale."""
    configured = os.environ.get(
        "SMARTHYDRO_EDGE_SIMULATOR_EXECUTABLE"
    ) or os.environ.get("SMARTHYDRO_EDGE_EXECUTABLE")
    return Path(configured).expanduser() if configured else DEFAULT_EDGE_EXECUTABLE


def edge_is_ready(executable: Path) -> bool:
    """Controlla esistenza e permesso di esecuzione senza avviare processi."""
    return executable.is_file() and os.access(executable, os.X_OK)


def run_edge_simulation(
    executable: Path,
    recipe_path: Path,
    *,
    steps: int,
    step_seconds: float,
) -> dict[str, Any]:
    """Avvia l'Edge senza shell e restituisce il documento JSON validato."""
    if not edge_is_ready(executable):
        raise EdgeUnavailable(
            f"Edge executable not available at {executable}"
        )

    command = [
        str(executable),
        "--recipe",
        str(recipe_path),
        "--steps",
        str(steps),
        "--step-seconds",
        str(step_seconds),
        "--output",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=EDGE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise EdgeUnavailable("Edge executable was not found") from error
    except PermissionError as error:
        raise EdgeUnavailable("Edge executable is not executable") from error
    except subprocess.TimeoutExpired as error:
        raise EdgeTimedOut(
            f"Edge simulation exceeded {EDGE_TIMEOUT_SECONDS} seconds"
        ) from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown Edge error"
        raise EdgeExecutionFailed(detail)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise EdgeOutputInvalid("Edge output is not valid JSON") from error

    if not isinstance(payload, dict):
        raise EdgeOutputInvalid("Edge output must be a JSON object")
    if not isinstance(payload.get("recipe"), dict):
        raise EdgeOutputInvalid("Edge output is missing recipe metadata")
    cycles = payload.get("steps")
    if not isinstance(cycles, list) or len(cycles) != steps:
        raise EdgeOutputInvalid(
            f"Edge returned {len(cycles) if isinstance(cycles, list) else 0} "
            f"cycles, expected {steps}"
        )
    required_cycle_keys = {
        "cycle",
        "phase_name",
        "sensors",
        "models",
        "decisions",
        "actuators",
        "delivered",
        "environment",
    }
    if any(
        not isinstance(cycle, dict)
        or not required_cycle_keys.issubset(cycle)
        for cycle in cycles
    ):
        raise EdgeOutputInvalid("One or more Edge cycles are incomplete")
    return payload
