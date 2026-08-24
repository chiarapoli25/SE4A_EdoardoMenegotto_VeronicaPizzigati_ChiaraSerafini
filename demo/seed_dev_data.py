"""Popolamento test completo per la dashboard SmartHydro.

USO: con il backend già avviato (uvicorn backend.app.main:app --reload),
in un terzo terminale:

    python demo/seed_test_scenario.py

NON serve l'Edge C++ acceso: la telemetria viene inviata direttamente
all'API, rispettando lo stesso contratto che userebbe l'Edge, ma senza
build, firewall o attese di 15 minuti. Rilanciabile più volte: zone,
coltivazioni, piante e quarantene vengono create solo se mancano
(idempotenti in senso stretto). Telemetria, attuatori ed eventi vengono
invece reinviati ad ogni esecuzione con identificativi nuovi — basati
sull'orario di lancio dello script — così ogni rilancio aggiorna davvero
"adesso" nella dashboard invece di diventare un no-op silenzioso dopo la
prima corsa; non è comunque possibile fallire per un conflitto su questi
dati, perché gli identificativi non si ripetono mai fra due esecuzioni.

Topologia creata (adattata al vincolo reale di 4 reparti produttivi max,
2 settori per reparto compreso il quinto):
- Reparto 1 (Piante Tropicali e da Fogliame): 2 settori
    - r1-s1: online, stato Nominal, storico di telemetria realistico e un
      evento RecipePhaseChanged per testare l'avanzamento fase
    - r1-s2: online, stato Degraded (pH fuori dal safety_range della
      ricetta nell'ultimo campione, con FaultDetected + StateChanged)
- Reparto 2 (Piante da Fiore): 2 settori
    - r2-s1: online, stato Nominal
    - r2-s2: OFFLINE (un solo campione con timestamp vecchio, per testare
      un settore con dati residui invece di uno mai collegato)
- Reparto 3 (Piante Grasse e Succulente): 1 settore
    - r3-s1: online, Nominal
- Reparto 4 (Piante da Frutto e Ortaggi): 1 settore
    - r4-s1: online, Nominal
- Reparto 5 (Quarantena): 2 settori container + 5 piante quarantenate,
  con specie miste prese dai reparti produttivi sopra.

Setpoint, bande e Strategy non sono più valori fissi: vengono letti dalla
ricetta assegnata a ciascuna zona (GET /recipes, che restituisce già
l'oggetto Recipe completo con "phases" e "controllers"), così i dati
seedati sono sempre coerenti con qualunque ricetta il catalogo assegni.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "http://127.0.0.1:8000"
EDGE_ID = "edge-serra-1"

# --- Vincoli di dominio del backend SmartHydro -----------------------------
#
# Una Zone controlla esattamente queste 6 variabili; ognuna ha un canale
# sensore dedicato in GreenhouseTelemetry (vedi backend/app/features/
# telemetry/models.py). L'ordine non è significativo, ma resta stabile per
# leggibilità dei log e dei payload generati.
SENSOR_FIELD_BY_VARIABLE = {
    "soil_moisture": "soil_moisture_percent",
    "light": "light_ppfd_umol_m2_s",
    "ph": "ph",
    "nitrogen": "nitrogen_estimate_mg_per_liter",
    "phosphorus": "phosphorus_estimate_mg_per_liter",
    "potassium": "potassium_estimate_mg_per_liter",
}
CONTROLLED_VARIABLES = tuple(SENSOR_FIELD_BY_VARIABLE)

# N/P/K sono stimati dal modello di bilancio di massa dell'Edge
# (nitrogen_model/phosphorus_model/potassium_model), non da un sensore
# fisico: per questo possono usare solo la Strategy Predictive, mai
# Threshold o PID, indipendentemente da cosa dichiari la ricetta.
NUTRIENT_VARIABLES = frozenset({"nitrogen", "phosphorus", "potassium"})

# Limiti assoluti dei campi telemetria corrispondenti (vedi
# GreenhouseTelemetry): usati solo per non sforare la validazione Pydantic
# quando si genera deliberatamente un valore fuori dal safety_range della
# ricetta. N/P/K non hanno un tetto nel modello (solo ge=0.0): il valore
# qui sotto è un semplice paracadute, mai realmente raggiunto.
ABSOLUTE_SENSOR_BOUNDS = {
    "soil_moisture": (0.0, 100.0),
    "light": (0.0, 3000.0),
    "ph": (0.0, 14.0),
    "nitrogen": (0.0, 1_000_000.0),
    "phosphorus": (0.0, 1_000_000.0),
    "potassium": (0.0, 1_000_000.0),
}

HISTORY_SAMPLES = 7
HISTORY_STEP_MINUTES = 15

# Zona Nominal su cui testare la visualizzazione di un cambio fase.
PHASE_CHANGE_ZONE_ID = "r1-s1"

# id, nome, department_number, sector_number, ruolo ("nominal"/"degraded"/"offline")
PRODUCTION_ZONES = [
    ("r1-s1", "Reparto 1 - Settore 1", 1, 1, "nominal"),
    ("r1-s2", "Reparto 1 - Settore 2", 1, 2, "degraded"),
    ("r2-s1", "Reparto 2 - Settore 1", 2, 1, "nominal"),
    ("r2-s2", "Reparto 2 - Settore 2", 2, 2, "offline"),
    ("r3-s1", "Reparto 3 - Settore 1", 3, 1, "nominal"),
    ("r4-s1", "Reparto 4 - Settore 1", 4, 1, "nominal"),
]
QUARANTINE_ZONES = [
    ("r5-s1", "Quarantena - Settore 1", 5, 1),
    ("r5-s2", "Quarantena - Settore 2", 5, 2),
]


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, (json.loads(body) if body else {})
    except urllib.error.URLError as error:
        raise SystemExit(
            f"[seed] impossibile raggiungere {BASE_URL} — il backend è acceso? ({error})"
        ) from error


def pick_recipes(department_number: int, how_many: int) -> list[dict]:
    """Recupera dal catalogo `how_many` ricette distinte per il reparto.

    GET /recipes restituisce già l'oggetto Recipe completo (fasi e
    controller inclusi): non serve una seconda chiamata per zona.
    """
    status, body = request("GET", f"/recipes?department_number={department_number}")
    if status != 200 or not isinstance(body, list):
        raise SystemExit(
            f"[seed] impossibile leggere il catalogo per il reparto {department_number}"
        )
    candidates = [r for r in body if r.get("department_number") == department_number]
    if len(candidates) < how_many:
        raise SystemExit(
            f"[seed] servono {how_many} ricette per il reparto {department_number}, "
            f"trovate solo {len(candidates)}"
        )
    return candidates[:how_many]


def ensure_zone(zone_id: str, name: str, department: int, sector: int, payload_extra: dict) -> None:
    payload = {
        "id": zone_id,
        "name": name,
        "department_number": department,
        "sector_number": sector,
        **payload_extra,
    }
    status, body = request("POST", "/zones", payload)
    if status == 201:
        print(f"[seed] creata {zone_id}")
        return
    if status == 409:
        update = {k: v for k, v in payload_extra.items() if k != "plant_species"}
        status, body = request("PATCH", f"/zones/{zone_id}", update)
        print(f"[seed] {zone_id} già esistente, aggiornata (status {status})")
        return
    raise SystemExit(f"[seed] errore creando {zone_id}: {status} {body}")


def ensure_cultivation(zone_id: str, recipe_id: str) -> None:
    status, body = request(
        "POST", "/cultivations", {"zone_id": zone_id, "recipe_id": recipe_id}
    )
    if status in (201, 409):
        return
    print(f"[seed] avviso: coltivazione non creata su {zone_id} (status {status}) {body}")


# --- Ricetta -> Strategy/setpoint/fase reali --------------------------------


def phase_targets(phase: dict) -> dict[str, dict]:
    """Indicizza i target di una fase per variabile controllata."""
    return {target["variable"]: target for target in phase["targets"]}


def strategies_from_recipe(recipe: dict) -> dict[str, str]:
    """Legge la Strategy corrente di ogni variabile dai controller della ricetta.

    Usa `selected_strategy` quando presente, altrimenti `default_strategy`.
    N/P/K vengono comunque forzate a Predictive: è un vincolo di dominio
    indipendente da cosa dichiari la singola ricetta (vedi
    NUTRIENT_VARIABLES sopra).
    """
    strategies: dict[str, str] = {}
    for controller in recipe["controllers"]:
        variable = controller["variable"]
        strategies[variable] = controller.get("selected_strategy") or controller["default_strategy"]
    for nutrient in NUTRIENT_VARIABLES:
        strategies[nutrient] = "Predictive"
    return strategies


def setpoints_from_phase(phase: dict) -> dict[str, float]:
    """Costruisce current_setpoints dai target reali della fase."""
    return {variable: target["setpoint"] for variable, target in phase_targets(phase).items()}


# --- Generazione di valori sensore plausibili -------------------------------


def in_band_walk(minimum: float, maximum: float, count: int, rng: random.Random) -> list[float]:
    """Genera `count` valori dentro [minimum, maximum] con un piccolo
    random walk invece di punti indipendenti, così un grafico "andamento"
    disegna una linea plausibile invece di rumore bianco."""
    span = maximum - minimum
    step = span * 0.08
    value = rng.uniform(minimum + span * 0.35, maximum - span * 0.35)
    values = []
    for _ in range(count):
        value += rng.uniform(-step, step)
        value = min(max(value, minimum), maximum)
        values.append(value)
    return values


def out_of_safety_value(target: dict, variable: str, rng: random.Random) -> float:
    """Sceglie un valore deliberatamente fuori da target['safety_range'],
    non solo da allowed_range, restando dentro i limiti assoluti del canale
    telemetria corrispondente."""
    absolute_min, absolute_max = ABSOLUTE_SENSOR_BOUNDS[variable]
    safety = target["safety_range"]
    margin = max((safety["maximum"] - safety["minimum"]) * 0.08, 0.05)
    margin *= rng.uniform(0.8, 1.4)
    below = safety["minimum"] - margin
    if below >= absolute_min:
        return round(max(below, absolute_min), 3)
    above = safety["maximum"] + margin
    return round(min(above, absolute_max), 3)


def build_variable_series(
    phase: dict, count: int, rng: random.Random, *, fault_variable: str | None = None
) -> dict[str, list[float]]:
    """Serie di `count` letture per ciascuna delle 6 variabili, dentro
    l'allowed_range della fase. Se `fault_variable` è impostata, il suo
    ultimo campione viene sostituito con un valore fuori dal safety_range
    (il "malfunzionamento" appena accaduto)."""
    targets = phase_targets(phase)
    series = {
        variable: in_band_walk(
            targets[variable]["allowed_range"]["minimum"],
            targets[variable]["allowed_range"]["maximum"],
            count,
            rng,
        )
        for variable in CONTROLLED_VARIABLES
    }
    if fault_variable is not None:
        series[fault_variable][-1] = out_of_safety_value(targets[fault_variable], fault_variable, rng)
    return series


def ambient_extras(rng: random.Random) -> dict:
    """Canali telemetria informativi che non corrispondono a nessuna delle
    6 variabili controllate (nessun target di ricetta a cui ancorarli)."""
    return {
        "temperature_c": round(rng.uniform(20.0, 26.0), 1),
        "air_humidity_percent": round(rng.uniform(55.0, 75.0), 1),
        "soil_bulk_ec_ms_cm": round(rng.uniform(1.0, 2.2), 2),
        "soil_ec_ms_cm": round(rng.uniform(1.2, 2.4), 2),
        "fertilizer_concentration_mg_per_liter": round(rng.uniform(400.0, 900.0), 1),
    }


# --- Telemetria --------------------------------------------------------------


def telemetry_payload(
    recipe: dict,
    phase: dict,
    strategies: dict[str, str],
    setpoints: dict[str, float],
    sensor_readings: dict,
    *,
    operational_state: str,
    lifecycle_state: str,
    recorded_at: datetime,
    timestamp_seconds: float,
    sequence_number: int,
) -> dict:
    return {
        "boot_id": "seed",
        "sequence_number": sequence_number,
        "timestamp_seconds": timestamp_seconds,
        "recorded_at": recorded_at.isoformat(),
        "active_recipe_id": recipe["id"],
        "active_recipe_version": recipe["version"],
        "current_phase": phase["name"],
        "operational_state": operational_state,
        "lifecycle_state": lifecycle_state,
        "current_strategies": strategies,
        "current_setpoints": setpoints,
        "time_scale": 1.0,
        **sensor_readings,
    }


def post_telemetry(zone_id: str, payload: dict) -> bool:
    status, body = request("POST", f"/zones/{zone_id}/telemetry", payload)
    if status not in (200, 201):
        print(
            f"[seed] avviso: telemetria non salvata su {zone_id} "
            f"seq {payload['sequence_number']} (status {status}) {body}"
        )
        return False
    return True


# --- Attuatori -----------------------------------------------------------


def _in_photoperiod(hour_of_day: float, start_hour: float, duration_hours: float) -> bool:
    hour_of_day = hour_of_day % 24.0
    end_hour = start_hour + duration_hours
    if end_hour <= 24.0:
        return start_hour <= hour_of_day < end_hour
    return hour_of_day >= start_hour or hour_of_day < (end_hour - 24.0)


def photoperiod_lighting_percent(phase: dict, hour_of_day: float) -> float:
    photoperiod = phase["photoperiod"]
    in_window = _in_photoperiod(hour_of_day, photoperiod["start_hour"], photoperiod["duration_hours"])
    return 88.0 if in_window else 4.0


def build_actuator_command_and_output(
    readings: dict[str, float], setpoints: dict[str, float], phase: dict, timestamp_seconds: float
) -> tuple[dict, dict]:
    """Comando e uscita fisica coerenti con le letture del campione: pompa e
    valvole si aprono quando la variabile relativa è sotto il proprio
    setpoint, l'illuminazione segue il fotoperiodo della fase."""
    hour_of_day = (timestamp_seconds / 3600.0) % 24.0
    lighting_percent = photoperiod_lighting_percent(phase, hour_of_day)
    pump_on = readings["soil_moisture"] < setpoints["soil_moisture"]
    ph_tolerance = 0.05
    valves_open = {
        "nitrogen": readings["nitrogen"] < setpoints["nitrogen"],
        "phosphorus": readings["phosphorus"] < setpoints["phosphorus"],
        "potassium": readings["potassium"] < setpoints["potassium"],
        "ph_up": readings["ph"] < setpoints["ph"] - ph_tolerance,
        "ph_down": readings["ph"] > setpoints["ph"] + ph_tolerance,
    }
    command = {
        "requested_irrigation_volume_liters": 0.6 if pump_on else 0.0,
        "fertilizer_valves_open": valves_open,
        "lighting_percent": lighting_percent,
    }
    output = {
        "water_pump_on": pump_on,
        "water_pump_flow_liters_per_hour": 14.0 if pump_on else 0.0,
        "irrigation_volume_liters_last_step": 0.08 if pump_on else 0.0,
        "water_pump_on_time_seconds_last_step": 25.0 if pump_on else 0.0,
        "remaining_irrigation_volume_liters": 0.12,
        "fertilizer_valves_open": valves_open,
        "fertilizer_flow_milliliters_per_hour": {
            name: (4.5 if open_ else 0.0) for name, open_ in valves_open.items()
        },
        "fertilizer_volume_milliliters_last_step": {
            name: (0.35 if open_ else 0.0) for name, open_ in valves_open.items()
        },
        "lighting_power_watts": round(lighting_percent * 1.8, 1),
    }
    return command, output


