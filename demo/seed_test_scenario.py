# Test RAPIDO — nessun Edge richiesto, POST diretti via API.
# Usa questo per sviluppo quotidiano e demo veloci.
# Per una verifica rigorosa del sistema reale (Edge C++, FSM, guasti),
# usa invece demo/seed_dev_data.py.

"""Test RAPIDO — nessun Edge richiesto, POST dirette via API.
Usa questo per sviluppo quotidiano e demo veloci.
Per una verifica rigorosa del sistema reale (Edge C++, FSM, guasti),
usa invece demo/seed_dev_data.py.

USO: con il backend già avviato (uvicorn backend.app.main:app --reload):

    python demo/seed_test_scenario.py

Lo script TERMINA da solo (nessun input() manuale, a differenza di
seed_dev_data.py che aspetta l'avvio a mano dell'Edge reale) ed è pensato
per essere richiamato anche da uno script di avvio automatico come
avvia_demo.bat.

COSA FA (a differenza di seed_dev_data.py):
- Nessun Edge C++ viene mai avviato o atteso. Ogni cosa che nella realtà
  arriverebbe dall'Edge (telemetria, lifecycle_state, current_phase,
  current_strategies, current_setpoints) viene invece POSTata direttamente
  su /zones/{id}/telemetry, con valori plausibili derivati dalla ricetta
  assegnata alla zona — non è una simulazione fisica, è un istantanea
  "come se" un Edge l'avesse appena riportata.
- current_strategies della telemetria è preso PARI PARI da
  recipe["controllers"][*]["selected_strategy"]: rispecchia esattamente lo
  stato di un Edge reale che ha appena adottato quella ricetta con
  l'auto-conferma introdotta in edge/src/control/control_system.cpp
  (RecipeControlSystem::confirm_all_from_recipe — ogni Strategy è
  confermata e utilizzabile immediatamente, senza un ConfirmConfiguration
  separato). Non c'è quindi alcuna differenza col comportamento reale da
  replicare qui: il valore che scriviamo è letteralmente quello che un
  Edge reale scriverebbe in current_strategies subito dopo l'adozione.
- Una sola POST di telemetria per zona: lo stato "online" (calcolato da
  last_edge_contact, vedi backend/app/core/config.py
  offline_threshold_seconds, default 60s) dura solo quella finestra da
  quando lo script è stato eseguito, poi la zona torna "offline" come
  farebbe una zona che ha davvero smesso di essere raggiunta. Per
  rinfrescarla basta rilanciare lo script.
- r2-s2 viene creata e le viene comunque attivata una coltivazione (stessa
  ricetta/specie delle altre), ma non riceve MAI una POST di telemetria:
  resta "offline" di proposito, così la dashboard mostra anche un esempio
  reale di zona offline invece di soli settori verdi. È la stessa idea di
  seed_dev_data.py (lì offline per assenza di assigned_edge_id), qui
  ottenuta semplicemente non simulando mai il suo Edge.
- Non vengono POSTati snapshot degli attuatori: il pannello "Controllo
  avanzato" di ogni zona seedata da questo script mostrerà quindi "—" per
  gli attuatori finché non arriva una lettura reale (o dello script
  rigoroso). Non serve per popolare rapidamente la vista di riepilogo, le
  strategie e le piante in quarantena, che sono l'obiettivo di questo
  script.

VINCOLO RISPETTATO: solo chiamate HTTP dirette (POST/GET/PATCH via
urllib). Nessuna dipendenza dall'Edge C++, nessun processo esterno
avviato da questo script.

AUTENTICAZIONE: come primo passo lo script fa login su POST /auth/login con
l'account amministratore di esempio (vedi demo/seed_users.py, che va
eseguito almeno una volta prima di questo script) e allega il token
ottenuto a ogni chiamata successiva. Nessuno dei comandi accodati qui è
oggi ChangeStrategy/ConfirmConfiguration (l'unico gate protetto da ruolo,
vedi backend/app/features/commands/routes.py), ma restare autenticati
allo stesso modo evita rotture silenziose se lo scenario dovesse
cambiare in futuro.

Topologia (stessa forma di seed_dev_data.py, per coerenza fra i due
script):
- Reparto 1: r1-s1, r1-s2 (entrambe online, ricette distinte)
- Reparto 2: r2-s1 (online), r2-s2 (creata e attivata, ma volutamente MAI
  online: nessuna telemetria le viene mai inviata)
- Reparto 3: r3-s1 (online)
- Reparto 4: r4-s1 (online)
- Reparto 5: r5-s1 (unico settore possibile per la quarantena) + 5 piante,
  tutte spostate in quarantena in r5-s1
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Sibling module, non il pacchetto backend: Python mette la cartella dello
# script (demo/) in sys.path[0] quando lo lanci direttamente, quindi questo
# import funziona sia da `python demo/seed_test_scenario.py` (dalla radice)
# sia da dentro demo/, senza bisogno di manipolare sys.path.
from seed_users import ADMIN_PASSWORD, ADMIN_USERNAME

BASE_URL = "http://127.0.0.1:8000"

# Token di sessione ottenuto da login_as_admin() e allegato da request() a
# ogni chiamata di scrittura (POST/PATCH/DELETE). Nessun comando emesso da
# questo script tocca oggi ChangeStrategy/ConfirmConfiguration, ma il token
# viene comunque allegato ovunque: e' innocuo verso gli endpoint che non lo
# richiedono, e mette lo script al riparo se in futuro dovesse emetterne uno.
_AUTH_TOKEN: str | None = None

# id, nome, department_number, sector_number, riceve mai telemetria da
# questo script (vedi nota su r2-s2 in cima al file)
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
    # Il reparto 5 ha un solo settore fisico, sempre sector_number=1: il
    # backend ora lo impone esplicitamente (400 su qualunque altro valore),
    # quindi non esiste piu' un "r5-s2" da seedare qui.
]

# Valori di sensore plausibili per i campi non coperti dalle sei variabili
# controllate (che invece leggiamo dai target di fase della ricetta):
# servono solo a produrre un GreenhouseTelemetry valido e ragionevole, non
# rappresentano una simulazione fisica.
PLACEHOLDER_TEMPERATURE_C = 23.5
PLACEHOLDER_AIR_HUMIDITY_PERCENT = 58.0
PLACEHOLDER_SOIL_BULK_EC_MS_CM = 0.7
PLACEHOLDER_SOIL_EC_MS_CM = 1.6
PLACEHOLDER_FERTILIZER_CONCENTRATION_MG_PER_LITER = 380.0


def _parse_json_body(text: str) -> dict:
    """Interpreta il corpo di una risposta HTTP come JSON, senza mai
    sollevare un'eccezione non gestita se non lo e'.

    @details Gli endpoint che usiamo rispondono sempre con JSON quando tutto
    va secondo i piani, ma un errore lato server non gestito esplicitamente
    (es. un 500 dovuto a un'eccezione imprevista nel backend, come un errore
    del database) fa rispondere FastAPI con un semplice "Internal Server
    Error" in testo semplice, non JSON. Senza questa guardia, il vecchio
    `json.loads(body)` sollevava un JSONDecodeError non catturato QUI DENTRO
    request(), che si propagava fino a un traceback Python grezzo invece del
    messaggio esplicito che login_as_admin()/ensure_cultivation()/
    post_fake_telemetria() eccetera sono pensati per mostrare — mascherando
    la vera causa dell'errore in mezzo a righe di stack trace facili da
    perdere, specialmente se lo script è invocato da un lanciatore come
    avvia_demo.bat."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"detail": text.strip() or "(risposta vuota, non JSON)"}


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if _AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {_AUTH_TOKEN}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode("utf-8")
            return response.status, _parse_json_body(body)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, _parse_json_body(body)
    except urllib.error.URLError as error:
        raise SystemExit(
            f"[seed] impossibile raggiungere {BASE_URL} — il backend è acceso? ({error})"
        ) from error


