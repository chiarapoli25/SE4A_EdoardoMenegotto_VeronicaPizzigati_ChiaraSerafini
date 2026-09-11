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
- Reparto 3: r3-s1 (Nominal, avanzata fino all'ultima fase)
- Reparto 4: r4-s1 (dimostrazione InjectFault persistente -> Degraded ->
  EmergencyLockdown -> ResetFault + ResetEmergency -> Degraded -> Nominal,
  POI avanzata fino all'ultima fase una volta rientrata Nominal), r4-s2
  (dimostrazione lockdown per violazione di safety_range, vedi sotto; NON
  avanzata di fase)
- Reparto 5: r5-s1 (unico settore possibile per la quarantena) + 5 piante
  quarantenate, backdatate oltre la soglia di rilascio del frontend (vedi
  BACKDATING QUARANTENA sotto)

Ogni zona online (con un Edge assegnato) riceve anche una pianta residente
non quarantenata, di specie coerente con la ricetta della zona (vedi
ensure_resident_plants()): senza questo, un settore Nominal/Degraded/
EmergencyLockdown normale non avrebbe mai alcuna pianta a proprio nome, a
differenza delle 5 piante del Reparto 5 (che sono sempre e solo in
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
al momento in cui la demo parte, cosi' chi guarda non deve aspettare un
giorno reale per vedere il bottone abilitato. Il backend applica lo stesso
identico istante sia a plants.quarantined_at sia a plant_movements.
moved_at (mai solo all'uno o all'altro): i due raccontano lo stesso
evento e non devono mai divergere.

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
  (nessun reset automatico, a differenza di r4-s1): e' la terza situazione
  attiva, diversa sia da r1-s2 (fault recuperabile breve) sia da r4-s1
  (fault recuperabile persistente + reset manuale).

IMPORTANTE su cosa "genera" Degraded vs EmergencyLockdown nel codice reale
dell'Edge (verificato leggendo edge/src/faults/fault_detector.cpp ed
edge/src/runtime/edge_runtime_fsm.cpp, non assunto):
- Un valore che supera il safety_range della ricetta per una variabile
  fisica come soil_moisture/ph/light NON produce Degraded: produce una
  fault CRITICAL e porta IMMEDIATAMENTE a EmergencyLockdown (bypassando
  Degraded) — esattamente il meccanismo usato sopra per r4-s2. Per questo
  motivo r1-s2/r4-s1 sotto NON usano `sensor_offset` per ottenere Degraded:
  userebbe un valore fuori dal safety_range e salterebbe dritto in
  EmergencyLockdown, contraddicendo il loro obiettivo (Degraded).
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
solo ciclo -> Degraded -> auto-recupero. r4-s1 riceve lo stesso fault ma
persistente (nessun duration_seconds) -> Degraded -> EmergencyLockdown
dopo 3 cicli -> ResetFault + ResetEmergency -> Degraded -> Nominal.
"""

from __future__ import annotations

import copy
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Modulo non nel pacchetto backend, ora dentro demo/old_test/ (spostato li'
# insieme a seed_test_scenario.py/run_end_to_end.py). Python mette la
# cartella dello script (demo/) in sys.path[0] quando lo lanci direttamente,
# e old_test/ e' un namespace package implicito (nessun __init__.py
# necessario in Python 3), quindi questo import funziona sia da
# `python demo/seed_dev_data.py` (dalla radice) sia da dentro demo/, senza
# bisogno di manipolare sys.path.
from old_test.seed_users import ADMIN_PASSWORD, ADMIN_USERNAME

# Configurabile via variabile d'ambiente cosi' lo STESSO script, senza
# modifiche, funziona sia contro un backend locale (default) sia contro
# l'indirizzo pubblico di un deploy Railway (SMARTHYDRO_BASE_URL=https://
# <nome-servizio>.up.railway.app). Nota sull'account amministratore quando
# BASE_URL e' remoto: questo script fa solo POST /auth/login (puro HTTP),
# non puo' creare il primo account da solo (per scelta non esiste un
# endpoint HTTP di registrazione, vedi demo/old_test/seed_users.py) — su
# Railway demo/old_test/seed_users.py va eseguito UNA VOLTA dentro lo
# stesso container (es. `railway run python demo/old_test/seed_users.py`,
# cosi' scrive nello stesso
# file SQLite che il backend in esecuzione sta davvero usando), non dal tuo
# PC puntato all'URL pubblico: eseguito localmente scriverebbe in un
# database SQLite locale, sul tuo PC, completamente scollegato da quello
# del servizio remoto.
BASE_URL = os.environ.get("SMARTHYDRO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
EDGE_ID = "edge-serra-1"

# Token di sessione ottenuto da login_as_admin() e allegato da request() a
# ogni chiamata di scrittura (POST/PATCH/DELETE).
_AUTH_TOKEN: str | None = None

# Vedi nota in cima al file: SOLO per accelerare test/demo in questo
# ambiente. Il prodotto di default lavora a time_scale=1x.
DEV_TIME_SCALE = 60.0

# Quanto della durata di un ciclo di controllo simulato usare per il fault
# "un solo ciclo" (r1-s2): abbastanza per essere rilevato, abbastanza corto
# da esaurirsi prima del ciclo successivo. STEP_SECONDS_HINT documenta
# l'assunzione (l'Edge di default usa --step-seconds 900, non modificato
# dai comandi qui accodati): se avvii edge.exe con un --step-seconds
# diverso, questo valore resta comunque valido perché è ben al di sotto di
# qualunque quantum ragionevole.
STEP_SECONDS_HINT = 900.0
SHORT_FAULT_DURATION_SECONDS = 60.0

# Timeout di polling in secondi reali. Con time_scale=60 e step-seconds=900
# di default, un ciclo di controllo dura ~15s reali: 60s coprono quindi
# piu di tre cicli, margine ampio per Running/Degraded. Verificato con un
# run reale end-to-end (vedi messaggio di consegna).
COMMAND_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 2.0

DEGRADED_DEMO_ZONE_ID = "r1-s2"
LOCKDOWN_DEMO_ZONE_ID = "r4-s1"

# Quanto aspettare, SENZA chiedere alcun input, per capire se un Edge è
# già in esecuzione e raggiungibile (caso Railway: start.sh lo avvia da
# solo insieme al backend) prima di ripiegare sul prompt interattivo
# (caso locale). Margine: ~1s di discovery lato Edge (poll_zone_assignments
# ogni command_poll_milliseconds, default 1000ms) + un intero ciclo di
# controllo a DEV_TIME_SCALE già attivo (900/60 = 15s) + margine — vedi
# probe_edge_already_running().
EDGE_READY_PROBE_SECONDS = 25.0

# Zone che restano Nominal per l'intera demo (cioè non r1-s2 né la zona
# dedicata al lockdown safety_range) e vengono quindi avanzate fino all'ultima fase
# della loro ricetta con AdvanceRecipePhase. r4-s1 NON è qui: passa prima
# dalla dimostrazione Degraded/EmergencyLockdown (step7_lockdown_demo) e
# viene avanzata di fase solo DOPO essere rientrata Nominal — vedi main().
PHASE_ADVANCE_ZONE_IDS = ["r1-s1", "r2-s1", "r3-s1"]

# Le 5 piante del Passo 8 risultano già in quarantena da questo intervallo
# al momento in cui la demo parte: deve superare
# QUARANTINE_MIN_RELEASE_MS (24h) di dashboard/script.js, con un margine
# comodo per non finire sul filo per un ritardo di rete o di orologio fra
# questa macchina e chi guarda la demo.
QUARANTINE_BACKDATE = timedelta(hours=30)

# Quante volte al secondo la console viene aggiornata con lo stato di
# avanzamento durante le attese lunghe (Passo 7): puramente cosmetico, per
# chi guarda la demo dal vivo — vedi narrate_wait().
NARRATION_TICK_SECONDS = 5.0

# Zona dedicata alla dimostrazione del lockdown safety_range (vedi la nota in cima al
# file): DIVERSA da r1-s2/r4-s1, cosi' "Situazioni attive"/"Registro eventi"
# mostrano tre scenari distinti. Sta FUORI da PRODUCTION_ZONES apposta: le
# altre zone del Reparto 4 (qui solo r4-s1) prendono la loro ricetta dal
# catalogo via pick_recipes()/by_department in main(), mentre questa zona
# deve puntare esattamente a LOCKDOWN_SAFETY_DEMO_RECIPE_ID e a nessun'altra
# — tenerla fuori da quel ciclo generico evita qualunque ambiguita' su quale
# ricetta del reparto finisca su quale settore.
LOCKDOWN_SAFETY_DEMO_ZONE_ID = "r4-s2"
LOCKDOWN_SAFETY_DEMO_DEPARTMENT = 4
LOCKDOWN_SAFETY_DEMO_SECTOR = 2
LOCKDOWN_SAFETY_DEMO_RECIPE_ID = "recipe-safety-demo-g"

# id, nome, department_number, sector_number, ha un Edge assegnato
PRODUCTION_ZONES = [
    ("r1-s1", "Reparto 1 - Settore 1", 1, 1, True),
    ("r1-s2", "Reparto 1 - Settore 2", 1, 2, True),
    ("r2-s1", "Reparto 2 - Settore 1", 2, 1, True),
    ("r2-s2", "Reparto 2 - Settore 2", 2, 2, False),
    ("r3-s1", "Reparto 3 - Settore 1", 3, 1, True),
    ("r4-s1", "Reparto 4 - Settore 1", 4, 1, True),
    # r4-s2 (lockdown safety_range demo) non e' qui: vedi la nota su
    # LOCKDOWN_SAFETY_DEMO_ZONE_ID sopra e ensure_safety_lockdown_zone() sotto.
]
QUARANTINE_ZONES = [
    ("r5-s1", "Quarantena - Settore 1", 5, 1),
    # Il reparto 5 ha un solo settore fisico, sempre sector_number=1: il
    # backend ora lo impone esplicitamente (400 su qualunque altro valore),
    # quindi non esiste piu' un "r5-s2" da seedare qui.
]


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
    messaggio esplicito che login_as_admin()/enqueue_command()/ecc. sono
    pensati per mostrare — mascherando la vera causa dell'errore in mezzo a
    righe di stack trace facili da perdere, specialmente se lo script è
    invocato da un lanciatore come avvia_demo.bat."""
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
    che demo/old_test/seed_users.py sia gia' stato eseguito almeno una volta contro
    lo stesso database del backend."""
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


def ensure_safety_lockdown_recipe() -> str:
    """Crea (POST /recipes, puro input — vedi la nota LOCKDOWN "DA SICUREZZA"
    in cima al file) la ricetta con un target soil_moisture volutamente
    stretto che fa scattare un vero EmergencyLockdown per safety_range su
    LOCKDOWN_SAFETY_DEMO_ZONE_ID al primissimo ciclo. Restituisce il
    plant_type usato, cosi' ensure_safety_lockdown_zone() puo' mostrarlo
    come specie della zona senza doverlo ricalcolare.

    Copia una ricetta REALE del catalogo del reparto
    LOCKDOWN_SAFETY_DEMO_DEPARTMENT e sovrascrive SOLO il target di fase di
    soil_moisture: il controllore (selected_strategy/parameters) resta
    quello originale del catalogo, perche' tanto verrebbe comunque
    ristampato dalla Strategy globale d'impianto alla lettura (vedi la nota
    in cima al file) — non c'e' piu' alcun motivo di toccarlo.

    Chiamata SOLO dopo che il Passo 1 ha gia' assegnato le ricette di
    catalogo alle zone via pick_recipes(): questa ricetta non esiste ancora
    quando quel passo gira, quindi pick_recipes(4, ...) non puo' mai
    sceglierla per sbaglio al posto della ricetta vera di r4-s1.

    Idempotente: un 409 (RecipeVersionConflict, stessa versione gia'
    salvata da un run precedente) viene tollerato, non e' un errore."""
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
            "ricetta della demo lockdown/safety_range (serve che r4-s1 sia "
            "gia' stata registrata nel Passo 1)."
        )

    recipe = copy.deepcopy(template)
    recipe.pop("department_name", None)  # computed field, non accettato in POST
    recipe["id"] = LOCKDOWN_SAFETY_DEMO_RECIPE_ID
    recipe["version"] = 1
    plant_type = f"{template['plant_type']} (demo lockdown sicurezza)"
    recipe["plant_type"] = plant_type

    # Target soil_moisture volutamente stretto: safety_range e' solo 2 punti
    # percentuali oltre allowed_range per lato (43-57 contro 45-55). L'umidita'
    # INIZIALE del terriccio non e' campionata dentro allowed_range ma in una
    # finestra allargata di meta' della sua ampiezza per lato
    # (edge_runtime_core.cpp::environment_for_recipe(), vedi la nota in cima
    # al file) — con un margine di sicurezza cosi' stretto, un campione fuori
    # da allowed_range e' spesso ANCHE fuori da safety_range fin dal primo
    # ciclo. Il campionamento e' deterministico (seed fisso mescolato con
    # l'hash dell'id ricetta): per LOCKDOWN_SAFETY_DEMO_RECIPE_ID verificato
    # dal vivo in questa sessione che il campione cade a 81.33% (ben oltre
    # 57) — da cui l'id scelto qui sotto, NON un id "pulito" a caso.
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
    """Registra (POST /zones, puro input) la zona dedicata
    LOCKDOWN_SAFETY_DEMO_ZONE_ID con la ricetta demo. Tenuta fuori da
    PRODUCTION_ZONES/pick_recipes() apposta — vedi la nota su
    LOCKDOWN_SAFETY_DEMO_ZONE_ID."""
    ensure_zone(
        LOCKDOWN_SAFETY_DEMO_ZONE_ID,
        "Reparto 4 - Settore 2",
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


def ensure_cultivation(zone_id: str, recipe_id: str) -> None:
    """POST /cultivations crea E accoda già da sola il comando
    ActivateCultivation (vedi backend/app/features/cultivations/repository.py
    create_and_activate): non serve accodarlo di nuovo a mano con
    POST /zones/{id}/commands, sarebbe un comando duplicato e comunque
    servirebbe un cultivation_id che a questo punto non esiste ancora."""
    status, body = request(
        "POST", "/cultivations", {"zone_id": zone_id, "recipe_id": recipe_id}
    )
    if status in (201, 409):
        return
    print(f"[seed] avviso: coltivazione non creata su {zone_id} (status {status}) {body}")


def enqueue_command(zone_id: str, command_id: str, command_type: str, payload: dict) -> bool:
    """Puro input verso la coda comandi dell'Edge: nessun dato calcolato a
    mano, solo la richiesta che un chiamante legittimo può fare. Non aspetta
    l'esito: per i comandi dove l'ordine con un comando successivo conta
    (es. ResetFault prima di ResetEmergency) usa
    enqueue_and_wait_command()."""
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
    """Accoda un comando e ne attende l'ESITO reale (succeeded/rejected)
    prima di restituire il controllo, invece di limitarsi a verificare che
    sia stato accodato (status 201 = pending). Il polling ripete la STESSA
    POST (stesso command_id, stesso payload): per costruzione questo non
    riesegue il comando, restituisce solo lo stato corrente della riga già
    persistita (vedi backend/app/features/commands/repository.py
    create_command: un command_id già esistente con payload identico
    restituisce la riga esistente, qualunque sia il suo status). È quindi
    ancora puro input, non una nuova API.

    Usata soprattutto quando l'ordine tra due comandi conta (es. ResetFault
    deve essere REALMENTE applicato — non solo accodato — prima di
    ResetEmergency, altrimenti il reset manuale rischia di essere valutato
    dalla FSM mentre il detector osserva ancora il guasto e viene
    rifiutato in silenzio).

    Restituisce lo status finale ("succeeded" o "rejected"), oppure None se
    il comando non è stato accodato o se scade il timeout mentre resta
    'pending' (l'Edge non l'ha ancora processato)."""
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
    """Interroga GET /zones/{id} finché predicate(zone) è vero o scade il
    timeout. Restituisce lo stato zona che ha soddisfatto la condizione,
    oppure None se il timeout scade (non è un crash: chi chiama decide come
    riportarlo)."""
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
    """Interroga GET /zones/{id}/events finché un evento soddisfa predicate,
    o scade il timeout. Restituisce l'evento trovato oppure None.

    `since`, se passato, scarta gli eventi con `received_at` precedente:
    serve a non confondere un evento identico lasciato da un'esecuzione
    precedente dello script (rerun idempotente) con uno prodotto adesso."""
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
    """quarantined_at, se passato, backdata sia plants.quarantined_at sia
    plant_movements.moved_at allo stesso istante (vedi la nota BACKDATING
    QUARANTENA in cima al file) — puro input verso il campo opzionale
    aggiunto a PlantQuarantineUpdate apposta per questo script, non un
    accesso diretto al database."""
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
    """Verifica, SENZA chiedere alcun input, se un Edge è già in esecuzione
    e sta già scoprendo/eseguendo le zone appena registrate e attivate —
    il caso tipico di un deploy dove l'Edge parte da solo insieme al
    backend a ogni avvio del container (vedi Dockerfile/start.sh: lo
    stesso identico --edge-id EDGE_ID usato qui). Prova per
    EDGE_READY_PROBE_SECONDS: se nessuna zona diventa Running entro
    quella finestra, assume che l'Edge NON sia ancora in esecuzione (il
    caso locale, dove va ancora avviato a mano) e restituisce False senza
    aver mai bloccato lo script in attesa di un input.

    Non sostituisce il Passo 4 (polling più lungo, con timeout completo,
    per OGNI zona): questo è solo un rilevamento rapido "c'è già qualcuno
    dall'altra parte?" per decidere se mostrare il prompt oppure no."""
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
    """Aspetta `seconds` reali stampando un avanzamento ogni
    NARRATION_TICK_SECONDS: puramente cosmetico, per chi guarda la demo dal
    vivo mentre si parla — non introduce alcun dato, comando o stato, solo
    un ritmo leggibile fra una fase e la successiva della narrazione."""
    print(f"\n[seed] {message}")
    remaining = seconds
    while remaining > 0:
        tick = min(NARRATION_TICK_SECONDS, remaining)
        time.sleep(tick)
        remaining -= tick
        if remaining > 0:
            print(f"[seed]   ... ancora ~{remaining:.0f}s")


def ensure_resident_plants(zone_species: dict[str, str], edge_zone_ids: list[str]) -> None:
    """Ogni settore online (con un Edge assegnato) riceve almeno una
    pianta residente, non quarantenata, di specie coerente con la ricetta
    già assegnata alla zona (il backend rifiuta altrimenti con 400, vedi
    backend/app/features/plants/routes.py: "plant species must match the
    home zone species"). ID distinti (resident-<zone_id>) dalle 5 piante
    del Passo 8, che restano una popolazione separata dedicata solo alla
    quarantena."""
    print("\n[seed] --- Passo 1ter: pianta residente per ogni settore online ---")
    for zone_id in edge_zone_ids:
        species = zone_species.get(zone_id)
        if not species:
            print(f"[seed] avviso: nessuna specie nota per {zone_id}, salto la pianta residente")
            continue
        ensure_plant(f"resident-{zone_id}", species, zone_id)


def advance_zone_to_last_phase(zone_id: str, recipe_id: str, run_suffix: str) -> None:
    """Fa avanzare `zone_id` fino all'ultima fase della sua ricetta con
    AdvanceRecipePhase ripetuti (vedi la nota AVANZAMENTO DI FASE in cima
    al file) — mai aspettando che trascorra davvero il tempo simulato
    necessario, impraticabile per una demo di pochi minuti. Si ferma
    quando il comando risulta rejected per "already in its last phase",
    quando current_phase raggiunge l'ultima fase, o quando
    cultivation_completed diventa true (il secondo criterio richiesto,
    praticamente irraggiungibile in una demo breve: la fase finale dura
    comunque centinaia di ore anche a time_scale=60, ma il controllo resta
    qui per correttezza)."""
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
    """GET /zones/{id}/actuators/latest: se vuoto, DIAGNOSTICA il motivo
    invece di introdurre una POST manuale con dati inventati (questo
    script rimane input puro fino in fondo — vedi VINCOLI RISPETTATI). Lo
    snapshot è normalmente caricato in automatico dall'Edge reale a OGNI
    ciclo di controllo completato (edge/src/backend/http_backend_client.cpp,
    stesso evento che porta la telemetria): se manca, la causa più
    probabile è che la zona non abbia ancora completato un ciclo intero,
    non un problema di questo script."""
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
    # Il payload di FaultDetected non porta il fault_id che abbiamo scelto
    # noi (solo component/rule/severity/diagnostic, vedi
    # edge/src/backend/http_backend_client.cpp): correliamo quindi sul
    # componente/regola attesi per un sensor_dropout su soil_moisture.
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
            f"timeout su {zone_id}. Non è un errore dello script: può darsi che il "
            f"fault non fosse abbastanza severo o che l'Edge non sia ancora attivo su "
            f"questa zona."
        )


