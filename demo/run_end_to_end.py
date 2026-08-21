#!/usr/bin/env python3
"""Avvia backend ed Edge reali e verifica lo scenario SmartHydro end to end."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = Path(__file__).with_name("end_to_end_scenario.json")


class DemoFailure(RuntimeError):
    """Errore di una verifica osservabile dello scenario."""


def step(message: str) -> None:
    print(f"[DEMO] {message}", flush=True)


def choose_port(requested: int) -> int:
    if requested:
        return requested
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            raw = response.read()
    except HTTPError as error:
        diagnostic = error.read().decode("utf-8", errors="replace")
        raise DemoFailure(
            f"{method} {path} returned HTTP {error.code}: {diagnostic}"
        ) from error
    except URLError as error:
        raise DemoFailure(f"{method} {path} failed: {error.reason}") from error
    return json.loads(raw) if raw else None


def wait_for(
    description: str,
    probe: Callable[[], Any],
    timeout: float = 15.0,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value:
                return value
        except (DemoFailure, OSError, sqlite3.Error, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(0.05)
    suffix = f"; last error: {last_error}" if last_error else ""
    raise DemoFailure(f"timeout waiting for {description}{suffix}")


def read_json_file(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def command_status(database: Path, command_id: str) -> tuple[str, str | None] | None:
    with sqlite3.connect(database, timeout=1.0) as connection:
        row = connection.execute(
            "SELECT status, result_message FROM runtime_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    return None if row is None else (str(row[0]), row[1])


def wait_command(database: Path, command_id: str) -> None:
    result = wait_for(
        f"completion of command {command_id}",
        lambda: (
            status
            if (status := command_status(database, command_id))
            and status[0] != "pending"
            else None
        ),
    )
    if result[0] != "succeeded":
        raise DemoFailure(f"command {command_id} was rejected: {result[1]}")


def enqueue(
    base_url: str,
    database: Path,
    zone_id: str,
    command_id: str,
    command_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    created = request_json(
        base_url,
        "POST",
        f"/api/v1/zones/{zone_id}/commands",
        {
            "command_id": command_id,
            "command_type": command_type,
            "payload": payload or {},
        },
    )
    if created["status"] != "pending":
        raise DemoFailure(f"command {command_id} was not queued")
    wait_command(database, command_id)


def event_history(base_url: str, zone_id: str) -> list[dict[str, Any]]:
    return request_json(base_url, "GET", f"/api/v1/zones/{zone_id}/events?limit=1000")


def event_exists(
    base_url: str,
    zone_id: str,
    event_type: str,
    current_state: str | None = None,
) -> bool:
    for event in event_history(base_url, zone_id):
        if event["event_type"] != event_type:
            continue
        if (
            current_state is None
            or event["payload"].get("current_state") == current_state
        ):
            return True
    return False


def stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    
    if sys.platform == "win32":
        # Comportamento per Windows
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    else:
        # Comportamento originale per Mac/Linux
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def tail(path: Path, lines: int = 40) -> str:
    if not path.exists():
        return "(log unavailable)"
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def build_edge() -> Path:
    build = ROOT / "edge" / "build"
    subprocess.run(
        ["cmake", "-S", str(ROOT / "edge"), "-B", str(build)],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["cmake", "--build", str(build), "--target", "edge"],
        cwd=ROOT,
        check=True,
    )
    executable = build / "bin" / "edge"
    if not executable.is_file():
        raise DemoFailure(f"Edge executable not found at {executable}")
    return executable


def run(args: argparse.Namespace) -> None:
    scenario = read_json_file(args.scenario.resolve())
    edge_id = scenario["edge_id"]
    zones = scenario["zones"]
    if len(zones) != 2:
        raise DemoFailure("the end-to-end scenario must define exactly two zones")

    executable = ROOT / "edge" / "build" / "bin" / "edge"
    if not args.skip_build:
        step("configuring and building the Edge executable")
        executable = build_edge()
    elif not executable.is_file():
        raise DemoFailure(f"--skip-build requires {executable}")

    artifacts = Path(tempfile.mkdtemp(prefix="smarthydro-e2e-"))
    database = artifacts / "smarthydro-demo.db"
    outbox = artifacts / "edge-outbox"
    backend_log = artifacts / "backend.log"
    edge_log = artifacts / "edge.log"
    port = choose_port(args.port)
    base_url = f"http://127.0.0.1:{port}"
    backend: subprocess.Popen[Any] | None = None
    edge: subprocess.Popen[Any] | None = None
    success = False

    try:
        environment = os.environ.copy()
        environment["SMARTHYDRO_DATABASE_PATH"] = str(database)
        environment["SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS"] = "30"
        current_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(ROOT)
            if not current_pythonpath
            else str(ROOT) + os.pathsep + current_pythonpath
        )

        step(f"starting isolated backend at {base_url}")
        with backend_log.open("w", encoding="utf-8") as output:
            backend = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        wait_for("backend health", lambda: request_json(base_url, "GET", "/health"))

        step("registering two zones with different catalog recipes")
        for zone in zones:
            request_json(
                base_url,
                "POST",
                "/api/v1/zones",
                {
                    "id": zone["id"],
                    "name": zone["name"],
                    "department_number": zone["department_number"],
                    "sector_number": zone["sector_number"],
                    "plant_species": zone["plant_species"],
                    "assigned_edge_id": edge_id,
                    "active_recipe_id": zone["recipe_id"],
                },
            )

        step("starting the real C++ Edge service")
        with edge_log.open("w", encoding="utf-8") as output:
            edge = subprocess.Popen(
                [
                    str(executable),
                    "--edge-id",
                    edge_id,
                    "--backend-url",
                    base_url,
                    "--outbox-path",
                    str(outbox),
                    "--step-seconds",
                    "1",
                    "--command-poll-ms",
                    "100",
                    "--service-loop-ms",
                    "20",
                    "--max-catch-up-steps",
                    "16",
                ],
                cwd=ROOT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        expected_ids = {zone["id"] for zone in zones}
        assignments = outbox / "assigned-zones.json"
        wait_for(
            "Edge assignment reconciliation",
            lambda: assignments.exists()
            and set(read_json_file(assignments)) == expected_ids,
        )

        step("activating both cultivations and waiting for telemetry")
        for zone in zones:
            enqueue(
                base_url,
                database,
                zone["id"],
                f"demo-activate-{zone['id']}",
                "ActivateCultivation",
                {
                    "cultivation_id": zone["cultivation_id"],
                    "recipe_id": zone["recipe_id"],
                },
            )
        latest: dict[str, dict[str, Any]] = {}
        for zone in zones:
            latest[zone["id"]] = wait_for(
                f"telemetry from {zone['id']}",
                lambda zone_id=zone["id"]: request_json(
                    base_url,
                    "GET",
                    f"/api/v1/zones/{zone_id}/telemetry/latest",
                ),
            )
            if latest[zone["id"]]["active_recipe_id"] != zone["recipe_id"]:
                raise DemoFailure(f"wrong active recipe for {zone['id']}")

        strategy = scenario["strategy_change"]
        step("changing one control Strategy at runtime")
        enqueue(
            base_url,
            database,
            strategy["zone_id"],
            strategy["command_id"],
            "ChangeStrategy",
            {
                "variable": strategy["variable"],
                "strategy": strategy["strategy"],
                "parameters": strategy["parameters"],
            },
        )
        enqueue(
            base_url,
            database,
            strategy["zone_id"],
            "demo-confirm-changed-strategy",
            "ConfirmConfiguration",
            {"variable": strategy["variable"]},
        )
        wait_for(
            "Strategy projection",
            lambda: request_json(
                base_url, "GET", f"/api/v1/zones/{strategy['zone_id']}"
            )["current_strategies"][strategy["variable"]]
            == strategy["strategy"],
        )

        phase = scenario["phase_advance"]
        previous_phase = request_json(
            base_url, "GET", f"/api/v1/zones/{phase['zone_id']}"
        )["current_phase"]
        step("advancing the recipe phase manually")
        enqueue(
            base_url,
            database,
            phase["zone_id"],
            phase["command_id"],
            "AdvanceRecipePhase",
        )
        current_phase = wait_for(
            "recipe phase change",
            lambda: (
                value
                if (value := request_json(
                    base_url, "GET", f"/api/v1/zones/{phase['zone_id']}"
                )["current_phase"])
                != previous_phase
                else None
            ),
        )

        fault = scenario["recoverable_fault"]
        step("injecting a temporary sensor fault and observing automatic recovery")
        enqueue(
            base_url,
            database,
            fault["zone_id"],
            fault["command_id"],
            "InjectFault",
            {
                key: value
                for key, value in fault.items()
                if key not in {"zone_id", "command_id"}
            },
        )
        wait_for(
            "FaultDetected event",
            lambda: event_exists(base_url, fault["zone_id"], "FaultDetected"),
        )
        wait_for(
            "Degraded transition",
            lambda: event_exists(
                base_url, fault["zone_id"], "StateChanged", "Degraded"
            ),
        )
        wait_for(
            "automatic recovery to Nominal",
            lambda: event_exists(base_url, fault["zone_id"], "StateChanged", "Nominal"),
        )

        removal = scenario["remove_zone"]
        step("pausing and removing one zone from the Edge assignment")
        enqueue(
            base_url,
            database,
            removal["zone_id"],
            removal["pause_command_id"],
            "PauseCultivation",
        )
        wait_for(
            "Paused lifecycle",
            lambda: request_json(
                base_url, "GET", f"/api/v1/zones/{removal['zone_id']}"
            )["lifecycle_state"]
            == "Paused",
        )
        request_json(
            base_url,
            "PATCH",
            f"/api/v1/zones/{removal['zone_id']}",
            {"assigned_edge_id": None},
        )
        remaining_zone = next(
            zone["id"]
            for zone in zones
            if zone["id"] != removal["zone_id"]
        )
        wait_for(
            "removed assignment in the persistent Edge cache",
            lambda: assignments.exists()
            and read_json_file(assignments) == [remaining_zone],
        )

        sequence_before = request_json(
            base_url,
            "GET",
            f"/api/v1/zones/{remaining_zone}/telemetry/latest",
        )["sequence_number"]
        ignored = request_json(
            base_url,
            "POST",
            f"/api/v1/zones/{removal['zone_id']}/commands",
            {
                "command_id": removal["ignored_command_id"],
                "command_type": "AdvanceRecipePhase",
                "payload": {},
            },
        )
        time.sleep(0.6)
        status = command_status(database, removal["ignored_command_id"])
        if ignored["status"] != "pending" or status is None or status[0] != "pending":
            raise DemoFailure("the removed zone accepted a new runtime command")
        sequence_after = wait_for(
            "continued telemetry from the remaining zone",
            lambda: (
                sequence
                if (sequence := request_json(
                    base_url,
                    "GET",
                    f"/api/v1/zones/{remaining_zone}/telemetry/latest",
                )["sequence_number"])
                > sequence_before
                else None
            ),
        )

        summary = {
            "zones_activated": sorted(expected_ids),
            "recipes": {zone["id"]: zone["recipe_id"] for zone in zones},
            "strategy_changed": f"{strategy['variable']} -> {strategy['strategy']}",
            "phase_changed": f"{previous_phase} -> {current_phase}",
            "fault": "FaultDetected, Degraded, automatic Nominal recovery",
            "removed_zone": removal["zone_id"],
            "remaining_zone_last_sequence": sequence_after,
        }
        step("SUCCESS: all end-to-end checks passed")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        success = True
    finally:
        stop_process(edge)
        stop_process(backend)
        if success and not args.keep_artifacts:
            shutil.rmtree(artifacts)
        else:
            print(f"[DEMO] artifacts: {artifacts}", file=sys.stderr)
            if not success:
                print(f"\n--- backend.log ---\n{tail(backend_log)}", file=sys.stderr)
                print(f"\n--- edge.log ---\n{tail(edge_log)}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated SmartHydro Edge/backend end-to-end demo."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="backend port; 0 selects a free port",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse edge/build/bin/edge",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="keep database and process logs",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args())
    except (DemoFailure, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"[DEMO] FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error
