# Test RIGOROSO — richiede l'Edge C++ reale acceso a mano.
# Usa questo prima di una consegna/esame, per verificare che il sistema
# vero (non simulato) si comporti correttamente.
# Per popolare rapidamente la dashboard durante lo sviluppo, usa invece
# demo/seed_test_scenario.py (non richiede l'Edge).
#
# NOTA su r4-s2 (CommandFailed): lo scenario storico "CommandFailed per
# volume di irrigazione oltre il limite fisico dell'attuatore (5.0 L)" non
# e' piu' riproducibile da input legittimo dal commit 85633f6 in poi
# ("Strategy diventa impostazione globale d'impianto", branch dashboard):
# il backend (recipes/repository.py::_stamp_global_strategy() +
# recipes/parameters.py::default_parameters_for()) ricalcola SEMPRE i
# parametri del controllore (incluso active_command) da formule globali
# fisse alla lettura di ogni ricetta, ignorando qualunque valore la ricetta
# dichiari — per soil_moisture il comando massimo possibile e' ora 1.0 L
# (Threshold) o 0.5 L (PID/Predictive), sempre sotto il limite fisico reale.
# Vedi la nota estesa "LOCKDOWN 'DA SICUREZZA' (safety_range) su r4-s2" piu'
# sotto per il meccanismo attualmente usato al suo posto (una violazione di
# safety_range, non un CommandFailed) e per i dettagli di come e' stato
# verificato dal vivo in questa sessione.