def post_actuators(
    zone_id: str, sequence_number: int, timestamp_seconds: float, recorded_at: datetime, command: dict, output: dict
) -> None:
    payload = {
        "sequence_number": sequence_number,
        "boot_id": "seed",
        "timestamp_seconds": timestamp_seconds,
        "recorded_at": recorded_at.isoformat(),
        "command": command,
        "output": output,
    }
    status, body = request("POST", f"/zones/{zone_id}/actuators", payload)
    if status not in (200, 201):
        print(
            f"[seed] avviso: attuatori non salvati su {zone_id} "
            f"seq {sequence_number} (status {status}) {body}"
        )


# --- Eventi ------------------------------------------------------------------


def post_event(
    zone_id: str, event_id: str, event_type: str, payload: dict, recorded_at: datetime, timestamp_seconds: float
) -> None:
    body_payload = {
        "event_id": event_id,
        "edge_id": EDGE_ID,
        "boot_id": "seed",
        "event_type": event_type,
        "timestamp_seconds": timestamp_seconds,
        "recorded_at": recorded_at.isoformat(),
        "payload": payload,
    }
    status, body = request("POST", f"/zones/{zone_id}/events", body_payload)
    if status not in (200, 201):
        print(f"[seed] avviso: evento {event_type} non salvato su {zone_id} (status {status}) {body}")
    else:
        print(f"[seed] evento {event_type} registrato su {zone_id}")