def step7_lockdown_demo(run_suffix: str) -> None:
    zone_id = LOCKDOWN_DEMO_ZONE_ID
    fault_id = f"demo-persistent-dropout-{zone_id}-{run_suffix}"
    since = datetime.now(timezone.utc)
    print(f"\n[seed] --- Passo 7 (opzionale): fault persistente su {zone_id} ---")
    print(
        "[seed] Nota: invece di tre comandi InjectFault separati e ravvicinati, "
        "invio UN solo fault persistente (senza duration_seconds): la FSM valuta "
        "la gravità una volta per ciclo di controllo, quindi se questo stesso "
        "fault resta attivo per almeno 3 cicli consecutivi l'escalation a "
        "EmergencyLockdown avviene comunque esattamente come descritto nel "
        "README ('tre guasti recuperabili consecutivi') — in modo deterministico."
    )
    sent = enqueue_command(
        zone_id,
        f"inject-{fault_id}",
        "InjectFault",
        {
            "fault_id": fault_id,
            "target_type": "sensor",
            "target": "soil_moisture",
            "mode": "sensor_dropout",
        },
    )
    if not sent:
        print(f"[seed] avviso: fault non accodato su {zone_id}, salto il resto del punto 7")
        return

    degraded_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "StateChanged"
        and e.get("payload", {}).get("current_state") == "Degraded",
        "StateChanged -> Degraded (pre-lockdown)",
        since=since,
    )
    if degraded_event is None:
        print(f"[seed] avviso: {zone_id} non è mai passata a Degraded, salto il resto del punto 7")
        return
    print(f"[seed] {zone_id} è passata a Degraded (primo ciclo col fault attivo).")

    lockdown_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "StateChanged"
        and e.get("payload", {}).get("current_state") == "EmergencyLockdown",
        "StateChanged -> EmergencyLockdown",
        since=since,
    )
    if lockdown_event is None:
        print(
            f"[seed] avviso: {zone_id} non ha raggiunto EmergencyLockdown entro il "
            f"timeout; il fault potrebbe essere scaduto o l'escalation richiede più "
            f"tempo reale di quanto previsto. Salto reset ed EmergencyLockdown."
        )
        return
    print(f"[seed] {zone_id} è entrata in EmergencyLockdown (3 cicli recuperabili consecutivi).")

    # ResetFault deve essere REALMENTE applicato (status 'succeeded', non solo
    # accodato) prima di inviare ResetEmergency: il reset manuale viene
    # valutato dalla FSM al ciclo successivo e rifiutato in silenzio se il
    # detector osserva ancora il guasto (vedi README). Per questo aspettiamo
    # l'esito di ResetFault invece di accodare i due comandi a raffica.
    print(f"[seed] invio ResetFault su {zone_id} e attendo il suo esito prima di ResetEmergency...")
    reset_fault_status = enqueue_and_wait_command(
        zone_id,
        f"reset-fault-{fault_id}",
        "ResetFault",
        {"fault_id": fault_id},
    )
    if reset_fault_status != "succeeded":
        print(
            f"[seed] avviso: ResetFault su {zone_id} non è andato a buon fine "
            f"(esito: {reset_fault_status!r}) — salto ResetEmergency perché la FSM "
            f"osserverebbe ancora il guasto e rifiuterebbe il reset manuale."
        )
        return

    reset_sent_at = datetime.now(timezone.utc)
    reset_emergency_status = enqueue_and_wait_command(
        zone_id,
        f"reset-emergency-{zone_id}-{run_suffix}",
        "ResetEmergency",
        {},
    )
    if reset_emergency_status != "succeeded":
        print(
            f"[seed] avviso: ResetEmergency su {zone_id} non è andato a buon fine "
            f"(esito: {reset_emergency_status!r}); non posso verificare il recupero."
        )
        return

    degraded_after_reset = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "StateChanged"
        and e.get("payload", {}).get("current_state") == "Degraded"
        and e.get("payload", {}).get("previous_state") == "EmergencyLockdown",
        "StateChanged -> Degraded (dopo reset manuale)",
        since=reset_sent_at,
    )
    if degraded_after_reset is None:
        print(
            f"[seed] avviso: {zone_id} non risulta rientrata in Degraded dopo il reset "
            f"manuale entro il timeout, anche se ResetEmergency è stato accettato dal "
            f"comando — la FSM applica la transizione al ciclo di controllo successivo."
        )
        return
    print(
        f"[seed] {zone_id} è rientrata in Degraded dopo il reset manuale "
        f"({degraded_after_reset['payload'].get('previous_state')} -> "
        f"{degraded_after_reset['payload'].get('current_state')})."
    )

    nominal_event = poll_events_until(
        zone_id,
        lambda e: e.get("event_type") == "StateChanged"
        and e.get("payload", {}).get("current_state") == "Nominal"
        and e.get("payload", {}).get("previous_state") == "Degraded",
        "StateChanged -> Nominal (recupero automatico dopo reset)",
        since=reset_sent_at,
    )
    if nominal_event is not None:
        print(f"[seed] {zone_id} è tornata Nominal automaticamente dopo qualche ciclo sano.")
    else:
        print(
            f"[seed] avviso: {zone_id} non è ancora tornata Nominal entro il timeout; "
            f"il recupero automatico potrebbe richiedere ancora qualche ciclo (prova a "
            f"controllare GET /zones/{zone_id} tra poco)."
        )

    # --- Verifica finale: la sequenza ESATTA e ORDINATA degli StateChanged,
    # non solo la presenza isolata di ciascuno stato osservata sopra passo
    # per passo (utile per il progresso a video, ma non basta da sola: uno
    # stato mancante o fuori ordine potrebbe comunque passare inosservato
    # se si guarda solo "esiste un evento Degraded da qualche parte"). ---
    verify_state_sequence(
        zone_id,
        since=since,
        expected=[
            ("Nominal", "Degraded"),
            ("Degraded", "EmergencyLockdown"),
            ("EmergencyLockdown", "Degraded"),
            ("Degraded", "Nominal"),
        ],
    )