"""Popolamento SmartHydro basato ESCLUSIVAMENTE sull'Edge C++ reale.

USO: con il backend già avviato (uvicorn backend.app.main:app --reload):

    python demo/seed_dev_data.py

Per puntare a un backend remoto (es. il deploy Railway, dove — vedi
Dockerfile/start.sh — l'Edge reale parte da solo insieme al backend a ogni
avvio del container, senza bisogno di avviarlo qui a mano):

    SMARTHYDRO_BASE_URL=https://<nome-servizio>.up.railway.app python demo/seed_dev_data.py

Il prompt "avvia l'Edge e premi INVIO" (vedi wait_for_edge_start()) diventa
automaticamente superfluo in questo caso: lo script prova per una manciata
di secondi, SENZA chiedere alcun input, se le zone appena attivate
diventano già Running da sole (segno che un Edge è già in esecuzione e
raggiungibile, come su Railway); solo se questo non accade — il caso
locale, dove l'Edge va ancora avviato a mano — torna al prompt interattivo
di sempre. Vedi probe_edge_already_running().

Questo script NON invia mai telemetria o stati calcolati a mano: fornisce
solo l'input che un chiamante legittimo può dare al backend (registrazione
zone, attivazione coltivazioni, iniezione guasti). Tutto il resto —
telemetria, operational_state, lifecycle_state, fasi, strategie — viene
letto e riportato dal vero Edge C++ (edge.exe), che l'utente avvia a mano
in un terminale separato quando lo script lo richiede. Lo script si limita
ad ASPETTARE (polling con timeout) che l'Edge reale faccia il suo lavoro.

NOTA PER CHI LEGGE: questo script imposta time_scale=60 su ogni zona
SOLO per velocizzare i test/demo in questo ambiente. Il sistema
SmartHydro di default lavora a time_scale=1x (tempo reale) — è il
comportamento che verrà mostrato/consegnato. L'accelerazione qui è
uno strumento di sviluppo, non una feature del prodotto.

VINCOLI RISPETTATI:
- Nessuna POST /zones/{id}/telemetry né /zones/{id}/actuators da questo
  script: solo registrazione zone, comandi (ActivateCultivation via
  POST /cultivations, SetSimulationSpeed, InjectFault, ResetFault,
  ResetEmergency) e la parte di piante/quarantena, tutta pura richiesta
  legittima come farebbe un chiamante umano o un pannello di controllo.

AUTENTICAZIONE: come primo passo lo script fa login su POST /auth/login con
l'account amministratore di esempio (vedi demo/old_test/seed_users.py, che
va eseguito almeno una volta prima di questo script) e allega il token
ottenuto a ogni comando accodato su POST /zones/{id}/commands. Nessuno dei
command_type usati qui è oggi ChangeStrategy/ConfirmConfiguration (l'unico
gate protetto da ruolo, vedi backend/app/features/commands/routes.py), ma
restare autenticati allo stesso modo evita rotture silenziose se lo
scenario dovesse cambiare in futuro.
- edge.exe NON viene lanciato da qui: lo script stampa il comando e
  aspetta un INVIO dell'utente prima di fare polling.
- Un solo Edge (--edge-id) gestisce tutte le zone con quell'
  assigned_edge_id: lo scopre da solo interrogando il backend, non serve
  passargli --zones/--zone-id (verificato: senza queste opzioni l'Edge fa
  discovery automatica delle zone assegnate).
- La zona offline (r2-s2) non ha alcun assigned_edge_id: non riceverà mai
  telemetria dal nostro Edge, quindi resta offline per davvero.

Topologia (stessa forma delle versioni precedenti dello script):
- Reparto 1: r1-s1 (Nominal, avanzata fino all'ultima fase della ricetta),
  r1-s2 (dimostrazione InjectFault -> Degraded, auto-recupero; NON avanzata
  di fase, resta nella sua fase iniziale per tutta la demo)
- Reparto 2: r2-s1 (Nominal, avanzata fino all'ultima fase), r2-s2
  (OFFLINE: nessun assigned_edge_id, mai — stesso principio di r2-s2 in
  demo/seed_test_scenario.py: non è una disconnessione simulata a metà
  scenario, è una zona che non ha mai avuto un Edge)
- Reparto 3: r3-s1 (Nominal, avanzata fino all'ultima fase); r3-s2 esiste
  solo per pochi istanti durante il Passo 1 (vedi ALLARMI "QUARANTENA
  ORFANA" sotto) e NON è più presente a fine script
- Reparto 4: r4-s2 (dimostrazione lockdown per violazione di safety_range, 
  vedi sotto; NON avanzata di fase)
- Reparto 5: r5-s1 (unico settore possibile per la quarantena) + 2 piante
  già in quarantena fin dal primissimo istante (backdate istantaneo,
  puramente illustrativo — vedi QUARANTINE_INSTANT_PLANT_SOURCES/
  ensure_instant_quarantine_plants()) + altre 5 piante che entrano in
  quarantena solo dopo l'attesa reale del Passo 8, backdatate oltre la
  soglia di rilascio del frontend (vedi BACKDATING QUARANTENA sotto) + 1
  pianta orfana (home_zone_id punta a r3-s2, non a un settore del Reparto
  5 — vedi ALLARMI "QUARANTENA ORFANA" sotto): 8 piante in quarantena in
  totale, 7 "del Reparto 5" in senso stretto (nate lì) più questa

Ogni zona online (con un Edge assegnato) riceve anche una pianta residente
non quarantenata, di specie coerente con la ricetta della zona (vedi
ensure_resident_plants()): senza questo, un settore Nominal/Degraded/
EmergencyLockdown normale non avrebbe mai alcuna pianta a proprio nome, a
differenza delle 7 piante del Reparto 5 (che sono sempre e solo in
quarantena).

AVANZAMENTO DI FASE (AdvanceRecipePhase) — ogni ricetta di catalogo ha 4
fasi la cui durata reale (control_system.cpp/profiles.json) va da ~126 a
~231 giorni: anche a time_scale=60 non c'e' alcuna speranza di vedere una
zona arrivare da sola all'ultima fase entro una demo di pochi minuti.
AdvanceRecipePhase (backend/app/features/commands/models.py CommandType.
ADVANCE_RECIPE_PHASE, gestito in edge/src/runtime/runtime_commands.cpp e
edge/src/runtime/edge_runtime_configuration.cpp advance_recipe_phase()) è
un comando legittimo pensato esattamente per questo: sposta in avanti
SOLO l'orologio interno della ricetta fino all'inizio della fase
successiva (mai lo stato dei sensori/attuatori), e risponde "rejected:
recipe is already in its last phase" quando non c'e' una fase successiva
— usato qui per sapere quando fermarsi, senza dover contare le fasi a
mano. Non e' quindi un dato calcolato a mano: e' lo stesso comando che un
agronomo potrebbe inviare dal pannello di controllo per far avanzare
manualmente una coltivazione.

BACKDATING QUARANTENA — dashboard/script.js definisce
QUARANTINE_MIN_RELEASE_MS (24h, solo lato frontend, non imposto dal
backend) prima che "Fai uscire" diventi cliccabile. PATCH /plants/{id}/
quarantine ora accetta un quarantined_at opzionale (solo nel passato, solo
insieme a is_quarantined=True — vedi backend/app/features/plants/
models.py, aggiunto apposta per questo script): ensure_quarantine() lo usa
per registrare le 5 piante del Passo 8 come già in quarantena da oltre 24h
al momento in cui viene chiamato, cosi' chi guarda non deve aspettare un
giorno reale per vedere il bottone abilitato. Il backend applica lo stesso
identico istante sia a plants.quarantined_at sia a plant_movements.
moved_at (mai solo all'uno o all'altro): i due raccontano lo stesso
evento e non devono mai divergere.

Il tempo di quarantena non e' pero' un salto istantaneo: dato che
QUARANTINE_MIN_RELEASE_MS confronta solo tempo reale e che ripetere la
stessa PATCH e' un no-op silenzioso (vedi set_quarantine_state(), non si
può quindi far avanzare quarantined_at a piccoli passi), lo script prima
ATTENDE per davvero — con la stessa narrazione (narrate_wait) usata
altrove — il tempo reale equivalente al margine QUARANTINE_BACKDATE al
ritmo di 1 secondo reale = QUARANTINE_MINUTES_PER_SECOND minuti di
quarantena (vedi QUARANTINE_REAL_WAIT_SECONDS: con 30h di margine e 10
min/sec sono 180s, 3 minuti), e SOLO alla fine di quell'attesa registra
con una singola PATCH il quarantined_at già backdatato di
QUARANTINE_BACKDATE. Chi guarda la demo vede quindi il tempo di
quarantena scorrere con lo stesso spirito accelerato del resto della
serra, invece di un salto invisibile.

Perche' i 3 minuti di attesa reale non lascino il Reparto 5 vuoto proprio
all'inizio della demo (scopo puramente illustrativo: chi apre subito la
dashboard deve vedere gia' qualcosa in quarantena, non un settore vuoto),
ensure_instant_quarantine_plants() registra SUBITO, con backdate
istantaneo (nessuna attesa, stesso margine QUARANTINE_BACKDATE), altre 2
piante distinte — vedi QUARANTINE_INSTANT_PLANT_SOURCES — prima ancora
che parta l'attesa del Passo 8. Popolazione separata dalle 5 piante del
Passo 8: id diversi, nessuna sovrapposizione.

LOCKDOWN "DA SICUREZZA" (safety_range) su r4-s2 — terza transizione,
qualitativamente diversa dalle due sopra: niente InjectFault, e niente
CommandFailed nonostante il nome storico della zona (r4-s2 era in origine
la demo CommandFailed: vedi il ripensamento sotto). E' scatenata da puro
INPUT di ricetta (POST /recipes, target di fase), senza toccare una riga
di codice dell'Edge.

Perche' NON e' (piu') CommandFailed — verificato dal vivo in questa
sessione, non solo a codice: la tecnica storica (una ricetta con un
active_command/soglia tale da chiedere alla pompa piu' del limite fisico
di 5.0 L, edge/src/simulation/actuator_simulator.cpp) funzionava quando fu
verificata la prima volta, ma da allora e' stata introdotta (branch
dashboard, commit 85633f6 "Strategy diventa impostazione globale
d'impianto") la ristampa globale dei parametri del controllore:
backend/app/features/recipes/repository.py::_stamp_global_strategy()
sovrascrive SEMPRE selected_strategy e parameters di OGNI controllore alla
lettura di una ricetta (GET /recipes, e quindi anche il comando
ActivateCultivation che la incapsula), con
backend/app/features/recipes/parameters.py::default_parameters_for() —
funzione pura, senza alcuna lettura da configurazione modificabile: per
Threshold su soil_moisture restituisce SEMPRE active_command=1.0 L,
qualunque valore la ricetta dichiari. 1.0 L resta sempre sotto i 5.0 L
fisici, quindi nessuna ricetta (e nessun comando disponibile: SetSimulation-
Speed/AdvanceRecipePhase/InjectFault non toccano l'attuatore) puo' piu' far
scattare quell'eccezione tramite input legittimo. Verificato dal vivo:
un run completo di r4-s2 con la vecchia ricetta non ha mai prodotto un
evento CommandFailed, solo un fault ricorrente e non voluto
("commanded_without_response") e infine un EmergencyLockdown casuale per
una violazione del safety_range della luce, scollegata dallo scenario
voluto — da cui la sostituzione qui sotto con un meccanismo diverso ma
altrettanto reale.

Il nuovo meccanismo, deterministico e riproducibile:
- Il target di fase soil_moisture di questa ricetta (ensure_safety_lockdown
  _recipe(), *solo* il target, il controllore resta quello originale del
  catalogo — tanto i suoi parametri sarebbero comunque ristampati) ha un
  safety_range volutamente stretto attorno all'allowed_range (43-57%
  contro 45-55%): un margine di soli 2 punti percentuali per lato.
- edge/src/runtime/edge_runtime_core.cpp::environment_for_recipe() campiona
  l'umidita' INIZIALE del terriccio non dentro l'allowed_range ma in una
  finestra allargata di meta' della sua ampiezza per lato — quindi puo'
  cadere gia' fuori sia dall'allowed_range sia (con margini stretti come
  questi) dal safety_range fin dal primissimo ciclo. Il seed di questo
  campionamento e' FISSO (environment_seed di default, mai passato da riga
  di comando) e mescolato con l'hash dell'id ricetta: per una data stringa
  id il valore campionato e' quindi sempre lo stesso, run dopo run — motivo
  per cui LOCKDOWN_SAFETY_DEMO_RECIPE_ID e' scritta esattamente com'e'
  sotto (trovata empiricamente in questa sessione provando alcuni id su
  questo stesso Edge/backend finche' il campionamento non e' caduto fuori
  dal safety_range fin dal primo ciclo: 81.33%, ben oltre 57): cambiare
  quella stringa cambia il campione, quindi NON rinominarla senza riverifica
  dal vivo.
- Il risultato e' un FaultDetected CRITICAL con rule="outside_recipe_
  safety_range" component="soil_moisture_sensor" (edge/src/faults/
  fault_detector.cpp) sul primissimo ciclo di controllo, seguito
  immediatamente da StateChanged Nominal -> EmergencyLockdown (bypassando
  Degraded, vedi la nota IMPORTANTE sotto) — senza bisogno di alcun
  InjectFault ne' di ripetuti cicli di attesa: e' per questo lo scenario
  piu' VELOCE dei tre, utile per stare dentro il budget dei primi 3 minuti
  (punto 9). La zona resta in EmergencyLockdown per il resto della demo
  (nessun reset automatico): e' la terza situazione
  attiva, diversa sia da r1-s2 (fault recuperabile breve) sia da un 
  lockdown causato da escalation.

IMPORTANTE su cosa "genera" Degraded vs EmergencyLockdown nel codice reale
dell'Edge (verificato leggendo edge/src/faults/fault_detector.cpp ed
edge/src/runtime/edge_runtime_fsm.cpp, non assunto):
- Un valore che supera il safety_range della ricetta per una variabile
  fisica come soil_moisture/ph/light NON produce Degraded: produce una
  fault CRITICAL e porta IMMEDIATAMENTE a EmergencyLockdown (bypassando
  Degraded) — esattamente il meccanismo usato sopra per r4-s2. Per questo
  motivo r1-s2 sotto NON usa `sensor_offset` per ottenere Degraded:
  userebbe un valore fuori dal safety_range e salterebbe dritto in
  EmergencyLockdown, contraddicendo il suo obiettivo (Degraded).
- Un fault RECOVERABLE (es. `sensor_dropout`, cioè "missing_value") porta
  invece SUBITO a Degraded al primo ciclo di controllo in cui è rilevato;
  se lo stesso fault persiste per `recoverable_faults_before_lockdown`
  cicli consecutivi (3 di default), la FSM lo interpreta come "tre guasti
  recuperabili consecutivi" ed escala da sola a EmergencyLockdown — non è
  necessario inviare tre comandi InjectFault separati: un singolo fault
  persistente (senza duration_seconds) che dura almeno 3 cicli produce
  esattamente la stessa escalation descritta nel README, in modo
  deterministico (tre InjectFault ravvicinati rischierebbero di cadere
  nello stesso ciclo di controllo e contare come un solo guasto, perché la
  FSM valuta la gravità una volta per ciclo, non una volta per fault
  iniettato).
Per questo: r1-s2 riceve un `sensor_dropout` con `duration_seconds` corto
(inferiore alla durata di un ciclo di controllo), cosi resta attivo per un
solo ciclo -> Degraded -> auto-recupero. 

ALLARMI: copertura delle categorie della pagina Allarmi (dashboard/script.js)
— verificato leggendo computeFlaggedZones/computeOrphanQuarantineGroups/
isAlarmEvent/eventBadgeMeta/activeZoneCardMeta/renderOrphanGroupCard, non
assunto. Prima di questo giro lo script copriva già: GUASTO+Degraded
(r1-s2), EmergencyLockdown da violazione di safety_range (r4-s2), 
SETTORE OFFLINE (r2-s2), Registro eventi (storico di tutti questi) e 
Piante in osservazione (le 8 piante di quarantena sopra).

- QUARANTENA ORFANA: nessuno scenario cancellava mai una zona con una
  pianta già in quarantena che la usa come home_zone_id. Aggiunta con
  ensure_orphan_quarantine_scenario() (ORPHAN_QUARANTINE_ZONE_ID = r3-s2):
  crea un settore temporaneo del Reparto 3 mai attivato, vi mette in
  quarantena una pianta dedicata, poi lo cancella con DELETE /zones/{id} —
  che non tocca mai plants/plant_movements (zones/repository.py, verificato
  leggendo il codice), lasciando la pianta orfana. Puramente transitoria:
  a fine script quella zona non esiste più, quindi non aumenta il numero
  di settori della demo.
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

# Aggiungi la radice del repository al path per poter importare il backend
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.core.database import get_connection, init_db
from backend.app.features.users.models import UserRole
from backend.app.features.users.repository import UsernameConflict, create_user
from backend.app.features.users.security import hash_password

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "pass123"

# Account Agronomi nominati.
# Creati (o aggiornati se già esistenti) con lo stesso identico meccanismo
# dell'admin (_upsert_user, scrittura diretta nel database), non via HTTP.
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

EDGE_READY_PROBE_SECONDS = 25.0

PHASE_ADVANCE_ZONE_IDS = ["r1-s1", "r2-s1", "r3-s1"]
QUARANTINE_BACKDATE = timedelta(hours=30)
QUARANTINE_MINUTES_PER_SECOND = 10.0
QUARANTINE_REAL_WAIT_SECONDS = (
    QUARANTINE_BACKDATE.total_seconds() / 60.0 / QUARANTINE_MINUTES_PER_SECOND
)
QUARANTINE_INSTANT_PLANT_SOURCES = [
    ("plant-early-1", "r2-s1"),
    ("plant-early-2", "r3-s1"),
]

NARRATION_TICK_SECONDS = 5.0

# Zona dedicata alla dimostrazione del lockdown safety_range. 
# Sta FUORI da PRODUCTION_ZONES apposta:
LOCKDOWN_SAFETY_DEMO_ZONE_ID = "r4-s2"
LOCKDOWN_SAFETY_DEMO_DEPARTMENT = 4
LOCKDOWN_SAFETY_DEMO_SECTOR = 2
LOCKDOWN_SAFETY_DEMO_RECIPE_ID = "recipe-safety-demo-g"

# id, nome, department_number, sector_number, ha un Edge assegnato
# NOTA: r4-s1 e' stato RIMOSSO per evitare di avere due settori "Fragola".
PRODUCTION_ZONES = [
    ("r1-s1", "Reparto 1 - Settore 1", 1, 1, True),
    ("r1-s2", "Reparto 1 - Settore 2", 1, 2, True),
    ("r2-s1", "Reparto 2 - Settore 1", 2, 1, True),
    ("r2-s2", "Reparto 2 - Settore 2", 2, 2, False),
    ("r3-s1", "Reparto 3 - Settore 1", 3, 1, True),
]
QUARANTINE_ZONES = [
    ("r5-s1", "Quarantena - Settore 1", 5, 1),
]

ORPHAN_QUARANTINE_ZONE_ID = "r3-s2"
ORPHAN_QUARANTINE_PLANT_ID = "plant-orphan-1"


def _upsert_user(
    connection: sqlite3.Connection,
    username: str,
    password: str,
    role: UserRole,
    display_name: str,
) -> None:
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
    global _AUTH_TOKEN
    status, body = request(
        "POST", "/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    if status == 401:
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): credenziali rifiutate (401) {body}. Hai gia' "
            "eseguito `python demo/old_test/seed_users.py` contro questo stesso database?"
        )
    if status != 200:
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): il backend ha risposto con un errore "
            f"inatteso (status {status}) {body}."
        )
    _AUTH_TOKEN = body["token"]
    print(f"[seed] autenticato come {ADMIN_USERNAME!r} (ruolo={body['user']['role']})")


def pick_recipes(department_number: int, how_many: int) -> list[dict]:
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


def ensure_safety_lockdown_recipe() -> str:
    status, catalog = request(
        "GET", f"/recipes?department_number={LOCKDOWN_SAFETY_DEMO_DEPARTMENT}"
    )
    template = None
    if status == 200 and isinstance(catalog, list):
        template = next(
            (r for r in catalog if r.get("id") != LOCKDOWN_SAFETY_DEMO_RECIPE_ID), None
        )
    if template is None:
        raise SystemExit(
            "[seed] impossibile trovare una ricetta di catalogo del reparto "
            f"{LOCKDOWN_SAFETY_DEMO_DEPARTMENT} da usare come base per la "
            "ricetta della demo lockdown/safety_range."
        )

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
        phase["targets"] = [
            narrow_target if t["variable"] == "soil_moisture" else t
            for t in phase["targets"]
        ]

    status, body = request("POST", "/recipes", recipe)
    if status == 201:
        print(
            f"[seed] ricetta demo creata: {LOCKDOWN_SAFETY_DEMO_RECIPE_ID!r} "
            f"(base: {template['id']!r} del reparto {LOCKDOWN_SAFETY_DEMO_DEPARTMENT}, "
            "soil_moisture: allowed_range 45-55%, safety_range 43-57%)"
        )
    elif status == 409:
        print(
            f"[seed] ricetta demo {LOCKDOWN_SAFETY_DEMO_RECIPE_ID!r} gia' "
            "esistente da un run precedente, la lascio com'e'"
        )
    else:
        raise SystemExit(
            f"[seed] errore creando la ricetta demo lockdown/safety_range: {status} {body}"
        )
    return plant_type


def ensure_safety_lockdown_zone(plant_type: str) -> None:
    ensure_zone(
        LOCKDOWN_SAFETY_DEMO_ZONE_ID,
        "Fragola",
        LOCKDOWN_SAFETY_DEMO_DEPARTMENT,
        LOCKDOWN_SAFETY_DEMO_SECTOR,
        {
            "plant_species": plant_type,
            "active_recipe_id": LOCKDOWN_SAFETY_DEMO_RECIPE_ID,
            "assigned_edge_id": EDGE_ID,
        },
    )


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


def delete_zone(zone_id: str) -> None:
    status, body = request("DELETE", f"/zones/{zone_id}")
    if status == 204:
        print(
            f"[seed] {zone_id} eliminata: qualunque pianta con "
            f"home_zone_id={zone_id!r} resta ora orfana (nessuna cascata "
            "lato backend, per costruzione)"
        )
    elif status == 404:
        print(f"[seed] {zone_id} già assente (probabile rerun), nulla da eliminare")
    else:
        print(f"[seed] avviso: impossibile eliminare {zone_id} (status {status}) {body}")


def ensure_orphan_quarantine_scenario(plant_species: str) -> None:
    ensure_zone(
        ORPHAN_QUARANTINE_ZONE_ID,
        "Reparto 3 - Settore 2 (mai attivato)",
        3,
        2,
        {"plant_species": plant_species},
    )
    ensure_plant(ORPHAN_QUARANTINE_PLANT_ID, plant_species, ORPHAN_QUARANTINE_ZONE_ID)
    ensure_quarantine(
        ORPHAN_QUARANTINE_PLANT_ID,
        "r5-s1",
        "trasferita in quarantena per chiusura del settore di origine",
    )
    delete_zone(ORPHAN_QUARANTINE_ZONE_ID)


def ensure_cultivation(zone_id: str, recipe_id: str) -> None:
    status, body = request(
        "POST", "/cultivations", {"zone_id": zone_id, "recipe_id": recipe_id}
    )
    if status in (201, 409):
        return
    print(f"[seed] avviso: coltivazione non creata su {zone_id} (status {status}) {body}")


def push_historical_telemetry(zone_id: str) -> None:
    """Genera 24 ore di telemetria coerente e la invia al backend tramite HTTP.
    Usa 'soil_moisture' per garantire che i grafici della dashboard riflettano i dati."""
    print(f"[seed] Iniezione telemetria storica simulata per {zone_id}...")
    now = datetime.now(timezone.utc)
    ore_passate = 24
    campioni_ora = 4
    minuti_step = 60 // campioni_ora
    
    for i in range(ore_passate * campioni_ora):
        ts = now - timedelta(hours=ore_passate) + timedelta(minutes=minuti_step * i)
        ora_del_giorno = ts.hour + (ts.minute / 60.0)
        
        # Matematica del comportamento termodinamico e idrico
        temp = round(22.0 + 6.0 * math.sin(math.pi * (ora_del_giorno - 8) / 12) + random.gauss(0, 0.3), 2)
        hum = round(max(0, min(100, 75.0 - (temp - 20) * 3.0 + random.gauss(0, 1.5))), 2)
        ciclo_svuotamento = (ora_del_giorno % 8) / 8.0 
        soil_m = round(50.0 - (ciclo_svuotamento * 10) + random.gauss(0, 0.5), 2)
        
        payload = {
            "timestamp": ts.isoformat(),
            "temperature": temp,
            "humidity": hum,
            "soil_moisture": soil_m
        }
        
        request("POST", f"/zones/{zone_id}/telemetry", payload)


def enqueue_command(zone_id: str, command_id: str, command_type: str, payload: dict) -> bool:
    status, body = request(
        "POST",
        f"/zones/{zone_id}/commands",
        {"command_id": command_id, "command_type": command_type, "payload": payload},
    )
    if status != 201:
        print(
            f"[seed] avviso: comando {command_type} non accodato su {zone_id} "
            f"(status {status}) {body}"
        )
        return False
    print(f"[seed] comando {command_type} accodato su {zone_id} (command_id={command_id})")
    return True


def enqueue_and_wait_command(
    zone_id: str,
    command_id: str,
    command_type: str,
    payload: dict,
    *,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> str | None:
    command_payload = {
        "command_id": command_id,
        "command_type": command_type,
        "payload": payload,
    }
    deadline = time.monotonic() + timeout_seconds
    last_status: str | None = None
    last_body: dict = {}
    while time.monotonic() < deadline:
        status, body = request("POST", f"/zones/{zone_id}/commands", command_payload)
        if status != 201:
            print(
                f"[seed] avviso: comando {command_type} non accodato/rileggibile su "
                f"{zone_id} (status {status}) {body}"
            )
            return None
        last_status = body.get("status")
        last_body = body
        if last_status != "pending":
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    if last_status == "pending" or last_status is None:
        print(
            f"[seed] AVVISO: timeout ({timeout_seconds:.0f}s) in attesa dell'esito di "
            f"{command_type} (command_id={command_id}) su {zone_id}: risulta ancora "
            f"'pending' — l'Edge potrebbe non averlo ancora processato."
        )
        return None

    detail = last_body.get("result_message")
    print(
        f"[seed] comando {command_type} su {zone_id} concluso: {last_status}"
        + (f" ({detail})" if detail else "")
    )
    return last_status


def get_zone(zone_id: str) -> dict | None:
    status, body = request("GET", f"/zones/{zone_id}")
    return body if status == 200 else None


def poll_zone_until(
    zone_id: str,
    predicate,
    description: str,
    *,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> dict | None:
    deadline = time.monotonic() + timeout_seconds
    last_zone: dict | None = None
    while time.monotonic() < deadline:
        zone = get_zone(zone_id)
        if zone is not None:
            last_zone = zone
            if predicate(zone):
                return zone
        time.sleep(POLL_INTERVAL_SECONDS)
    print(
        f"[seed] AVVISO: timeout ({timeout_seconds:.0f}s) in attesa di '{description}' "
        f"su {zone_id}; ultimo stato osservato: "
        f"lifecycle_state={last_zone.get('lifecycle_state') if last_zone else '?'} "
        f"operational_state={last_zone.get('operational_state') if last_zone else '?'}"
    )
    return None


def _parse_iso(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def poll_events_until(
    zone_id: str,
    predicate,
    description: str,
    *,
    since: datetime | None = None,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> dict | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status, body = request("GET", f"/zones/{zone_id}/events?limit=200")
        if status == 200 and isinstance(body, list):
            candidates = body
            if since is not None:
                candidates = [
                    event for event in candidates
                    if "received_at" in event and _parse_iso(event["received_at"]) >= since
                ]
            match = next((event for event in candidates if predicate(event)), None)
            if match is not None:
                return match
        time.sleep(POLL_INTERVAL_SECONDS)
    print(f"[seed] AVVISO: timeout ({timeout_seconds:.0f}s) in attesa di '{description}' su {zone_id}")
    return None


def ensure_plant(plant_id: str, species: str, home_zone_id: str) -> None:
    status, body = request(
        "POST",
        "/plants",
        {"id": plant_id, "species": species, "home_zone_id": home_zone_id},
    )
    if status not in (201, 409):
        print(f"[seed] avviso: pianta {plant_id} non creata (status {status}) {body}")


def ensure_quarantine(
    plant_id: str,
    quarantine_zone_id: str,
    reason: str,
    *,
    quarantined_at: datetime | None = None,
) -> None:
    payload = {
        "is_quarantined": True,
        "quarantine_zone_id": quarantine_zone_id,
        "reason": reason,
    }
    if quarantined_at is not None:
        payload["quarantined_at"] = quarantined_at.isoformat()
    status, body = request("PATCH", f"/plants/{plant_id}/quarantine", payload)
    if status != 200:
        print(f"[seed] avviso: quarantena non applicata a {plant_id} (status {status}) {body}")
    elif quarantined_at is not None:
        print(
            f"[seed] {plant_id} spostata in quarantena, backdatata a "
            f"{quarantined_at.isoformat()} (oltre la soglia di rilascio del frontend)"
        )
    else:
        print(f"[seed] {plant_id} spostata in quarantena")


def wait_for_edge_start() -> None:
    print()
    print("=" * 78)
    print("Avvia ora l'Edge in un altro terminale con:")
    print()
    print(f"  .\\edge\\build\\bin\\Debug\\edge.exe --backend-url {BASE_URL} --edge-id {EDGE_ID}")
    print()
    print("(un solo processo edge.exe gestisce da solo tutte le zone con")
    print(f" assigned_edge_id={EDGE_ID!r}: le scopre interrogando il backend,")
    print(" non servono opzioni --zones/--zone-id.)")
    print("=" * 78)
    input("Premi INVIO qui quando è avviato... ")


def probe_edge_already_running(edge_zone_ids: list[str]) -> bool:
    print(
        f"\n[seed] verifico se un Edge è già raggiungibile e attivo "
        f"(fino a {EDGE_READY_PROBE_SECONDS:.0f}s, senza chiedere alcun input)..."
    )
    deadline = time.monotonic() + EDGE_READY_PROBE_SECONDS
    while time.monotonic() < deadline:
        for zone_id in edge_zone_ids:
            zone = get_zone(zone_id)
            if zone is not None and zone.get("lifecycle_state") == "Running":
                print(
                    f"[seed] {zone_id} è già Running: un Edge reale è già in "
                    "esecuzione e raggiungibile (probabile deploy remoto). "
                    "Procedo senza attendere alcun avvio manuale."
                )
                return True
        time.sleep(POLL_INTERVAL_SECONDS)
    print(
        "[seed] nessuna zona è diventata Running entro questa finestra breve: "
        "assumo che l'Edge non sia ancora in esecuzione (caso locale) e passo "
        "al prompt manuale."
    )
    return False


def narrate_wait(message: str, seconds: float) -> None:
    print(f"\n[seed] {message}")
    remaining = seconds
    while remaining > 0:
        tick = min(NARRATION_TICK_SECONDS, remaining)
        time.sleep(tick)
        remaining -= tick
        if remaining > 0:
            print(f"[seed]   ... ancora ~{remaining:.0f}s")


def ensure_resident_plants(zone_species: dict[str, str], edge_zone_ids: list[str]) -> None:
    print("\n[seed] --- Passo 1ter: pianta residente per ogni settore online ---")
    for zone_id in edge_zone_ids:
        species = zone_species.get(zone_id)
        if not species:
            print(f"[seed] avviso: nessuna specie nota per {zone_id}, salto la pianta residente")
            continue
        ensure_plant(f"resident-{zone_id}", species, zone_id)


def ensure_instant_quarantine_plants(zone_species: dict[str, str]) -> None:
    print(
        "\n[seed] --- Passo 1quater: piante già in quarantena "
        f"(backdate istantaneo di {QUARANTINE_BACKDATE}, illustrativo) ---"
    )
    backdate_at = datetime.now(timezone.utc) - QUARANTINE_BACKDATE
    for plant_id, home_zone in QUARANTINE_INSTANT_PLANT_SOURCES:
        species = zone_species.get(home_zone)
        if not species:
            print(f"[seed] avviso: nessuna specie nota per {home_zone}, salto {plant_id}")
            continue
        ensure_plant(plant_id, species, home_zone)
        ensure_quarantine(
            plant_id,
            "r5-s1",
            "controllo fitosanitario di routine",
            quarantined_at=backdate_at,
        )


def advance_zone_to_last_phase(zone_id: str, recipe_id: str, run_suffix: str) -> None:
    status, recipe = request("GET", f"/recipes/{recipe_id}")
    if status != 200 or not isinstance(recipe, dict) or not recipe.get("phases"):
        print(
            f"[seed] avviso: impossibile leggere le fasi della ricetta {recipe_id!r} "
            f"per {zone_id} (status {status}), salto l'avanzamento di fase"
        )
        return
    phase_names = [p["name"] for p in recipe["phases"]]
    last_phase = phase_names[-1]
    print(
        f"\n[seed] --- Avanzamento di fase su {zone_id}: "
        f"{' -> '.join(phase_names)} ---"
    )
    for step_index in range(len(phase_names) - 1):
        zone = get_zone(zone_id)
        if zone is not None and (
            zone.get("current_phase") == last_phase or zone.get("cultivation_completed")
        ):
            break
        result = enqueue_and_wait_command(
            zone_id,
            f"advance-phase-{zone_id}-{step_index}-{run_suffix}",
            "AdvanceRecipePhase",
            {},
        )
        if result != "succeeded":
            print(
                f"[seed] avviso: AdvanceRecipePhase su {zone_id} non è andato a buon "
                f"fine (esito={result!r}), mi fermo qui"
            )
            break
        zone = get_zone(zone_id)
        current = zone.get("current_phase") if zone is not None else None
        print(f"[seed] {zone_id}: fase avanzata a {current!r}")

    final_zone = get_zone(zone_id)
    if final_zone is None:
        print(f"[seed] avviso: impossibile rileggere {zone_id} per la verifica finale di fase")
    elif final_zone.get("current_phase") == last_phase:
        print(f"[seed] {zone_id} ha raggiunto l'ultima fase della ricetta: {last_phase!r}.")
    elif final_zone.get("cultivation_completed"):
        print(f"[seed] {zone_id}: coltivazione segnalata come completata (cultivation_completed=true).")
    else:
        print(
            f"[seed] avviso: {zone_id} non risulta sull'ultima fase attesa "
            f"({last_phase!r}); fase corrente osservata: "
            f"{final_zone.get('current_phase')!r}"
        )


def diagnose_actuator_snapshot(zone_id: str) -> None:
    status, snapshot = request("GET", f"/zones/{zone_id}/actuators/latest")
    if status == 200 and isinstance(snapshot, dict):
        print(
            f"[seed] {zone_id}: snapshot attuatori presente "
            f"(sequence_number={snapshot.get('sequence_number')}, "
            f"timestamp_seconds={snapshot.get('timestamp_seconds')})."
        )
        return
    zone = get_zone(zone_id)
    lifecycle = zone.get("lifecycle_state") if zone is not None else None
    if lifecycle != "Running":
        reason = (
            f"lifecycle_state={lifecycle!r}: la zona non è (ancora) Running, quindi "
            "l'Edge non ha ancora eseguito alcun ciclo di controllo su di essa — "
            "non c'è nulla da caricare su /actuators, non è un errore."
        )
    else:
        reason = (
            f"lifecycle_state=Running ma nessuno snapshot presente: può darsi sia "
            "trascorso troppo poco tempo simulato per completare un intero ciclo di "
            "controllo, o che l'outbox dell'Edge stia ancora ritentando la consegna "
            "(controlla il log del processo edge reale: tempo simulato trascorso ed "
            "eventuali errori di rete verso il backend)."
        )
    print(
        f"[seed] AVVISO: GET /zones/{zone_id}/actuators/latest è vuoto "
        f"(status {status}). Diagnosi — {reason}"
    )


def step5_degraded_demo(run_suffix: str) -> None:
    zone_id = DEGRADED_DEMO_ZONE_ID
    fault_id = f"demo-short-dropout-{zone_id}-{run_suffix}"
    since = datetime.now(timezone.utc)
    print(f"\n[seed] --- Passo 5: InjectFault reale su {zone_id} (obiettivo: Degraded) ---")
    sent = enqueue_command(
        zone_id,
        f"inject-{fault_id}",
        "InjectFault",
        {
            "fault_id": fault_id,
            "target_type": "sensor",
            "target": "soil_moisture",
            "mode": "sensor_dropout",
            "duration_seconds": SHORT_FAULT_DURATION_SECONDS,
        },
    )
    if not sent:
        print(f"[seed] avviso: fault non accodato su {zone_id}, salto la verifica del punto 6")
        return

    print(f"[seed] --- Passo 6: attendo FaultDetected + StateChanged(Degraded) su {zone_id} ---")
    fault_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "FaultDetected"
        and e.get("payload", {}).get("component") == "soil_moisture_sensor"
        and e.get("payload", {}).get("rule") == "missing_value",
        "evento FaultDetected (soil_moisture_sensor / missing_value)",
        since=since,
    )
    if fault_event is not None:
        payload = fault_event.get("payload", {})
        print(
            f"[seed] FaultDetected osservato: component={payload.get('component')} "
            f"rule={payload.get('rule')} severity={payload.get('severity')} "
            f"diagnostic={payload.get('diagnostic')!r}"
        )
    else:
        print(
            "[seed] avviso: nessun FaultDetected osservato entro il timeout — "
            "può darsi che il fault non sia stato ancora rilevato dall'Edge."
        )

    degraded_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "StateChanged"
        and e.get("payload", {}).get("current_state") == "Degraded",
        "StateChanged -> Degraded",
        since=since,
    )
    if degraded_event is not None:
        print(
            f"[seed] StateChanged osservato: "
            f"{degraded_event['payload'].get('previous_state')} -> "
            f"{degraded_event['payload'].get('current_state')}"
        )
        print(f"[seed] {zone_id} è passata a Degraded per un fault reale rilevato dall'Edge.")
    else:
        print(
            f"[seed] avviso: nessuno StateChanged verso Degraded osservato entro il "
            f"timeout su {zone_id}."
        )


def step_safety_lockdown_demo(since: datetime) -> None:
    zone_id = LOCKDOWN_SAFETY_DEMO_ZONE_ID
    print(
        f"\n[seed] --- Passo 6bis: attendo il vero lockdown da safety_range su "
        f"{zone_id} (nessun InjectFault: basta il target di fase gia' assegnato) ---"
    )

    fault_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "FaultDetected"
        and e.get("payload", {}).get("rule") == "outside_recipe_safety_range",
        "evento FaultDetected (outside_recipe_safety_range)",
        since=since,
    )
    if fault_event is None:
        print(
            f"[seed] avviso: nessun FaultDetected outside_recipe_safety_range "
            f"osservato entro il timeout su {zone_id} — puo' darsi che l'Edge "
            "non abbia ancora eseguito il primo ciclo di controllo su questa zona."
        )
        return
    payload = fault_event.get("payload", {})
    print(
        f"[seed] FaultDetected osservato: component={payload.get('component')} "
        f"severity={payload.get('severity')} diagnostic={payload.get('diagnostic')!r}"
    )
    if payload.get("severity") != "Critical":
        print(
            "[seed] avviso: severity non e' 'Critical' come atteso per una "
            "violazione di safety_range (verifica manuale consigliata)."
        )

    lockdown_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "StateChanged"
        and e.get("payload", {}).get("current_state") == "EmergencyLockdown",
        "StateChanged -> EmergencyLockdown (dopo FaultDetected safety_range)",
        since=since,
    )
    if lockdown_event is None:
        print(
            f"[seed] avviso: {zone_id} non risulta ancora in EmergencyLockdown "
            "entro il timeout, anche se il FaultDetected e' stato osservato."
        )
        return
    print(
        f"[seed] {zone_id} e' passata a EmergencyLockdown: "
        f"{lockdown_event['payload'].get('previous_state')} -> "
        f"{lockdown_event['payload'].get('current_state')} "
        "(una violazione CRITICAL di safety_range forza SEMPRE EmergencyLockdown "
        "bypassando Degraded — vedi la nota in cima al file)."
    )
    print(
        f"[seed] Verifica nella UI: apri la pagina Allarmi da amministratore "
        f"e controlla che compaia una card di allarme per {zone_id} "
        "in Situazioni attive e nel Registro eventi."
    )


def verify_state_sequence(
    zone_id: str,
    *,
    since: datetime,
    expected: list[tuple[str, str]],
) -> bool:
    status, body = request("GET", f"/zones/{zone_id}/events?limit=500")
    if status != 200 or not isinstance(body, list):
        print(f"[seed] avviso: impossibile rileggere gli eventi di {zone_id} per la verifica finale (status {status})")
        return False
    state_changes = sorted(
        (
            event for event in body
            if event.get("event_type") == "StateChanged"
            and "received_at" in event
            and _parse_iso(event["received_at"]) >= since
        ),
        key=lambda event: event["received_at"],
    )
    observed = [
        (event["payload"].get("previous_state"), event["payload"].get("current_state"))
        for event in state_changes
    ]
    expected_label = " -> ".join([expected[0][0]] + [pair[1] for pair in expected])
    if observed == expected:
        print(
            f"[seed] VERIFICA OK: sequenza StateChanged di {zone_id} esattamente "
            f"come atteso: {expected_label}"
        )
        return True
    observed_label = (
        " -> ".join([observed[0][0]] + [pair[1] for pair in observed])
        if observed else "(nessuno StateChanged osservato)"
    )
    print(
        f"[seed] VERIFICA FALLITA: sequenza StateChanged di {zone_id} diversa da "
        f"quella attesa.\n"
        f"  attesa:    {expected_label}\n"
        f"  osservata: {observed_label}"
    )
    return False


def main() -> None:
    connection = get_connection()
    try:
        init_db(connection)
        for username, password, role, display_name in SEED_ACCOUNTS:
            _upsert_user(connection, username, password, role, display_name)
    finally:
        connection.close()

    start_time = time.monotonic()
    run_suffix = str(int(time.time() * 1000))

    print(f"[seed] BASE_URL={BASE_URL!r} (SMARTHYDRO_BASE_URL per puntare altrove)")
    print("[seed] --- Passo 0: login come amministratore di seed ---")
    login_as_admin()

    print("\n[seed] --- Passo 1: registrazione zone (puro input) ---")
    by_department: dict[int, list[tuple]] = {}
    for entry in PRODUCTION_ZONES:
        by_department.setdefault(entry[2], []).append(entry)

    zone_recipe: dict[str, str] = {}
    zone_species: dict[str, str] = {}
    edge_zone_ids: list[str] = []

    for department, entries in sorted(by_department.items()):
        recipes = pick_recipes(department, len(entries))
        for (zone_id, name, dept, sector, has_edge), recipe in zip(entries, recipes):
            species = recipe["plant_type"]
            zone_species[zone_id] = species
            zone_recipe[zone_id] = recipe["id"]
            extra = {"plant_species": species, "active_recipe_id": recipe["id"]}
            if has_edge:
                extra["assigned_edge_id"] = EDGE_ID
                edge_zone_ids.append(zone_id)
            ensure_zone(zone_id, name, dept, sector, extra)

    # --- Zona dedicata al lockdown safety_range (r4-s2): DOPO il ciclo per-reparto qui
    # sopra, mai dentro — pick_recipes(4, ...) ha gia' scelto la ricetta di
    # r4-s1 dal catalogo, quindi la ricetta demo (creata solo ora) non puo'
    # mai finire assegnata per sbaglio alla zona sbagliata.
    safety_lockdown_plant_type = ensure_safety_lockdown_recipe()
    ensure_safety_lockdown_zone(safety_lockdown_plant_type)
    zone_species[LOCKDOWN_SAFETY_DEMO_ZONE_ID] = safety_lockdown_plant_type
    zone_recipe[LOCKDOWN_SAFETY_DEMO_ZONE_ID] = LOCKDOWN_SAFETY_DEMO_RECIPE_ID
    edge_zone_ids.append(LOCKDOWN_SAFETY_DEMO_ZONE_ID)
    
    safety_lockdown_since = datetime.now(timezone.utc)

    for zone_id, name, dept, sector in QUARANTINE_ZONES:
        ensure_zone(zone_id, name, dept, sector, {"plant_species": None})

    ensure_orphan_quarantine_scenario(zone_species["r3-s1"])

    ensure_resident_plants(zone_species, edge_zone_ids)
    ensure_instant_quarantine_plants(zone_species)

    print("\n[seed] --- Passo 2: ActivateCultivation (via POST /cultivations) e Iniezione Telemetria Storica ---")
    for zone_id in edge_zone_ids:
        ensure_cultivation(zone_id, zone_recipe[zone_id])
        push_historical_telemetry(zone_id)

    print(
        "\n[seed] --- Passo 8: piante e quarantena "
        f"(margine di backdate {QUARANTINE_BACKDATE} cosi' 'Fai uscire' e' gia' "
        "abilitabile; il tempo di quarantena scorre pero' con un'attesa reale, "
        f"al ritmo di 1s = {QUARANTINE_MINUTES_PER_SECOND:.0f}min, prima di "
        "registrarlo) ---"
    )
    narrate_wait(
        f"quarantena: attendo {QUARANTINE_REAL_WAIT_SECONDS:.0f}s reali "
        f"(equivalenti a {QUARANTINE_BACKDATE} di quarantena al ritmo di "
        f"1s = {QUARANTINE_MINUTES_PER_SECOND:.0f}min) prima di registrare "
        "l'inizio quarantena delle 5 piante...",
        QUARANTINE_REAL_WAIT_SECONDS,
    )
    quarantine_backdate_at = datetime.now(timezone.utc) - QUARANTINE_BACKDATE
    plant_sources = [
        ("plant-1", zone_species["r1-s1"], "r1-s1"),
        ("plant-2", zone_species["r1-s1"], "r1-s1"),
        ("plant-3", zone_species["r1-s2"], "r1-s2"),
        ("plant-4", zone_species["r2-s1"], "r2-s1"),
        ("plant-5", zone_species["r3-s1"], "r3-s1"),
    ]
    for plant_id, species, home_zone in plant_sources:
        ensure_plant(plant_id, species, home_zone)
        ensure_quarantine(
            plant_id,
            "r5-s1",
            "controllo fitosanitario di routine",
            quarantined_at=quarantine_backdate_at,
        )

    # --- Passo 3: Edge reale — salta il prompt se e' gia' raggiungibile ----
    if not probe_edge_already_running(edge_zone_ids):
        wait_for_edge_start()

    # --- Passo 4: polling lifecycle_state == Running ------------------------
    print(f"\n[seed] --- Passo 4: attendo lifecycle_state=Running (timeout {COMMAND_TIMEOUT_SECONDS:.0f}s ciascuna) ---")
    running: dict[str, bool] = {}
    for zone_id in edge_zone_ids:
        zone = poll_zone_until(
            zone_id,
            lambda z: z.get("lifecycle_state") == "Running",
            "lifecycle_state=Running",
        )
        running[zone_id] = zone is not None
        if zone is not None:
            print(f"[seed] {zone_id}: Running confermato dall'Edge reale.")

    if not all(running.values()):
        print(
            "\n[seed] AVVISO: non tutte le zone risultano Running. Le verifiche dei "
            "passi 5/6/7 procederanno solo sulle zone confermate; controlla che "
            "l'Edge sia stato avviato con l'edge-id corretto e che veda il backend."
        )

    # --- Passo 4bis: SetSimulationSpeed, ORA che ogni zona è Running -------
    print(
        f"\n[seed] --- Passo 4bis: SetSimulationSpeed (time_scale={DEV_TIME_SCALE:g}) "
        "sulle zone Running ---"
    )
    for zone_id in edge_zone_ids:
        if not running.get(zone_id):
            print(f"[seed] salto SetSimulationSpeed su {zone_id}: non è Running")
            continue
        time_scale_status = enqueue_and_wait_command(
            zone_id,
            f"set-time-scale-{zone_id}-{run_suffix}",
            "SetSimulationSpeed",
            {"time_scale": DEV_TIME_SCALE},
        )
        if time_scale_status != "succeeded":
            print(
                f"[seed] AVVISO: SetSimulationSpeed su {zone_id} non risulta "
                f"'succeeded' (esito: {time_scale_status!r}) — questa zona "
                "resterà a time_scale=1 (tempo reale): i timeout di polling dei "
                "passi successivi, calibrati assumendo time_scale=60 già "
                "attivo, potrebbero non bastare per questa zona."
            )

    # --- Passo 4ter: AdvanceRecipePhase sulle zone Nominal per tutta la ----
    for zone_id in PHASE_ADVANCE_ZONE_IDS:
        if running.get(zone_id):
            advance_zone_to_last_phase(zone_id, zone_recipe[zone_id], run_suffix)
        else:
            print(f"\n[seed] salto l'avanzamento di fase su {zone_id}: non è Running")

    # --- Passo 5 + 6: InjectFault -> Degraded (obbligatorio) ---------------
    if running.get(DEGRADED_DEMO_ZONE_ID):
        step5_degraded_demo(run_suffix)
    else:
        print(f"\n[seed] salto il passo 5/6: {DEGRADED_DEMO_ZONE_ID} non è Running")

    # --- Passo 6bis: lockdown da safety_range reale, nessun InjectFault (obbligatorio) ---
    if running.get(LOCKDOWN_SAFETY_DEMO_ZONE_ID):
        step_safety_lockdown_demo(safety_lockdown_since)
    else:
        print(
            f"\n[seed] salto il passo 6bis: {LOCKDOWN_SAFETY_DEMO_ZONE_ID} non è Running"
        )

    # --- Passo 9: diagnostica snapshot attuatori per ogni zona online ------
    print("\n[seed] --- Passo 9: verifica snapshot attuatori (GET /zones/{id}/actuators/latest) ---")
    for zone_id in edge_zone_ids:
        diagnose_actuator_snapshot(zone_id)

    # --- Riepilogo finale: stato REALE letto da GET /zones -----------------
    print("\n[seed] --- Riepilogo finale (stato reale riportato dal backend) ---")
    status, zones = request("GET", "/zones")
    if status != 200 or not isinstance(zones, list):
        print(f"[seed] avviso: impossibile leggere GET /zones (status {status})")
        return
    by_id = {z["id"]: z for z in zones}
    all_ids = (
        [z[0] for z in PRODUCTION_ZONES]
        + [LOCKDOWN_SAFETY_DEMO_ZONE_ID]
        + [z[0] for z in QUARANTINE_ZONES]
    )
    for zone_id in all_ids:
        zone = by_id.get(zone_id)
        if zone is None:
            print(f"  - {zone_id}: non trovata in GET /zones")
            continue
        print(
            f"  - {zone_id}: status={zone.get('status')} "
            f"lifecycle_state={zone.get('lifecycle_state')} "
            f"operational_state={zone.get('operational_state')} "
            f"current_phase={zone.get('current_phase')!r} "
            f"assigned_edge_id={zone.get('assigned_edge_id')!r}"
        )

    elapsed = time.monotonic() - start_time
    minimum_seconds = 5 * 60
    if elapsed < minimum_seconds:
        remaining = minimum_seconds - elapsed
        narrate_wait(
            f"la demo ha coperto tutti gli eventi principali in {elapsed:.0f}s; "
            f"resto in attesa altri {remaining:.0f}s (fino ad almeno 5 minuti "
            "reali totali) cosi' chi guarda ha tempo di ispezionare la dashboard "
            "senza che lo script termini sotto i suoi occhi",
            remaining,
        )
        elapsed = time.monotonic() - start_time
    print(
        f"\n[seed] fatto. Durata reale totale: {elapsed:.0f}s ({elapsed / 60:.1f} min). "
        "Ricorda: time_scale=60 su queste zone è solo per velocizzare questa demo — "
        "il prodotto di default lavora a time_scale=1x."
    )


if __name__ == "__main__":
    main()