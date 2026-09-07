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
- Ogni zona online riceve una BREVE STORIA di HISTORY_SAMPLE_COUNT
  campioni (non più uno solo): ogni campione rappresenta
  HISTORY_STEP_SIMULATED_MINUTES minuti simulati di distanza dal
  precedente (nei valori di timestamp_seconds/recorded_at che lo script
  stesso decide di scrivere — non c'entra il campo time_scale del
  payload, che il backend limita a un tetto di 60 e che qui resta
  invariato, vedi TIME_SCALE_NOMINAL e la nota in
  post_fake_telemetry_history()). Le POST verso il backend vengono pero'
  spedite in sequenza a un ritmo reale di circa
  HISTORY_SEND_INTERVAL_REAL_SECONDS l'una, cosi' chi guarda lo script
  girare vede i dati "arrivare" a quel ritmo mentre i timestamp coprono
  un intervallo simulato molto più lungo. I valori dei sei canali
  controllati (e dei placeholder fisici) si muovono con un piccolo random
  walk attorno al setpoint di fase, solo per un aspetto plausibile su un
  grafico — non è una simulazione fisica. Lo stato "online" (calcolato da
  last_edge_contact, vedi backend/app/core/config.py
  offline_threshold_seconds, default 60s) dura solo dall'ultimo campione
  di questa storia in poi; per rinfrescarla basta rilanciare lo script.
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

AUTENTICAZIONE: come primo passo lo script prova il login su POST
/auth/login con l'account amministratore di esempio (vedi
demo/seed_users.py). Se il database e' ancora completamente vuoto (nessun
utente, seed_users.py mai eseguito), ricorre invece a POST
/auth/bootstrap-admin per crearsi al volo un amministratore usa-e-getta
(vedi ensure_admin_token()) — sempre attraverso un endpoint reale, mai
scrivendo nel database a mano. Il token ottenuto (in un modo o nell'altro)
viene allegato a ogni chiamata successiva, incluse le POST /users che
creano i quattro account agronomo nominati (vedi
ensure_named_agronomo_accounts()) esattamente come farebbe un
amministratore dal pannello "Utenti" della dashboard. Nessuno dei comandi
accodati qui è oggi ChangeStrategy/ConfirmConfiguration (l'unico gate
protetto da ruolo, vedi backend/app/features/commands/routes.py), ma
restare autenticati allo stesso modo evita rotture silenziose se lo
scenario dovesse cambiare in futuro.

ACCOUNT AGRONOMO NOMINATI: oltre all'admin, lo script crea (o salta se
già esistenti, in modo idempotente) quattro account agronomo con
username/password fissi — vedi NAMED_AGRONOMO_ACCOUNTS — utili per
provare la dashboard con più account "umani" invece del solo
agronomo/pass123 di seed_users.py.

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
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Sibling module, non il pacchetto backend: Python mette la cartella dello
# script (demo/) in sys.path[0] quando lo lanci direttamente, quindi questo
# import funziona sia da `python demo/seed_test_scenario.py` (dalla radice)
# sia da dentro demo/, senza bisogno di manipolare sys.path.
from seed_users import ADMIN_PASSWORD, ADMIN_USERNAME

BASE_URL = "http://127.0.0.1:8000"

# Usato SOLO da ensure_admin_token() quando il database e' completamente
# vuoto (seed_users.py non e' mai stato eseguito): permette allo script di
# funzionare anche su un database vergine, creando un amministratore
# usa-e-getta con POST /auth/bootstrap-admin invece di richiedere
# l'esecuzione preventiva di seed_users.py. Password in chiaro qui per lo
# stesso motivo di ADMIN_PASSWORD in seed_users.py: solo uso locale di
# sviluppo.
SEED_BOOTSTRAP_USERNAME = "seed-admin"
SEED_BOOTSTRAP_PASSWORD = "SeedBootstrap!2026"

