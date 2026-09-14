"""Popolamento SmartHydro basato ESCLUSIVAMENTE sull'Edge C++ reale.
USO: python demo/seed_dev_data.py
"""

from __future__ import annotations

import copy
import json
import os
import time
import math
import random
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.core.database import get_connection, init_db
from backend.app.features.users.models import UserRole
from backend.app.features.users.repository import UsernameConflict, create_user
from backend.app.features.users.security import hash_password

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "pass123"

# Account Agronomi nominati
SEED_ACCOUNTS = (
    (ADMIN_USERNAME, ADMIN_PASSWORD, UserRole.ADMIN, "Amministratore"),
    ("mario", "Mario123!", UserRole.AGRONOMO, "Mario"),
    ("elena", "Elena123!", UserRole.AGRONOMO, "Elena"),
    ("matteo", "Matteo123!", UserRole.AGRONOMO, "Matteo"),
    ("irene", "Irene123!", UserRole.AGRONOMO, "Irene"),
)

BASE_URL = os.environ.get("SMARTHYDRO_BASE_URL", "https://smarthydro-production-2a53.up.railway.app").rstrip("/")
EDGE_ID = "edge-serra-1"
_AUTH_TOKEN: str | None = None

DEV_TIME_SCALE = 60.0
STEP_SECONDS_HINT = 900.0
SHORT_FAULT_DURATION_SECONDS = 60.0
COMMAND_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 2.0

DEGRADED_DEMO_ZONE_ID = "r1-s2"
LOCKDOWN_DEMO_ZONE_ID = "r4-s1"
EDGE_READY_PROBE_SECONDS = 25.0

PHASE_ADVANCE_ZONE_IDS = ["r1-s1", "r2-s1", "r3-s1"]
QUARANTINE_BACKDATE = timedelta(hours=30)
QUARANTINE_MINUTES_PER_SECOND = 10.0
QUARANTINE_REAL_WAIT_SECONDS = (
    QUARANTINE_BACKDATE.total_seconds() / 60.0 / QUARANTINE_MINUTES_PER_SECOND
)
QUARANTINE_INSTANT_PLANT_SOURCES = [
    ("plant-early-1", "r2-s1"),
    ("plant-early-2", "r4-s1"),
]
NARRATION_TICK_SECONDS = 5.0

# Zona dedicata alla dimostrazione del lockdown safety_range
LOCKDOWN_SAFETY_DEMO_ZONE_ID = "r4-s2"
LOCKDOWN_SAFETY_DEMO_DEPARTMENT = 4
LOCKDOWN_SAFETY_DEMO_SECTOR = 2
LOCKDOWN_SAFETY_DEMO_RECIPE_ID = "recipe-safety-demo-g"

PRODUCTION_ZONES = [
    ("r1-s1", "Reparto 1 - Settore 1", 1, 1, True),
    ("r1-s2", "Reparto 1 - Settore 2", 1, 2, True),
    ("r2-s1", "Reparto 2 - Settore 1", 2, 1, True),
    ("r2-s2", "Reparto 2 - Settore 2", 2, 2, False),
    ("r3-s1", "Reparto 3 - Settore 1", 3, 1, True),
    ("r4-s1", "Reparto 4 - Settore 1", 4, 1, True),
]

QUARANTINE_ZONES = [
    ("r5-s1", "Quarantena - Settore 1", 5, 1),
]

ORPHAN_QUARANTINE_ZONE_ID = "r3-s2"
ORPHAN_QUARANTINE_PLANT_ID = "plant-orphan-1"

def _upsert_user(connection: sqlite3.Connection, username: str, password: str, role: UserRole, display_name: str) -> None:
    try:
        create_user(connection, username, password, role, display_name)
        print(f"[seed] utente creato: {username} (ruolo={role.value})")
    except UsernameConflict:
        connection.execute(
            "UPDATE users SET password_hash = ?, role = ?, display_name = ? WHERE username = ?",
            (hash_password(password), role.value, display_name, username),
        )
        connection.commit()
        print(f"[seed] utente gia' esistente, password/ruolo aggiornati: {username} (ruolo={role.value})")