# --- Storico telemetria + attuatori per una zona online ---------------------


def seed_history(
    zone_id: str,
    recipe: dict,
    phase: dict,
    strategies: dict[str, str],
    setpoints: dict[str, float],
    *,
    fault_variable: str | None,
    base_sequence: int,
    rng: random.Random,
) -> tuple[dict[str, float], datetime, float]:
    """Invia HISTORY_SAMPLES campioni di telemetria (con attuatori
    corrispondenti), distanziati di HISTORY_STEP_MINUTES, dal più vecchio al
    più recente ("adesso"). Restituisce le letture, l'orario e il
    timestamp simulato dell'ultimo campione, per costruire eventuali eventi
    narrativi coerenti con l'ultimo dato inviato."""
    series = build_variable_series(phase, HISTORY_SAMPLES, rng, fault_variable=fault_variable)
    now = datetime.now(timezone.utc)

    last_readings: dict[str, float] = {}
    last_recorded_at = now
    last_timestamp_seconds = 0.0

    for i in range(HISTORY_SAMPLES):
        offset_minutes = (HISTORY_SAMPLES - 1 - i) * HISTORY_STEP_MINUTES
        recorded_at = now - timedelta(minutes=offset_minutes)
        timestamp_seconds = (i + 1) * HISTORY_STEP_MINUTES * 60
        sequence_number = base_sequence + i

        readings = {variable: round(series[variable][i], 2) for variable in CONTROLLED_VARIABLES}
        sensor_readings = {SENSOR_FIELD_BY_VARIABLE[v]: value for v, value in readings.items()}
        sensor_readings.update(ambient_extras(rng))

        is_last_sample = i == HISTORY_SAMPLES - 1
        operational_state = "Degraded" if (fault_variable and is_last_sample) else "Nominal"

        payload = telemetry_payload(
            recipe, phase, strategies, setpoints, sensor_readings,
            operational_state=operational_state, lifecycle_state="Running",
            recorded_at=recorded_at, timestamp_seconds=timestamp_seconds,
            sequence_number=sequence_number,
        )
        post_telemetry(zone_id, payload)

        command, output = build_actuator_command_and_output(readings, setpoints, phase, timestamp_seconds)
        post_actuators(zone_id, sequence_number, timestamp_seconds, recorded_at, command, output)

        last_readings, last_recorded_at, last_timestamp_seconds = readings, recorded_at, timestamp_seconds

    print(f"[seed] {HISTORY_SAMPLES} campioni di telemetria e attuatori inviati a {zone_id}")
    return last_readings, last_recorded_at, last_timestamp_seconds


