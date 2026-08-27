#!/usr/bin/env python3
"""Avvia la demo SmartHydro su Windows, macOS e Linux."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:8000"
HEALTH_URL = f"{BASE_URL}/health"
DASHBOARD_URL = f"{BASE_URL}/dashboard/"


def project_python() -> Path:
    """Preferisce il virtual environment del progetto su ogni sistema."""
    candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        Path(sys.executable),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("non trovo un interprete Python utilizzabile")


def backend_is_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.0) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload == {"status": "healthy"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def wait_for_backend(process: subprocess.Popen[bytes], timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if backend_is_healthy():
            return
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"il backend si e chiuso durante l'avvio (codice {return_code})"
            )
        time.sleep(0.5)
    raise RuntimeError(f"il backend non ha risposto entro {timeout_seconds:.0f} secondi")


def stop_backend(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    print("\n[avvia_demo] Arresto il backend...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> int:
    python = project_python()
    print(f"[avvia_demo] Python: {python}")

    dependency_check = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if dependency_check.returncode != 0:
        print("[avvia_demo] Mancano le dipendenze del backend.", file=sys.stderr)
        print(
            f"[avvia_demo] Esegui: {python} -m pip install -r backend/requirements.txt",
            file=sys.stderr,
        )
        return 1

    backend: subprocess.Popen[bytes] | None = None
    if backend_is_healthy():
        print(f"[avvia_demo] Backend gia attivo su {BASE_URL}; lo riutilizzo.")
    else:
        print("[avvia_demo] Avvio il backend FastAPI...")
        backend = subprocess.Popen(
            [str(python), "-m", "uvicorn", "backend.app.main:app"],
            cwd=ROOT,
        )
        try:
            wait_for_backend(backend)
        except Exception:
            stop_backend(backend)
            raise
        print("[avvia_demo] Backend pronto.")

    print("[avvia_demo] Carico i dati dimostrativi...")
    seed = subprocess.run(
        [str(python), str(ROOT / "demo" / "seed_test_scenario.py")],
        cwd=ROOT,
    )
    if seed.returncode != 0:
        if backend is not None:
            stop_backend(backend)
        print(
            f"[avvia_demo] Il caricamento demo e fallito (codice {seed.returncode}).",
            file=sys.stderr,
        )
        return seed.returncode

    print(f"[avvia_demo] Apro {DASHBOARD_URL}")
    webbrowser.open(DASHBOARD_URL)

    if backend is None:
        print("[avvia_demo] Fatto. Il backend era gia in esecuzione.")
        return 0

    print("[avvia_demo] Demo attiva. Premi Ctrl+C per arrestare il backend.")
    try:
        return backend.wait()
    except KeyboardInterrupt:
        stop_backend(backend)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"[avvia_demo] ERRORE: {error}", file=sys.stderr)
        raise SystemExit(1) from error