# Quattro account agronomo "umani", creati (o saltati se già esistenti) via
# POST /users con il token amministratore ottenuto da ensure_admin_token():
# stesso percorso reale che userebbe un amministratore dal pannello
# "Utenti" della dashboard, non un inserimento diretto nel database.
NAMED_AGRONOMO_ACCOUNTS = [
    ("mario", "1234frutta"),
    ("elena", "1234verdura"),
    ("antonio", "piantagrassa2"),
    ("alice", "curatrice10"),
]

# Token di sessione ottenuto da ensure_admin_token() e allegato da request() a
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

# time_scale nel payload di telemetria: NON tocca la "velocita'" della
# storia che generiamo qui sotto (quella la decide solo la spaziatura dei
# timestamp, vedi HISTORY_STEP_SIMULATED_MINUTES) — resta lo stesso valore
# usato oggi per le zone Nominal. TelemetryCreate
# (backend/app/features/telemetry/models.py) lo valida con un tetto FISSO
# di 60: un valore piu' alto (es. 600, per "1 secondo reale = 10 minuti
# simulati") verrebbe sempre rifiutato con un errore di validazione,
# qualunque cosa rappresenti concettualmente.
TIME_SCALE_NOMINAL = 1.0

# Quanti campioni "storici" per zona online, e quanti minuti SIMULATI
# separano un campione dal successivo (nei valori di
# timestamp_seconds/recorded_at che scriviamo noi in ciascun campione: non
# hanno alcun tetto imposto dal backend, a differenza di time_scale sopra).
# Con i valori di default: 12 campioni x 10 minuti simulati = 2 ore di
# storia "simulata" per zona.
HISTORY_SAMPLE_COUNT = 12
HISTORY_STEP_SIMULATED_MINUTES = 10.0
# Ritmo REALE (secondi di orologio) fra una POST e la successiva della
# stessa storia: da' l'impressione di dati "in arrivo" mentre lo script
# gira, anche se i timestamp dentro ai dati coprono un intervallo molto
# più lungo.
HISTORY_SEND_INTERVAL_REAL_SECONDS = 1.0


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
    messaggio esplicito che ensure_admin_token()/ensure_cultivation()/
    post_fake_telemetry_history() eccetera sono pensati per mostrare — mascherando
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