def login_as_admin() -> None:
    """Autentica lo script come l'account amministratore di seed e salva il
    token in _AUTH_TOKEN, cosi' request() lo allega da qui in poi. Richiede
    che demo/seed_users.py sia gia' stato eseguito almeno una volta contro
    lo stesso database del backend."""
    global _AUTH_TOKEN
    status, body = request(
        "POST", "/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    if status == 401:
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): credenziali rifiutate (401) {body}. Hai gia' "
            "eseguito `python demo/seed_users.py` contro questo stesso database?"
        )
    if status != 200:
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): il backend ha risposto con un errore "
            f"inatteso (status {status}) {body}. Non sembra un problema di "
            "credenziali: puo' essere un errore lato server (controlla il log "
            "del backend, es. un 'disk I/O error' o un altro problema di "
            "accesso al database)."
        )
    _AUTH_TOKEN = body["token"]
    print(f"[seed] autenticato come {ADMIN_USERNAME!r} (ruolo={body['user']['role']})")


def pick_recipes(department_number: int, how_many: int) -> list[dict]:
    """Recupera dal catalogo `how_many` ricette distinte per il reparto."""
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


def ensure_cultivation(zone_id: str, recipe_id: str) -> dict | None:
    """POST /cultivations crea E accoda già da sola il comando
    ActivateCultivation (vedi backend/app/features/cultivations/repository.py
    create_and_activate): imposta active_cultivation_id/active_recipe_id
    sulla zona, puro input legittimo, nessun valore calcolato a mano.
    Restituisce la Cultivation creata (per leggerne recipe_version), o None
    se la zona aveva già un ciclo attivo (409, tollerato: rerun idempotente)."""
    status, body = request(
        "POST", "/cultivations", {"zone_id": zone_id, "recipe_id": recipe_id}
    )
    if status == 201:
        return body["cultivation"]
    if status == 409:
        print(f"[seed] {zone_id}: coltivazione già attiva, la lascio com'è")
        return None
    raise SystemExit(f"[seed] errore attivando la coltivazione su {zone_id}: {status} {body}")