def step_safety_lockdown_demo(since: datetime) -> None:
    """Attende il vero EmergencyLockdown per violazione di safety_range su
    LOCKDOWN_SAFETY_DEMO_ZONE_ID (vedi la nota LOCKDOWN "DA SICUREZZA" in
    cima al file e ensure_safety_lockdown_recipe()): nessun InjectFault qui,
    il target di fase gia' assegnato alla zona basta da solo a farlo
    scattare al primo ciclo di controllo reale dell'Edge.

    A differenza di step5_degraded_demo/step7_lockdown_demo non serve
    accodare alcun comando: si limita a osservare cosa fa l'Edge reale non
    appena la zona e' Running, esattamente come richiesto ("verifica dal
    vivo... non solo che l'evento esista nel database" — qui verifichiamo
    prima l'evento via API, la verifica nella UI della dashboard e'
    responsabilita' di chi esegue lo script dal vivo, vedi il messaggio
    finale stampato sotto).

    `since` va catturato PRIMA di wait_for_edge_start() (non qui dentro):
    a differenza di step5/step7, che inviano loro stessi il comando che fa
    scattare la transizione (quindi "since=adesso" e' sempre corretto),
    qui non c'e' alcun comando da inviare — il primo ciclo di controllo
    dell'Edge reale potrebbe gia' avere fatto scattare il lockdown PRIMA
    che questa funzione venga chiamata (es. durante il polling Running del
    Passo 4 o durante step5/step7), quindi filtrare da un "since" preso solo
    ora rischierebbe di scartare l'evento vero e segnalare un falso
    avviso."""
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
    """Legge TUTTI gli eventi StateChanged della zona da `since` in poi,
    li ordina cronologicamente e confronta la sequenza di transizioni
    (previous_state, current_state) con `expected`, elemento per elemento
    e nell'ordine esatto — non si limita a controllare che ogni stato sia
    presente da qualche parte nella cronologia. Stampa un esito chiaro,
    con la sequenza osservata per intero se non corrisponde."""
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
    # mai finire assegnata per sbaglio alla zona sbagliata. Vedi la nota
    # IMPORTANTE in cima al file e i docstring delle due funzioni.
    safety_lockdown_plant_type = ensure_safety_lockdown_recipe()
    ensure_safety_lockdown_zone(safety_lockdown_plant_type)
    zone_species[LOCKDOWN_SAFETY_DEMO_ZONE_ID] = safety_lockdown_plant_type
    zone_recipe[LOCKDOWN_SAFETY_DEMO_ZONE_ID] = LOCKDOWN_SAFETY_DEMO_RECIPE_ID
    edge_zone_ids.append(LOCKDOWN_SAFETY_DEMO_ZONE_ID)
    # Catturato QUI, non dentro step_safety_lockdown_demo(): vedi il
    # docstring di quella funzione sul perche' non si puo' aspettare fino a
    # quando viene chiamata (dopo step5/step7).
    safety_lockdown_since = datetime.now(timezone.utc)

    for zone_id, name, dept, sector in QUARANTINE_ZONES:
        ensure_zone(zone_id, name, dept, sector, {"plant_species": None})

    ensure_resident_plants(zone_species, edge_zone_ids)

    print("\n[seed] --- Passo 2: ActivateCultivation (via POST /cultivations) ---")
    for zone_id in edge_zone_ids:
        ensure_cultivation(zone_id, zone_recipe[zone_id])
    # SetSimulationSpeed (Passo 2bis) NON viene più accodato qui: vedi il
    # Passo 4bis più sotto, dopo la conferma lifecycle_state=Running.
    # Motivo (verificato leggendo il codice, non assunto): accodarlo subito
    # dopo POST /cultivations, PRIMA che l'Edge reale sia anche solo avviato
    # (wait_for_edge_start() arriva più avanti in questo script), rendeva
    # l'esecuzione dipendente da come l'Edge processa in un colpo solo, al
    # primo poll, sia la scoperta della zona sia i comandi già in coda
    # (edge/src/backend/http_backend_client.cpp poll_zone_assignments() +
    # poll_commands() nello stesso tick, edge/src/main.cpp la stessa
    # iterazione del loop principale) — E soprattutto usava un command_id
    # fisso (senza suffisso di run): su un rerun dello script, la POST con
    # lo stesso command_id+payload non crea un nuovo comando ma restituisce
    # SEMPRE la riga già esistente in DB, qualunque sia il suo status
    # (backend/app/features/commands/repository.py create_command) — e
    # enqueue_command() stampa "accodato" guardando solo lo status HTTP
    # (201, identico sia per un comando nuovo sia per la rilettura di uno
    # vecchio), MAI il campo status del comando restituito. Risultato: se
    # anche una sola volta, in passato, quel comando fosse stato rifiutato
    # (es. perché la zona non aveva ancora raggiunto Running quando fu
    # processato), ogni rerun successivo dello script continuerebbe a
    # rileggere silenziosamente quello stesso rifiuto per sempre, mostrando
    # comunque "accodato" — esattamente il sintomo osservato (log
    # "accodato" per ogni zona, ma time_scale rimasto a 1 su tutte).

    print(
        "\n[seed] --- Passo 8: piante e quarantena (invariato nel meccanismo; "
        f"backdatate di {QUARANTINE_BACKDATE} cosi' 'Fai uscire' e' gia' abilitabile) ---"
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
    # (vedi la nota nel Passo 2 sul perché non viene più accodato prima).
    # set_time_scale() lato Edge (edge/src/runtime/greenhouse_manager.cpp)
    # rifiuta esplicitamente il comando finché lifecycle_state non è Running
    # o Paused: accodarlo solo ora, con command_id univoco per questo run
    # (grazie a run_suffix) e attendendone davvero l'esito con
    # enqueue_and_wait_command() invece del solo status HTTP 201, elimina
    # sia la finestra di rifiuto sia il rischio di rileggere in silenzio lo
    # stato di un comando di un run precedente.
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
    # demo (vedi la nota AVANZAMENTO DI FASE in cima al file). Fatto qui,
    # subito dopo che time_scale e' gia' attivo su tutte, cosi' questi
    # eventi (avanzamenti di fase) compaiono ben dentro i primi 3 minuti
    # richiesti, prima ancora dei fault dimostrativi piu' sotto.
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

    # --- Passo 7: fault persistente -> EmergencyLockdown -> reset (opzionale) ---
    if running.get(LOCKDOWN_DEMO_ZONE_ID):
        step7_lockdown_demo(run_suffix)
        # Avanzamento di fase su r4-s1 SOLO ora, a valle del recupero: prima
        # (durante Degraded/EmergencyLockdown) non avrebbe senso dimostrare
        # una progressione di coltivazione "normale" su una zona che sta
        # ancora vivendo lo scenario di guasto.
        advance_zone_to_last_phase(
            LOCKDOWN_DEMO_ZONE_ID, zone_recipe[LOCKDOWN_DEMO_ZONE_ID], run_suffix
        )
    else:
        print(f"\n[seed] salto il passo 7: {LOCKDOWN_DEMO_ZONE_ID} non è Running")

    # --- Passo 6bis: lockdown da safety_range reale, nessun InjectFault (obbligatorio) ---
    if running.get(LOCKDOWN_SAFETY_DEMO_ZONE_ID):
        step_safety_lockdown_demo(safety_lockdown_since)
    else:
        print(
            f"\n[seed] salto il passo 6bis: {LOCKDOWN_SAFETY_DEMO_ZONE_ID} non è Running"
        )

    # --- Passo 9: diagnostica snapshot attuatori per ogni zona online ------
    # (vedi diagnose_actuator_snapshot(): mai una POST manuale, solo lettura
    # + diagnosi se manca).
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
