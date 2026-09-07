# Test RIGOROSO — richiede l'Edge C++ reale acceso a mano.
# Usa questo prima di una consegna/esame, per verificare che il sistema
# vero (non simulato) si comporti correttamente.
# Per popolare rapidamente la dashboard durante lo sviluppo, usa invece
# demo/seed_test_scenario.py (non richiede l'Edge).

"""Popolamento SmartHydro basato ESCLUSIVAMENTE sull'Edge C++ reale.

USO: con il backend già avviato (uvicorn backend.app.main:app --reload):

    python demo/seed_dev_data.py

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
l'account amministratore di esempio (vedi demo/seed_users.py, che va
eseguito almeno una volta prima di questo script) e allega il token
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
- Reparto 1: r1-s1 (Nominal), r1-s2 (dimostrazione InjectFault -> Degraded,
  auto-recupero)
- Reparto 2: r2-s1 (Nominal), r2-s2 (OFFLINE: nessun assigned_edge_id)
- Reparto 3: r3-s1 (Nominal)
- Reparto 4: r4-s1 (dimostrazione InjectFault persistente -> Degraded ->
  EmergencyLockdown -> ResetFault + ResetEmergency -> Degraded -> Nominal)
- Reparto 5: r5-s1 (unico settore possibile per la quarantena) + 5 piante
  quarantenate

IMPORTANTE su cosa "genera" Degraded vs EmergencyLockdown nel codice reale
dell'Edge (verificato leggendo edge/src/faults/fault_detector.cpp ed
edge/src/runtime/edge_runtime_fsm.cpp, non assunto):
- Un valore che supera il safety_range della ricetta per una variabile
  fisica come soil_moisture/ph/light NON produce Degraded: produce una
  fault CRITICAL e porta IMMEDIATAMENTE a EmergencyLockdown (bypassando
  Degraded). Per questo motivo questo script non usa `sensor_offset` per
  ottenere Degraded: userebbe un valore fuori dal safety_range e salterebbe
  dritto in EmergencyLockdown, contraddicendo l'obiettivo del punto 5.
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

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Sibling module, non il pacchetto backend: Python mette la cartella dello
# script (demo/) in sys.path[0] quando lo lanci direttamente, quindi questo
# import funziona sia da `python demo/seed_dev_data.py` (dalla radice) sia
# da dentro demo/, senza bisogno di manipolare sys.path.
from seed_users import ADMIN_PASSWORD, ADMIN_USERNAME

BASE_URL = "http://127.0.0.1:8000"
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

# id, nome, department_number, sector_number, ha un Edge assegnato
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
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, (json.loads(body) if body else {})
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
    if status != 200:
        raise SystemExit(
            "[seed] impossibile autenticarsi come amministratore "
            f"({ADMIN_USERNAME!r}): status {status} {body}. Hai gia' eseguito "
            "`python demo/seed_users.py` contro questo stesso database?"
        )
    _AUTH_TOKEN = body["token"]
    print(f"[seed] autenticato come {ADMIN_USERNAME!r} (ruolo={body['role']})")


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
    run_suffix = str(int(time.time() * 1000))

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

    for zone_id, name, dept, sector in QUARANTINE_ZONES:
        ensure_zone(zone_id, name, dept, sector, {"plant_species": None})

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

    print("\n[seed] --- Passo 8: piante e quarantena (puro input, invariato) ---")
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

    # --- Passo 3: fermarsi in attesa dell'Edge reale, avviato a mano -------
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

    # --- Passo 5 + 6: InjectFault -> Degraded (obbligatorio) ---------------
    if running.get(DEGRADED_DEMO_ZONE_ID):
        step5_degraded_demo(run_suffix)
    else:
        print(f"\n[seed] salto il passo 5/6: {DEGRADED_DEMO_ZONE_ID} non è Running")

    # --- Passo 7: fault persistente -> EmergencyLockdown -> reset (opzionale) ---
    if running.get(LOCKDOWN_DEMO_ZONE_ID):
        step7_lockdown_demo(run_suffix)
    else:
        print(f"\n[seed] salto il passo 7: {LOCKDOWN_DEMO_ZONE_ID} non è Running")

    # --- Riepilogo finale: stato REALE letto da GET /zones -----------------
    print("\n[seed] --- Riepilogo finale (stato reale riportato dal backend) ---")
    status, zones = request("GET", "/zones")
    if status != 200 or not isinstance(zones, list):
        print(f"[seed] avviso: impossibile leggere GET /zones (status {status})")
        return
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
            f"operational_state={zone.get('operational_state')} "
            f"current_phase={zone.get('current_phase')!r} "
            f"assigned_edge_id={zone.get('assigned_edge_id')!r}"
        )
    print(
        "\n[seed] fatto. Ricorda: time_scale=60 su queste zone è solo per velocizzare "
        "questa demo — il prodotto di default lavora a time_scale=1x."
    )


if __name__ == "__main__":
    main()