def post_fake_telemetry(zone_id: str, recipe: dict, recipe_version: int, boot_id: str) -> None:
    """Simula in un colpo solo quello che un Edge reale riporterebbe subito
    dopo aver adottato `recipe`: prima fase, Strategy auto-confermate
    (identiche a recipe['controllers'][*]['selected_strategy'], come fa
    davvero l'Edge da RecipeControlSystem::confirm_all_from_recipe — nessun
    ConfirmConfiguration separato), setpoint della prima fase. Nessun dato
    fisico viene simulato oltre questo singolo istante."""
    phase = recipe["phases"][0]
    setpoint_by_variable = {t["variable"]: t["setpoint"] for t in phase["targets"]}
    strategy_by_variable = {c["variable"]: c["selected_strategy"] for c in recipe["controllers"]}

    telemetry = {
        "sequence_number": 1,
        "boot_id": boot_id,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timestamp_seconds": 1.0,
        "temperature_c": PLACEHOLDER_TEMPERATURE_C,
        "air_humidity_percent": PLACEHOLDER_AIR_HUMIDITY_PERCENT,
        "soil_moisture_percent": setpoint_by_variable["soil_moisture"],
        "soil_bulk_ec_ms_cm": PLACEHOLDER_SOIL_BULK_EC_MS_CM,
        "soil_ec_ms_cm": PLACEHOLDER_SOIL_EC_MS_CM,
        "fertilizer_concentration_mg_per_liter": PLACEHOLDER_FERTILIZER_CONCENTRATION_MG_PER_LITER,
        "nitrogen_estimate_mg_per_liter": setpoint_by_variable["nitrogen"],
        "phosphorus_estimate_mg_per_liter": setpoint_by_variable["phosphorus"],
        "potassium_estimate_mg_per_liter": setpoint_by_variable["potassium"],
        "ph": setpoint_by_variable["ph"],
        "light_ppfd_umol_m2_s": setpoint_by_variable["light"],
        "active_recipe_id": recipe["id"],
        "active_recipe_version": recipe_version,
        "current_phase": phase["name"],
        "operational_state": "Nominal",
        "lifecycle_state": "Running",
        "current_strategies": strategy_by_variable,
        "current_setpoints": setpoint_by_variable,
        "time_scale": 1.0,
    }
    status, body = request("POST", f"/zones/{zone_id}/telemetry", telemetry)
    if status != 201:
        raise SystemExit(f"[seed] errore inviando telemetria a {zone_id}: {status} {body}")
    print(
        f"[seed] telemetria inviata a {zone_id}: fase {phase['name']!r}, "
        f"strategie {strategy_by_variable}"
    )


