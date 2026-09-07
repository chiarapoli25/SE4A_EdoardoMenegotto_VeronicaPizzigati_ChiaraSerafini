"""Esecuzione controllata dell'Edge C++ e validazione del suo output JSON."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EDGE_SIMULATOR_BIN_DIR = PROJECT_ROOT / "edge" / "build" / "bin"
DEFAULT_EDGE_EXECUTABLE = EDGE_SIMULATOR_BIN_DIR / "edge_simulator"
EDGE_TIMEOUT_SECONDS = 20


class EdgeUnavailable(RuntimeError):
    """L'eseguibile Edge non è disponibile o non è avviabile."""


class EdgeTimedOut(RuntimeError):
    """La simulazione non è terminata entro il timeout."""


class EdgeExecutionFailed(RuntimeError):
    """L'Edge ha rifiutato la ricetta o ha terminato con un errore."""


class EdgeOutputInvalid(RuntimeError):
    """L'Edge non ha prodotto il contratto JSON atteso."""


def edge_is_ready(executable: Path) -> bool:
    """Controlla esistenza e permesso di esecuzione senza avviare processi.

    Il controllo del permesso di esecuzione (os.access(..., os.X_OK)) viene
    saltato su Windows: li' non esiste un bit di esecuzione per-file
    paragonabile a quello POSIX (l'eseguibilita' e' decisa dall'estensione,
    gia' distinta da _edge_simulator_candidates() tramite i nomi
    "edge_simulator"/"edge_simulator.exe"), e os.access(os.X_OK) su Windows
    si limita di fatto a verificare l'esistenza del file — non offre quindi
    alcuna protezione reale in piu' rispetto a is_file(), a fronte del
    rischio di un raro falso negativo se mai si comportasse diversamente
    (ACL insolite, filesystem di rete). Su POSIX il controllo resta
    invariato: e' li' che un file presente ma privo del bit +x va scartato
    (vedi test_non_executable_file_is_not_a_match, che per lo stesso motivo
    e' marcato solo-POSIX in backend/tests/test_edge_runner.py)."""
    if not executable.is_file():
        return False
    if os.name == "nt":
        return True
    return os.access(executable, os.X_OK)


def _edge_simulator_candidates() -> list[Path]:
    """Ogni percorso in cui un edge_simulator compilato può trovarsi.

    Un generatore CMake a singola configurazione (Makefiles/Ninja, il caso
    storico su Linux/macOS) mette l'eseguibile direttamente in bin/. I
    generatori multi-configurazione di Windows (Visual Studio) lo mettono
    invece in una sottocartella bin/<Config>/ a seconda della
    configurazione scelta in fase di build (Debug o Release), con
    estensione .exe. Si prova ciascuna combinazione nell'ordine
    bin/, bin/Debug/, bin/Release/, senza assumere la piattaforma
    corrente (nessuna dipendenza da os.name/sys.platform): un eseguibile
    con o senza .exe viene riconosciuto ovunque si trovi.
    """
    directories = (
        EDGE_SIMULATOR_BIN_DIR,
        EDGE_SIMULATOR_BIN_DIR / "Debug",
        EDGE_SIMULATOR_BIN_DIR / "Release",
    )
    names = ("edge_simulator", "edge_simulator.exe")
    return [directory / name for directory in directories for name in names]


def configured_edge_executable() -> Path:
    """Legge il percorso del simulatore batch, con fallback locale.

    Un override esplicito via variabile d'ambiente ha sempre precedenza
    assoluta e non viene validato qui (lo fa edge_is_ready quando serve
    davvero avviarlo). In assenza di override, cerca un eseguibile già
    pronto tra le posizioni note (vedi _edge_simulator_candidates) e
    restituisce la prima trovata; se nessuna esiste ancora (build non
    fatta), ricade sul percorso storico DEFAULT_EDGE_EXECUTABLE, cosi'
    il messaggio "Edge simulator is not available at ..." resta
    comprensibile invece di elencare percorsi mai esistiti.
    """
    configured = os.environ.get(
        "SMARTHYDRO_EDGE_SIMULATOR_EXECUTABLE"
    ) or os.environ.get("SMARTHYDRO_EDGE_EXECUTABLE")
    if configured:
        return Path(configured).expanduser()
    for candidate in _edge_simulator_candidates():
        if edge_is_ready(candidate):
            return candidate
    return DEFAULT_EDGE_EXECUTABLE


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