def ensure_admin_token() -> None:
    """Ottiene un token amministratore per il resto dello script e lo salva
    in _AUTH_TOKEN, cosi' request() lo allega da qui in poi.

    @details Prova prima il login con l'account seminato da
    demo/seed_users.py (il caso comune). Se le credenziali vengono
    rifiutate (401), NON assume subito che sia un errore: interroga GET
    /auth/setup-required per distinguere "il database e' ancora vuoto"
    (seed_users.py non e' mai stato eseguito) da "esiste gia' un account ma
    con credenziali diverse" (un vero problema da segnalare). Nel primo
    caso ricorre a POST /auth/bootstrap-admin per crearsi al volo un
    amministratore usa-e-getta (SEED_BOOTSTRAP_USERNAME) — sempre
    attraverso un endpoint reale, mai scrivendo nel database a mano — cosi'
    lo script funziona anche senza aver prima lanciato seed_users.py."""
    global _AUTH_TOKEN
    status, body = request(
        "POST", "/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    if status == 200:
        _AUTH_TOKEN = body["token"]
        print(f"[seed] autenticato come {ADMIN_USERNAME!r} (ruolo={body['user']['role']})")
        return

    if status != 401:
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): il backend ha risposto con un errore "
            f"inatteso (status {status}) {body}. Non sembra un problema di "
            "credenziali: puo' essere un errore lato server (controlla il log "
            "del backend, es. un 'disk I/O error' o un altro problema di "
            "accesso al database)."
        )

    # 401: o le credenziali sono sbagliate, o il database e' ancora vuoto e
    # seed_users.py non e' mai stato eseguito. Lo distinguiamo interrogando
    # /auth/setup-required invece di indovinare dal messaggio del 401.
    setup_status, setup_body = request("GET", "/auth/setup-required")
    if setup_status != 200:
        raise SystemExit(
            f"[seed] impossibile autenticarsi come {ADMIN_USERNAME!r} (401) e "
            "impossibile verificare se il database e' vuoto (GET "
            f"/auth/setup-required: status {setup_status} {setup_body})."
        )

    if not setup_body.get("setup_required"):
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): credenziali rifiutate (401) {body}, ma il "
            "database non risulta vuoto (esiste gia' almeno un account, "
            "probabilmente con un altro username/password). Hai gia' eseguito "
            "`python demo/seed_users.py` contro questo stesso database, o e' "
            "stato creato un amministratore con credenziali diverse?"
        )

    print(
        f"[seed] nessun account trovato ({ADMIN_USERNAME!r} non esiste "
        "ancora): il database e' vuoto. Creo un amministratore di seed al "
        "volo con POST /auth/bootstrap-admin invece di richiedere "
        "l'esecuzione preventiva di `python demo/seed_users.py`."
    )
    bootstrap_status, bootstrap_body = request(
        "POST",
        "/auth/bootstrap-admin",
        {"username": SEED_BOOTSTRAP_USERNAME, "password": SEED_BOOTSTRAP_PASSWORD},
    )
    if bootstrap_status != 201:
        raise SystemExit(
            "[seed] impossibile creare l'amministratore iniziale via POST "
            f"/auth/bootstrap-admin: status {bootstrap_status} {bootstrap_body}. "
            "Puo' darsi che un'altra esecuzione concorrente lo abbia gia' "
            "creato nel frattempo: riprova."
        )
    _AUTH_TOKEN = bootstrap_body["token"]
    print(
        f"[seed] autenticato come {SEED_BOOTSTRAP_USERNAME!r} "
        f"(ruolo={bootstrap_body['user']['role']}, creato ora da questo script)"
    )


def ensure_named_agronomo_accounts() -> None:
    """Crea i quattro account agronomo nominati (NAMED_AGRONOMO_ACCOUNTS),
    sempre attraverso POST /users con il token amministratore corrente —
    mai scrivendo nel database a mano: e' lo stesso percorso, con la stessa
    validazione (UserCreate) e lo stesso controllo di ruolo (require_admin),
    che userebbe un amministratore dal pannello "Utenti" della dashboard.

    Idempotente: un 409 (username gia' esistente) salta quell'account e
    stampa un avviso, senza fermare lo script ne' gli altri tre account."""
    for username, password in NAMED_AGRONOMO_ACCOUNTS:
        status, body = request(
            "POST",
            "/users",
            {"username": username, "password": password, "role": "agronomo"},
        )
        if status == 201:
            print(f"[seed] account agronomo creato: {username!r}")
        elif status == 409:
            print(f"[seed] account {username!r} gia' esistente, lo lascio com'è")
        else:
            raise SystemExit(
                f"[seed] errore creando l'account agronomo {username!r}: "
                f"status {status} {body}"
            )


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


def _walk(value: float, step: float, min_value: float, max_value: float) -> float:
    """Un passo di random walk limitato: sposta `value` di una quantita'
    casuale in [-step, step], poi lo ricaccia dentro [min_value, max_value]
    se ne esce. Serve solo a dare alla storia generata da
    post_fake_telemetry_history() un aspetto plausibile su un grafico — non
    e' una simulazione fisica, esattamente come il resto di questo script
    (vedi nota in testa al file)."""
    return min(max_value, max(min_value, value + random.uniform(-step, step)))