def ensure_plant(plant_id: str, species: str, home_zone_id: str) -> None:
    status, body = request(
        "POST",
        "/plants",
        {"id": plant_id, "species": species, "home_zone_id": home_zone_id},
    )
    if status not in (201, 409):
        raise SystemExit(f"[seed] errore creando la pianta {plant_id}: {status} {body}")


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
        raise SystemExit(f"[seed] errore mettendo in quarantena {plant_id}: {status} {body}")
    print(f"[seed] {plant_id} spostata in quarantena")


def main() -> None:
    run_suffix = str(int(time.time() * 1000))
    boot_id = f"seed-test-{run_suffix}"

    print("[seed] --- Passo 0: login come amministratore di seed ---")
    login_as_admin()

    print("\n[seed] --- Passo 1: registrazione zone (puro input) ---")
    by_department: dict[int, list[tuple]] = {}
    for entry in PRODUCTION_ZONES:
        by_department.setdefault(entry[2], []).append(entry)

    zone_recipe: dict[str, dict] = {}
    zone_species: dict[str, str] = {}

    for department, entries in sorted(by_department.items()):
        recipes = pick_recipes(department, len(entries))
        for (zone_id, name, dept, sector, _receives_telemetry), recipe in zip(entries, recipes):
            species = recipe["plant_type"]
            zone_species[zone_id] = species
            zone_recipe[zone_id] = recipe
            ensure_zone(
                zone_id, name, dept, sector,
                {"plant_species": species, "active_recipe_id": recipe["id"]},
            )

    for zone_id, name, dept, sector in QUARANTINE_ZONES:
        ensure_zone(zone_id, name, dept, sector, {"plant_species": None})

    print("\n[seed] --- Passo 2: attivazione coltivazione (POST /cultivations, puro input) ---")
    zone_recipe_version: dict[str, int] = {}
    for zone_id, _name, _dept, _sector, _receives_telemetry in PRODUCTION_ZONES:
        cultivation = ensure_cultivation(zone_id, zone_recipe[zone_id]["id"])
        if cultivation is not None:
            zone_recipe_version[zone_id] = cultivation["recipe_version"]
        else:
            # Ciclo già attivo da un run precedente: rileggiamo la versione
            # corrente dalla zona invece di assumerla.
            zone = request("GET", f"/zones/{zone_id}")[1]
            zone_recipe_version[zone_id] = zone.get("active_recipe_version") or zone_recipe[zone_id]["version"]

    print("\n[seed] --- Passo 3: telemetria diretta (finta, coerente con la ricetta) ---")
    for zone_id, _name, _dept, _sector, receives_telemetry in PRODUCTION_ZONES:
        if not receives_telemetry:
            print(f"[seed] {zone_id}: nessuna telemetria inviata di proposito, resterà offline")
            continue
        post_fake_telemetry(
            zone_id, zone_recipe[zone_id], zone_recipe_version[zone_id], boot_id,
        )

    print("\n[seed] --- Passo 4: piante e quarantena ---")
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

    print("\n[seed] --- Riepilogo finale (stato reale riportato dal backend) ---")
    status, zones = request("GET", "/zones")
    if status != 200 or not isinstance(zones, list):
        raise SystemExit(f"[seed] impossibile leggere GET /zones (status {status})")
    by_id = {z["id"]: z for z in zones}
    all_ids = [z[0] for z in PRODUCTION_ZONES] + [z[0] for z in QUARANTINE_ZONES]
    for zone_id in all_ids:
        zone = by_id.get(zone_id)
        if zone is None:
            print(f"  - {zone_id}: non trovata in GET /zones")
            continue
        print(
            f"  - {zone_id}: status={zone.get('status')} "
            f"lifecycle_state={zone.get('lifecycle_state')} "
            f"current_phase={zone.get('current_phase')!r} "
            f"current_strategies={zone.get('current_strategies')}"
        )

    status, plants = request("GET", "/plants?is_quarantined=true")
    quarantined_count = len(plants) if status == 200 and isinstance(plants, list) else "?"
    print(f"\n[seed] piante in quarantena (GET /plants?is_quarantined=true): {quarantined_count}")
    print(
        "\n[seed] fatto. Lo stato 'online' delle zone dura "
        "SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS (default 60s) da questa esecuzione: "
        "rilancia lo script per rinfrescarlo."
    )


if __name__ == "__main__":
    main()