def _parse_json_body(text: str) -> dict:
    if not text: return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"detail": text.strip() or "(risposta vuota, non JSON)"}

def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if _AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {_AUTH_TOKEN}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, _parse_json_body(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, _parse_json_body(error.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise SystemExit(f"[seed] impossibile raggiungere {BASE_URL} ({error})") from error

def login_as_admin() -> None:
    global _AUTH_TOKEN
    status, body = request("POST", "/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    if status == 401:
        raise SystemExit("[seed] impossibile autenticarsi come amministratore (401).")
    if status != 200:
        raise SystemExit(f"[seed] errore inatteso in login (status {status})")
    _AUTH_TOKEN = body["token"]
    print(f"[seed] autenticato come {ADMIN_USERNAME!r}")

def pick_recipes(department_number: int, how_many: int) -> list[dict]:
    status, body = request("GET", f"/recipes?department_number={department_number}")
    if status != 200 or not isinstance(body, list):
        raise SystemExit(f"[seed] impossibile leggere il catalogo per il reparto {department_number}")
    candidates = [r for r in body if r.get("department_number") == department_number]
    if len(candidates) < how_many:
        raise SystemExit(f"[seed] servono {how_many} ricette, trovate {len(candidates)}")
    return candidates[:how_many]

def ensure_safety_lockdown_recipe() -> str:
    status, catalog = request("GET", f"/recipes?department_number={LOCKDOWN_SAFETY_DEMO_DEPARTMENT}")
    template = next((r for r in catalog if r.get("id") != LOCKDOWN_SAFETY_DEMO_RECIPE_ID), None) if status == 200 and isinstance(catalog, list) else None
    if template is None:
        raise SystemExit("[seed] impossibile trovare una ricetta base per il lockdown.")

    recipe = copy.deepcopy(template)
    recipe.pop("department_name", None)
    recipe["id"] = LOCKDOWN_SAFETY_DEMO_RECIPE_ID
    recipe["version"] = 1
    plant_type = "Fragola"
    recipe["plant_type"] = plant_type

    narrow_target = {
        "variable": "soil_moisture",
        "setpoint": 50.0,
        "allowed_range": {"minimum": 45.0, "maximum": 55.0},
        "safety_range": {"minimum": 43.0, "maximum": 57.0},
        "suggested_phase_dose_milliliters": 0.0,
    }
    for phase in recipe["phases"]:
        phase["targets"] = [narrow_target if t["variable"] == "soil_moisture" else t for t in phase["targets"]]

    status, body = request("POST", "/recipes", recipe)
    if status == 201:
        print(f"[seed] ricetta demo creata: {LOCKDOWN_SAFETY_DEMO_RECIPE_ID!r}")
    elif status == 409:
        print(f"[seed] ricetta demo {LOCKDOWN_SAFETY_DEMO_RECIPE_ID!r} gia' esistente.")
    else:
        raise SystemExit(f"[seed] errore creando la ricetta demo: {status} {body}")
    return plant_type

def ensure_safety_lockdown_zone(plant_type: str) -> None:
    ensure_zone(
        LOCKDOWN_SAFETY_DEMO_ZONE_ID,
        "Fragola",
        LOCKDOWN_SAFETY_DEMO_DEPARTMENT,
        LOCKDOWN_SAFETY_DEMO_SECTOR,
        {"plant_species": plant_type, "active_recipe_id": LOCKDOWN_SAFETY_DEMO_RECIPE_ID, "assigned_edge_id": EDGE_ID},
    )

def ensure_zone(zone_id: str, name: str, department: int, sector: int, payload_extra: dict) -> None:
    payload = {"id": zone_id, "name": name, "department_number": department, "sector_number": sector, **payload_extra}
    status, body = request("POST", "/zones", payload)
    if status == 201:
        print(f"[seed] creata {zone_id}")
    elif status == 409:
        update = {k: v for k, v in payload_extra.items() if k != "plant_species"}
        request("PATCH", f"/zones/{zone_id}", update)
        print(f"[seed] {zone_id} già esistente, aggiornata")
    else:
        raise SystemExit(f"[seed] errore creando {zone_id}: {status} {body}")

def delete_zone(zone_id: str) -> None:
    status, body = request("DELETE", f"/zones/{zone_id}")
    if status == 204:
        print(f"[seed] {zone_id} eliminata.")
    elif status == 404:
        print(f"[seed] {zone_id} già assente, nulla da eliminare.")

def ensure_orphan_quarantine_scenario(plant_species: str) -> None:
    ensure_zone(ORPHAN_QUARANTINE_ZONE_ID, "Reparto 3 - Settore 2 (mai attivato)", 3, 2, {"plant_species": plant_species})
    ensure_plant(ORPHAN_QUARANTINE_PLANT_ID, plant_species, ORPHAN_QUARANTINE_ZONE_ID)
    ensure_quarantine(ORPHAN_QUARANTINE_PLANT_ID, "r5-s1", "chiusura del settore di origine")
    delete_zone(ORPHAN_QUARANTINE_ZONE_ID)

def ensure_cultivation(zone_id: str, recipe_id: str) -> None:
    request("POST", "/cultivations", {"zone_id": zone_id, "recipe_id": recipe_id})

def push_historical_telemetry(zone_id: str) -> None:
    print(f"[seed] Iniezione telemetria storica simulata per {zone_id}...")
    now = datetime.now(timezone.utc)
    ore_passate = 24
    campioni_ora = 4
    minuti_step = 60 // campioni_ora
    
    for i in range(ore_passate * campioni_ora):
        ts = now - timedelta(hours=ore_passate) + timedelta(minutes=minuti_step * i)
        ora_del_giorno = ts.hour + (ts.minute / 60.0)
        
        temp = round(22.0 + 6.0 * math.sin(math.pi * (ora_del_giorno - 8) / 12) + random.gauss(0, 0.3), 2)
        hum = round(max(0, min(100, 75.0 - (temp - 20) * 3.0 + random.gauss(0, 1.5))), 2)
        ciclo_svuotamento = (ora_del_giorno % 8) / 8.0 
        water = round(100.0 - (ciclo_svuotamento * 30) + random.gauss(0, 0.5), 2)
        
        request("POST", f"/zones/{zone_id}/telemetry", {
            "timestamp": ts.isoformat(),
            "temperature": temp,
            "humidity": hum,
            "water_level": water
        })

def enqueue_command(zone_id: str, command_id: str, command_type: str, payload: dict) -> bool:
    status, _ = request("POST", f"/zones/{zone_id}/commands", {"command_id": command_id, "command_type": command_type, "payload": payload})
    if status != 201: return False
    print(f"[seed] comando {command_type} accodato su {zone_id}")
    return True

def enqueue_and_wait_command(zone_id: str, command_id: str, command_type: str, payload: dict, *, timeout_seconds: float = COMMAND_TIMEOUT_SECONDS) -> str | None:
    command_payload = {"command_id": command_id, "command_type": command_type, "payload": payload}
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    
    while time.monotonic() < deadline:
        status, body = request("POST", f"/zones/{zone_id}/commands", command_payload)
        if status != 201: return None
        last_status = body.get("status")
        if last_status != "pending": break
        time.sleep(POLL_INTERVAL_SECONDS)
        
    print(f"[seed] comando {command_type} su {zone_id} concluso: {last_status}")
    return last_status

def get_zone(zone_id: str) -> dict | None:
    status, body = request("GET", f"/zones/{zone_id}")
    return body if status == 200 else None

def poll_zone_until(zone_id: str, predicate, description: str, *, timeout_seconds: float = COMMAND_TIMEOUT_SECONDS) -> dict | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        zone = get_zone(zone_id)
        if zone is not None and predicate(zone): return zone
        time.sleep(POLL_INTERVAL_SECONDS)
    return None

def _parse_iso(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

def poll_events_until(zone_id: str, predicate, description: str, *, since: datetime | None = None, timeout_seconds: float = COMMAND_TIMEOUT_SECONDS) -> dict | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status, body = request("GET", f"/zones/{zone_id}/events?limit=200")
        if status == 200 and isinstance(body, list):
            candidates = [e for e in body if since is None or ("received_at" in e and _parse_iso(e["received_at"]) >= since)]
            match = next((e for e in candidates if predicate(e)), None)
            if match is not None: return match
        time.sleep(POLL_INTERVAL_SECONDS)
    return None

def ensure_plant(plant_id: str, species: str, home_zone_id: str) -> None:
    request("POST", "/plants", {"id": plant_id, "species": species, "home_zone_id": home_zone_id})

def ensure_quarantine(plant_id: str, quarantine_zone_id: str, reason: str, *, quarantined_at: datetime | None = None) -> None:
    payload = {"is_quarantined": True, "quarantine_zone_id": quarantine_zone_id, "reason": reason}
    if quarantined_at is not None: payload["quarantined_at"] = quarantined_at.isoformat()
    request("PATCH", f"/plants/{plant_id}/quarantine", payload)

def wait_for_edge_start() -> None:
    print("\n" + "=" * 78)
    print(f"Avvia ora l'Edge in un altro terminale con:\n  .\\edge\\build\\bin\\Debug\\edge.exe --backend-url {BASE_URL} --edge-id {EDGE_ID}")
    print("=" * 78)
    input("Premi INVIO qui quando è avviato... ")

def probe_edge_already_running(edge_zone_ids: list[str]) -> bool:
    print(f"\n[seed] verifico se un Edge è già raggiungibile ({EDGE_READY_PROBE_SECONDS:.0f}s)...")
    deadline = time.monotonic() + EDGE_READY_PROBE_SECONDS
    while time.monotonic() < deadline:
        for zone_id in edge_zone_ids:
            zone = get_zone(zone_id)
            if zone is not None and zone.get("lifecycle_state") == "Running":
                print(f"[seed] {zone_id} è già Running.")
                return True
        time.sleep(POLL_INTERVAL_SECONDS)
    return False

def narrate_wait(message: str, seconds: float) -> None:
    print(f"\n[seed] {message}")
    remaining = seconds
    while remaining > 0:
        tick = min(NARRATION_TICK_SECONDS, remaining)
        time.sleep(tick)
        remaining -= tick

def ensure_resident_plants(zone_species: dict[str, str], edge_zone_ids: list[str]) -> None:
    for zone_id in edge_zone_ids:
        if species := zone_species.get(zone_id):
            ensure_plant(f"resident-{zone_id}", species, zone_id)

def ensure_instant_quarantine_plants(zone_species: dict[str, str]) -> None:
    backdate_at = datetime.now(timezone.utc) - QUARANTINE_BACKDATE
    for plant_id, home_zone in QUARANTINE_INSTANT_PLANT_SOURCES:
        if species := zone_species.get(home_zone):
            ensure_plant(plant_id, species, home_zone)
            ensure_quarantine(plant_id, "r5-s1", "controllo fitosanitario", quarantined_at=backdate_at)

def advance_zone_to_last_phase(zone_id: str, recipe_id: str, run_suffix: str) -> None:
    status, recipe = request("GET", f"/recipes/{recipe_id}")
    if status != 200 or not isinstance(recipe, dict) or not recipe.get("phases"): return
    last_phase = recipe["phases"][-1]["name"]

    for step_index in range(len(recipe["phases"]) - 1):
        zone = get_zone(zone_id)
        if zone and (zone.get("current_phase") == last_phase or zone.get("cultivation_completed")): break
        if enqueue_and_wait_command(zone_id, f"advance-phase-{zone_id}-{step_index}-{run_suffix}", "AdvanceRecipePhase", {}) != "succeeded":
            break

def diagnose_actuator_snapshot(zone_id: str) -> None:
    status, snapshot = request("GET", f"/zones/{zone_id}/actuators/latest")
    if status == 200 and isinstance(snapshot, dict): print(f"[seed] {zone_id}: snapshot attuatori presente.")

def step5_degraded_demo(run_suffix: str) -> None:
    zone_id = DEGRADED_DEMO_ZONE_ID
    fault_id = f"demo-short-dropout-{zone_id}-{run_suffix}"
    since = datetime.now(timezone.utc)
    if not enqueue_command(zone_id, f"inject-{fault_id}", "InjectFault", {"fault_id": fault_id, "target_type": "sensor", "target": "soil_moisture", "mode": "sensor_dropout", "duration_seconds": SHORT_FAULT_DURATION_SECONDS}): return

    poll_events_until(zone_id, lambda e: e.get("event_type") == "FaultDetected" and e.get("payload", {}).get("rule") == "missing_value", "FaultDetected", since=since)
    poll_events_until(zone_id, lambda e: e.get("event_type") == "StateChanged" and e.get("payload", {}).get("current_state") == "Degraded", "StateChanged -> Degraded", since=since)

def step7_lockdown_demo(run_suffix: str) -> None:
    zone_id = LOCKDOWN_DEMO_ZONE_ID
    fault_id = f"demo-persistent-dropout-{zone_id}-{run_suffix}"
    since = datetime.now(timezone.utc)
    
    if not enqueue_command(zone_id, f"inject-{fault_id}", "InjectFault", {"fault_id": fault_id, "target_type": "sensor", "target": "soil_moisture", "mode": "sensor_dropout"}): return

    poll_events_until(zone_id, lambda e: e.get("event_type") == "StateChanged" and e.get("payload", {}).get("current_state") == "Degraded", "StateChanged -> Degraded", since=since)
    poll_events_until(zone_id, lambda e: e.get("event_type") == "StateChanged" and e.get("payload", {}).get("current_state") == "EmergencyLockdown", "StateChanged -> EmergencyLockdown", since=since)

    if enqueue_and_wait_command(zone_id, f"reset-fault-{fault_id}", "ResetFault", {"fault_id": fault_id}) == "succeeded":
        enqueue_and_wait_command(zone_id, f"reset-emergency-{zone_id}-{run_suffix}", "ResetEmergency", {})

    poll_events_until(zone_id, lambda e: e.get("event_type") == "StateChanged" and e.get("payload", {}).get("current_state") == "Degraded" and e.get("payload", {}).get("previous_state") == "EmergencyLockdown", "StateChanged -> Degraded", since=datetime.now(timezone.utc))
    poll_events_until(zone_id, lambda e: e.get("event_type") == "StateChanged" and e.get("payload", {}).get("current_state") == "Nominal" and e.get("payload", {}).get("previous_state") == "Degraded", "StateChanged -> Nominal", since=datetime.now(timezone.utc))

def step_safety_lockdown_demo(since: datetime) -> None:
    zone_id = LOCKDOWN_SAFETY_DEMO_ZONE_ID
    poll_events_until(zone_id, lambda e: e.get("event_type") == "FaultDetected" and e.get("payload", {}).get("rule") == "outside_recipe_safety_range", "FaultDetected", since=since)
    poll_events_until(zone_id, lambda e: e.get("event_type") == "StateChanged" and e.get("payload", {}).get("current_state") == "EmergencyLockdown", "StateChanged -> EmergencyLockdown", since=since)

def verify_state_sequence(zone_id: str, *, since: datetime, expected: list[tuple[str, str]]) -> bool:
    status, body = request("GET", f"/zones/{zone_id}/events?limit=500")
    if status != 200 or not isinstance(body, list): return False
    state_changes = sorted((e for e in body if e.get("event_type") == "StateChanged" and _parse_iso(e["received_at"]) >= since), key=lambda e: e["received_at"])
    observed = [(e["payload"].get("previous_state"), e["payload"].get("current_state")) for e in state_changes]
    return observed == expected

def main() -> None:
    connection = get_connection()
    try:
        init_db(connection)
        for u, p, r, d in SEED_ACCOUNTS: _upsert_user(connection, u, p, r, d)
    finally:
        connection.close()

    run_suffix = str(int(time.time() * 1000))
    print(f"[seed] BASE_URL={BASE_URL!r}\n[seed] --- Passo 0: login ---")
    login_as_admin()

    print("\n[seed] --- Passo 1: registrazione zone ---")
    by_department: dict[int, list[tuple]] = {}
    for entry in PRODUCTION_ZONES: by_department.setdefault(entry[2], []).append(entry)

    zone_recipe, zone_species, edge_zone_ids = {}, {}, []
    for department, entries in sorted(by_department.items()):
        recipes = pick_recipes(department, len(entries))
        for (zone_id, name, dept, sector, has_edge), recipe in zip(entries, recipes):
            zone_species[zone_id] = recipe["plant_type"]
            zone_recipe[zone_id] = recipe["id"]
            extra = {"plant_species": recipe["plant_type"], "active_recipe_id": recipe["id"]}
            if has_edge:
                extra["assigned_edge_id"] = EDGE_ID
                edge_zone_ids.append(zone_id)
            ensure_zone(zone_id, name, dept, sector, extra)

    safety_lockdown_plant_type = ensure_safety_lockdown_recipe()
    ensure_safety_lockdown_zone(safety_lockdown_plant_type)
    zone_species[LOCKDOWN_SAFETY_DEMO_ZONE_ID] = safety_lockdown_plant_type
    zone_recipe[LOCKDOWN_SAFETY_DEMO_ZONE_ID] = LOCKDOWN_SAFETY_DEMO_RECIPE_ID
    edge_zone_ids.append(LOCKDOWN_SAFETY_DEMO_ZONE_ID)
    safety_lockdown_since = datetime.now(timezone.utc)

    for zone_id, name, dept, sector in QUARANTINE_ZONES: ensure_zone(zone_id, name, dept, sector, {"plant_species": None})

    ensure_orphan_quarantine_scenario(zone_species["r3-s1"])
    ensure_resident_plants(zone_species, edge_zone_ids)
    ensure_instant_quarantine_plants(zone_species)

    print("\n[seed] --- Passo 2: ActivateCultivation e Iniezione Telemetria ---")
    for zone_id in edge_zone_ids:
        ensure_cultivation(zone_id, zone_recipe[zone_id])
        push_historical_telemetry(zone_id)

    print("\n[seed] --- Passo 8: quarantena ---")
    quarantine_backdate_at = datetime.now(timezone.utc) - QUARANTINE_BACKDATE
    plant_sources = [("plant-1", "r1-s1"), ("plant-2", "r1-s1"), ("plant-3", "r1-s2"), ("plant-4", "r2-s1"), ("plant-5", "r3-s1")]
    for plant_id, home_zone in plant_sources:
        ensure_plant(plant_id, zone_species[home_zone], home_zone)
        ensure_quarantine(plant_id, "r5-s1", "routine", quarantined_at=quarantine_backdate_at)

    if not probe_edge_already_running(edge_zone_ids): wait_for_edge_start()

    print("\n[seed] --- Passo 4: polling Running ---")
    running = {zone_id: poll_zone_until(zone_id, lambda z: z.get("lifecycle_state") == "Running", "Running") is not None for zone_id in edge_zone_ids}

    for zone_id in edge_zone_ids:
        if running.get(zone_id): enqueue_and_wait_command(zone_id, f"set-time-scale-{zone_id}-{run_suffix}", "SetSimulationSpeed", {"time_scale": DEV_TIME_SCALE})

    for zone_id in PHASE_ADVANCE_ZONE_IDS:
        if running.get(zone_id): advance_zone_to_last_phase(zone_id, zone_recipe[zone_id], run_suffix)

    if running.get(DEGRADED_DEMO_ZONE_ID): step5_degraded_demo(run_suffix)

    if running.get(LOCKDOWN_DEMO_ZONE_ID):
        step7_lockdown_demo(run_suffix)
        advance_zone_to_last_phase(LOCKDOWN_DEMO_ZONE_ID, zone_recipe[LOCKDOWN_DEMO_ZONE_ID], run_suffix)

    if running.get(LOCKDOWN_SAFETY_DEMO_ZONE_ID): step_safety_lockdown_demo(safety_lockdown_since)

    for zone_id in edge_zone_ids: diagnose_actuator_snapshot(zone_id)
    print("\n[seed] fatto!")

if __name__ == "__main__":
    main()