# --- Piante e quarantena (invariati) -----------------------------------------


def ensure_plant(plant_id: str, species: str, home_zone_id: str) -> None:
    status, body = request(
        "POST",
        "/plants",
        {"id": plant_id, "species": species, "home_zone_id": home_zone_id},
    )
    if status not in (201, 409):
        print(f"[seed] avviso: pianta {plant_id} non creata (status {status}) {body}")


def ensure_quarantine(plant_id: str, quarantine_zone_id: str, reason: str) -> None:
    status, body = request(
        "PATCH",
        f"/plants/{plant_id}/quarantine",
        {
            "is_quarantined": True,
            "quarantine_zone_id": quarantine_zone_id,
            "reason": reason,
        },
    )
    if status != 200:
        print(f"[seed] avviso: quarantena non applicata a {plant_id} (status {status}) {body}")
    else:
        print(f"[seed] {plant_id} spostata in quarantena")


def main() -> None:
    rng = random.Random()
    # Base univoca per questa esecuzione: sequence_number ed event_id non
    # collidono mai con una corsa precedente, quindi telemetria/attuatori/
    # eventi si accumulano come dati "freschi" ad ogni rilancio invece di
    # fallire o restare fermi al primo run. Precisione al millisecondo (non
    # al secondo): ogni run usa HISTORY_SAMPLES valori consecutivi a partire
    # da questa base, quindi due rilanci nello stesso secondo produrrebbero
    # comunque range sovrapposti con una precisione di soli interi-secondo.
    base_sequence = int(time.time() * 1000)

    by_department: dict[int, list[tuple]] = {}
    for entry in PRODUCTION_ZONES:
        by_department.setdefault(entry[2], []).append(entry)

    zone_species: dict[str, str] = {}
    summary: list[tuple[str, str, str]] = []

    for department, entries in sorted(by_department.items()):
        recipes = pick_recipes(department, len(entries))
        for (zone_id, name, dept, sector, role), recipe in zip(entries, recipes):
            species = recipe["plant_type"]
            zone_species[zone_id] = species
            ensure_zone(
                zone_id,
                name,
                dept,
                sector,
                {
                    "plant_species": species,
                    "assigned_edge_id": EDGE_ID,
                    "active_recipe_id": recipe["id"],
                },
            )
            ensure_cultivation(zone_id, recipe["id"])

            phase = recipe["phases"][0]
            strategies = strategies_from_recipe(recipe)
            setpoints = setpoints_from_phase(phase)

            if role == "offline":
                stale_at = datetime.now(timezone.utc) - timedelta(hours=2)
                series = build_variable_series(phase, 1, rng)
                readings = {variable: round(series[variable][0], 2) for variable in CONTROLLED_VARIABLES}
                sensor_readings = {SENSOR_FIELD_BY_VARIABLE[v]: value for v, value in readings.items()}
                sensor_readings.update(ambient_extras(rng))
                payload = telemetry_payload(
                    recipe, phase, strategies, setpoints, sensor_readings,
                    operational_state="Nominal", lifecycle_state="Running",
                    recorded_at=stale_at, timestamp_seconds=900,
                    sequence_number=base_sequence,
                )
                post_telemetry(zone_id, payload)
                summary.append((zone_id, role, "1 campione con timestamp vecchio (dati residui)"))
                continue

            degraded = role == "degraded"
            fault_variable = "ph" if degraded else None
            last_readings, last_recorded_at, last_timestamp_seconds = seed_history(
                zone_id, recipe, phase, strategies, setpoints,
                fault_variable=fault_variable, base_sequence=base_sequence, rng=rng,
            )

            if degraded:
                fault_at = last_recorded_at + timedelta(seconds=30)
                state_at = last_recorded_at + timedelta(seconds=45)
                post_event(
                    zone_id, f"fault-{zone_id}-{base_sequence}", "FaultDetected",
                    {
                        "target": fault_variable,
                        "detail": (
                            f"{fault_variable} a {last_readings[fault_variable]:.2f} fuori dal "
                            f"safety_range della ricetta {recipe['id']}"
                        ),
                    },
                    fault_at, last_timestamp_seconds + 30,
                )
                post_event(
                    zone_id, f"statechange-{zone_id}-{base_sequence}", "StateChanged",
                    {"previous_state": "Nominal", "current_state": "Degraded"},
                    state_at, last_timestamp_seconds + 45,
                )
                summary.append((
                    zone_id, role,
                    f"{HISTORY_SAMPLES} campioni, {fault_variable} fuori banda nell'ultimo, "
                    "FaultDetected + StateChanged",
                ))
            elif zone_id == PHASE_CHANGE_ZONE_ID and len(recipe["phases"]) > 1:
                next_phase = recipe["phases"][1]
                phase_at = last_recorded_at + timedelta(seconds=30)
                post_event(
                    zone_id, f"phasechange-{zone_id}-{base_sequence}", "RecipePhaseChanged",
                    {"previous_phase": phase["name"], "current_phase": next_phase["name"]},
                    phase_at, last_timestamp_seconds + 30,
                )
                summary.append((
                    zone_id, role,
                    f"{HISTORY_SAMPLES} campioni, RecipePhaseChanged verso '{next_phase['name']}'",
                ))
            else:
                summary.append((zone_id, role, f"{HISTORY_SAMPLES} campioni di telemetria e attuatori"))

    for zone_id, name, dept, sector in QUARANTINE_ZONES:
        ensure_zone(zone_id, name, dept, sector, {"plant_species": None})

    plant_sources = [
        ("plant-1", zone_species["r1-s1"], "r1-s1"),
        ("plant-2", zone_species["r1-s1"], "r1-s1"),
        ("plant-3", zone_species["r1-s2"], "r1-s2"),
        ("plant-4", zone_species["r2-s1"], "r2-s1"),
        ("plant-5", zone_species["r3-s1"], "r3-s1"),
    ]
    for plant_id, species, home_zone in plant_sources:
        ensure_plant(plant_id, species, home_zone)
        ensure_quarantine(plant_id, "r5-s1", "controllo fitosanitario di routine")

    print("\n[seed] riepilogo per zona:")
    for zone_id, role, detail in summary:
        print(f"  - {zone_id} ({role}): {detail}")
    print(
        "\n[seed] fatto. Aggiorna la dashboard: dovresti vedere 6 settori produttivi "
        "(uno Degraded con un allarme reale, uno offline con dati residui) e 5 piante in quarantena."
    )


if __name__ == "__main__":
    main()
