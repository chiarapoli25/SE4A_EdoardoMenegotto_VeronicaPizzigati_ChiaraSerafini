"""Test per configured_edge_executable(): individuazione dell'eseguibile
edge_simulator tra le posizioni note di build a singola e multi
configurazione (Windows/Visual Studio produce bin/Debug/ o bin/Release/,
non bin/ direttamente)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from backend.app import edge_runner


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def isolated_bin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Punta EDGE_SIMULATOR_BIN_DIR/DEFAULT_EDGE_EXECUTABLE a una cartella
    temporanea vuota, cosi' i test non dipendono da cosa e' effettivamente
    compilato in questo ambiente e non lo modificano."""
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(edge_runner, "EDGE_SIMULATOR_BIN_DIR", bin_dir)
    monkeypatch.setattr(
        edge_runner, "DEFAULT_EDGE_EXECUTABLE", bin_dir / "edge_simulator"
    )
    monkeypatch.delenv("SMARTHYDRO_EDGE_SIMULATOR_EXECUTABLE", raising=False)
    monkeypatch.delenv("SMARTHYDRO_EDGE_EXECUTABLE", raising=False)
    return bin_dir


def test_finds_executable_directly_in_bin(isolated_bin_dir: Path) -> None:
    """Il caso storico (Linux/macOS, generatore a singola configurazione)."""
    target = isolated_bin_dir / "edge_simulator"
    _make_executable(target)
    assert edge_runner.configured_edge_executable() == target


def test_finds_executable_in_bin_debug(isolated_bin_dir: Path) -> None:
    """Windows/Visual Studio in configurazione Debug: bin/Debug/edge_simulator.exe."""
    target = isolated_bin_dir / "Debug" / "edge_simulator.exe"
    _make_executable(target)
    assert edge_runner.configured_edge_executable() == target


def test_finds_executable_in_bin_release(isolated_bin_dir: Path) -> None:
    """Windows/Visual Studio in configurazione Release: bin/Release/edge_simulator.exe."""
    target = isolated_bin_dir / "Release" / "edge_simulator.exe"
    _make_executable(target)
    assert edge_runner.configured_edge_executable() == target


def test_prefers_plain_bin_over_debug_and_release(isolated_bin_dir: Path) -> None:
    """Se piu' candidati esistono (build ripetute con generatori diversi
    nella stessa cartella), bin/ diretto vince — e' quello storico/atteso
    su questo repository."""
    plain = isolated_bin_dir / "edge_simulator"
    debug = isolated_bin_dir / "Debug" / "edge_simulator.exe"
    _make_executable(debug)
    _make_executable(plain)
    assert edge_runner.configured_edge_executable() == plain


def test_prefers_debug_over_release_when_bin_missing(
    isolated_bin_dir: Path,
) -> None:
    debug = isolated_bin_dir / "Debug" / "edge_simulator.exe"
    release = isolated_bin_dir / "Release" / "edge_simulator.exe"
    _make_executable(release)
    _make_executable(debug)
    assert edge_runner.configured_edge_executable() == debug


def test_falls_back_to_default_when_nothing_built(isolated_bin_dir: Path) -> None:
    """Nessuna build presente: il percorso storico bin/edge_simulator resta
    il valore restituito, cosi' il messaggio di errore a valle resta
    comprensibile invece di elencare percorsi mai esistiti."""
    assert (
        edge_runner.configured_edge_executable()
        == isolated_bin_dir / "edge_simulator"
    )


@pytest.mark.skipif(
    os.name != "posix",
    reason=(
        "il bit di esecuzione POSIX (chmod +x) e' il meccanismo che questo "
        "test verifica; su Windows non esiste un equivalente per-file "
        "affidabile (os.access(path, os.X_OK) vi si riduce di fatto a un "
        "controllo di sola esistenza, vedi edge_is_ready()), quindi i due "
        "file creati qui non sarebbero davvero distinguibili"
    ),
)
def test_non_executable_file_is_not_a_match(isolated_bin_dir: Path) -> None:
    """Un file presente ma senza permesso di esecuzione non conta come
    trovato (stesso criterio di edge_is_ready, usato anche a valle prima
    di avviare davvero il processo): con solo un file non eseguibile in
    bin/ e un eseguibile vero in bin/Debug/, deve vincere quest'ultimo,
    non il primo trovato per posizione."""
    bin_dir = isolated_bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    not_executable = bin_dir / "edge_simulator"
    not_executable.write_text("#!/bin/sh\n")
    # Nessun bit di esecuzione impostato su questo.
    debug = bin_dir / "Debug" / "edge_simulator.exe"
    _make_executable(debug)
    assert edge_runner.configured_edge_executable() == debug


def test_environment_override_always_wins(
    isolated_bin_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un override esplicito ha sempre precedenza, anche se un candidato
    valido esiste altrove — comportamento gia' esistente, non cambiato da
    questo fix."""
    built = isolated_bin_dir / "edge_simulator"
    _make_executable(built)
    override = tmp_path / "custom" / "my_edge_simulator"
    monkeypatch.setenv("SMARTHYDRO_EDGE_SIMULATOR_EXECUTABLE", str(override))
    assert edge_runner.configured_edge_executable() == override


def test_legacy_environment_variable_still_supported(
    isolated_bin_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom" / "legacy_edge_simulator"
    monkeypatch.setenv("SMARTHYDRO_EDGE_EXECUTABLE", str(override))
    assert edge_runner.configured_edge_executable() == override