def post_fake_telemetry_history(
    zone_id: str, recipe: dict, recipe_version: int, boot_id: str,
) -> None:
    """Invia una BREVE STORIA di HISTORY_SAMPLE_COUNT campioni per la zona
    (non piu' un singolo campione), simulando quello che un Edge reale
    riporterebbe dopo aver adottato `recipe`: stessa fase, stesse Strategy
    auto-confermate (identiche a
    recipe['controllers'][*]['selected_strategy'], come fa davvero l'Edge
    da RecipeControlSystem::confirm_all_from_recipe), setpoint della prima
    fase come punto di partenza del random walk di ciascun canale.

    @details DUE ritmi INDIPENDENTI, da non confondere:
    - Nei DATI: ogni campione rappresenta HISTORY_STEP_SIMULATED_MINUTES
      minuti simulati di distanza dal precedente. E' la spaziatura dei
      valori di timestamp_seconds/recorded_at che scriviamo NOI in ogni
      campione: non ha alcun limite imposto dal backend, perche' non e'
      il campo time_scale del payload (quello resta fisso a
      TIME_SCALE_NOMINAL, l'unico valore che TelemetryCreate valida con un
      tetto di 60 — vedi backend/app/features/telemetry/models.py).
    - Nell'INVIO: le POST verso il backend vengono spedite in sequenza a
      un ritmo REALE di circa HISTORY_SEND_INTERVAL_REAL_SECONDS l'una
      (un time.sleep() fra un campione e il successivo), cosi' chi guarda
      lo script girare vede i dati "arrivare" a quel ritmo, anche se i
      timestamp dentro ai dati coprono un intervallo molto piu' lungo.

    L'ultimo campione (il piu' recente) e' datato "adesso": last_edge_contact
    (e quindi lo stato "online" della zona) riflette il momento in cui
    questa funzione termina, non l'inizio della storia simulata."""
    phase = recipe["phases"][0]
    setpoint_by_variable = {t["variable"]: t["setpoint"] for t in phase["targets"]}
    strategy_by_variable = {c["variable"]: c["selected_strategy"] for c in recipe["controllers"]}

    now = datetime.now(timezone.utc)
    step_simulated_seconds = HISTORY_STEP_SIMULATED_MINUTES * 60.0
    oldest_recorded_at = now - timedelta(
        minutes=HISTORY_STEP_SIMULATED_MINUTES * (HISTORY_SAMPLE_COUNT - 1)
    )

    # Valore corrente di ciascun canale: parte dal setpoint/placeholder e
    # cammina di campione in campione (vedi _walk()). I canali senza un
    # tetto nello schema (azoto/fosforo/potassio/fertilizzante) usano un
    # margine generoso attorno al valore INIZIALE come limite superiore,
    # non un vincolo del backend.
    soil_moisture = setpoint_by_variable["soil_moisture"]
    nitrogen = setpoint_by_variable["nitrogen"]
    phosphorus = setpoint_by_variable["phosphorus"]
    potassium = setpoint_by_variable["potassium"]
    ph = setpoint_by_variable["ph"]
    light = setpoint_by_variable["light"]
    temperature = PLACEHOLDER_TEMPERATURE_C
    humidity = PLACEHOLDER_AIR_HUMIDITY_PERCENT
    soil_bulk_ec = PLACEHOLDER_SOIL_BULK_EC_MS_CM
    soil_ec = PLACEHOLDER_SOIL_EC_MS_CM
    fertilizer = PLACEHOLDER_FERTILIZER_CONCENTRATION_MG_PER_LITER
    nitrogen_cap = max(nitrogen * 3.0, 1.0)
    phosphorus_cap = max(phosphorus * 3.0, 1.0)
    potassium_cap = max(potassium * 3.0, 1.0)
    fertilizer_cap = max(fertilizer * 3.0, 1.0)

    last_recorded_at = oldest_recorded_at
    for i in range(HISTORY_SAMPLE_COUNT):
        if i > 0:
            soil_moisture = _walk(soil_moisture, 2.0, 0.0, 100.0)
            nitrogen = _walk(nitrogen, max(nitrogen * 0.06, 1.0), 0.0, nitrogen_cap)
            phosphorus = _walk(phosphorus, max(phosphorus * 0.06, 1.0), 0.0, phosphorus_cap)
            potassium = _walk(potassium, max(potassium * 0.06, 1.0), 0.0, potassium_cap)
            ph = _walk(ph, 0.15, 0.0, 14.0)
            light = _walk(light, 60.0, 0.0, 3000.0)
            temperature = _walk(temperature, 0.3, -50.0, 80.0)
            humidity = _walk(humidity, 1.5, 0.0, 100.0)
            soil_bulk_ec = _walk(soil_bulk_ec, 0.05, 0.0, 8.0)
            soil_ec = _walk(soil_ec, 0.08, 0.0, 8.0)
            fertilizer = _walk(fertilizer, max(fertilizer * 0.05, 5.0), 0.0, fertilizer_cap)

        setpoints_snapshot = {
            "soil_moisture": soil_moisture,
            "nitrogen": nitrogen,
            "phosphorus": phosphorus,
            "potassium": potassium,
            "ph": ph,
            "light": light,
        }
        last_recorded_at = oldest_recorded_at + timedelta(
            minutes=HISTORY_STEP_SIMULATED_MINUTES * i
        )
        telemetry = {
            "sequence_number": i + 1,
            "boot_id": boot_id,
            "recorded_at": last_recorded_at.isoformat().replace("+00:00", "Z"),
            "timestamp_seconds": 1.0 + step_simulated_seconds * i,
            "temperature_c": temperature,
            "air_humidity_percent": humidity,
            "soil_moisture_percent": soil_moisture,
            "soil_bulk_ec_ms_cm": soil_bulk_ec,
            "soil_ec_ms_cm": soil_ec,
            "fertilizer_concentration_mg_per_liter": fertilizer,
            "nitrogen_estimate_mg_per_liter": nitrogen,
            "phosphorus_estimate_mg_per_liter": phosphorus,
            "potassium_estimate_mg_per_liter": potassium,
            "ph": ph,
            "light_ppfd_umol_m2_s": light,
            "active_recipe_id": recipe["id"],
            "active_recipe_version": recipe_version,
            "current_phase": phase["name"],
            "operational_state": "Nominal",
            "lifecycle_state": "Running",
            "current_strategies": strategy_by_variable,
            "current_setpoints": setpoints_snapshot,
            "time_scale": TIME_SCALE_NOMINAL,
        }
        status, body = request("POST", f"/zones/{zone_id}/telemetry", telemetry)
        if status != 201:
            raise SystemExit(
                f"[seed] errore inviando il campione storico {i + 1}/"
                f"{HISTORY_SAMPLE_COUNT} a {zone_id}: {status} {body}"
            )
        if i < HISTORY_SAMPLE_COUNT - 1:
            time.sleep(HISTORY_SEND_INTERVAL_REAL_SECONDS)

    print(
        f"[seed] storia di {HISTORY_SAMPLE_COUNT} campioni inviata a {zone_id} "
        f"(passo {HISTORY_STEP_SIMULATED_MINUTES:g} min simulati, ritmo reale "
        f"~{HISTORY_SEND_INTERVAL_REAL_SECONDS:g}s/campione): fase "
        f"{phase['name']!r}, ultimo campione datato {last_recorded_at.isoformat()}"
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

    print("[seed] --- Passo 0: ottenimento token amministratore ---")
    ensure_admin_token()

    print("\n[seed] --- Passo 0bis: account agronomo nominati (POST /users, idempotente) ---")
    ensure_named_agronomo_accounts()

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

    print(
        "\n[seed] --- Passo 3: storia di telemetria diretta (finta, coerente "
        f"con la ricetta, {HISTORY_SAMPLE_COUNT} campioni a "
        f"{HISTORY_STEP_SIMULATED_MINUTES:g} min simulati l'uno, ritmo reale "
        f"~{HISTORY_SEND_INTERVAL_REAL_SECONDS:g}s/campione) ---"
    )
    for zone_id, _name, _dept, _sector, receives_telemetry in PRODUCTION_ZONES:
        if not receives_telemetry:
            print(f"[seed] {zone_id}: nessuna telemetria inviata di proposito, resterà offline")
            continue
        post_fake_telemetry_history(
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
