# SmartHydro

SmartHydro e un progetto per il monitoraggio e il controllo di una coltivazione
in terriccio. Il repository comprende un Edge Controller multi-zona in C++17,
un backend HTTP in Python, un catalogo persistente di ricette multifase, una
dashboard statica e uno scenario end-to-end automatizzato.

L'area Edge include un simulatore dinamico della serra, sensori con errori
strumentali, attuatori e un sistema di controllo configurabile basato su
ricette versionate e pattern Strategy. Edge e backend comunicano tramite API
HTTP versionate, con consegna asincrona, outbox persistente, retry, comandi
remoti e autenticazione Bearer opzionale. Non sono ancora presenti dispositivi
reali o Docker.

Il nome SmartHydro viene conservato come nome del progetto, ma il sistema non
e idroponico. Il contratto formale delle grandezze e del bilancio fisico e
descritto in [`doc/domain_model.md`](doc/domain_model.md). In sintesi:

- umidita del terriccio, PPFD, pH dell'acqua interstiziale e disponibilita
  stimata di N/P/K sono le sei variabili controllate;
- temperatura e umidita relativa dell'aria sono solamente osservate;
- l'umidita deriva da una sonda capacitiva simulata; una sonda resistiva misura
  la EC apparente, poi corretta per stimare EC e fertilizzante nella zona radicale;
- non esistono tre sensori fisici N/P/K: le quote sono ricavate dalla
  concentrazione totale e dalla composizione nota al modello.

## Struttura del progetto

```text
.
|-- backend/       Backend FastAPI e test automatici
|-- config/        Ricette e configurazioni di esempio
|-- dashboard/     Dashboard statica HTML, CSS e JavaScript
|-- demo/          Scenario end-to-end Edge-backend ripetibile
|-- doc/           Documentazione di progetto e contratto del dominio
|-- edge/          Edge Controller C++17 compilato con CMake
|-- .gitignore
`-- README.md
```

## Prerequisiti

- CMake 3.16 o successivo
- Un compilatore compatibile con C++17
- libcurl con header di sviluppo
- gnuplot (facoltativo per l'Edge, necessario per i grafici degli experiments)
- Python 3.10 o successivo
- Un browser web moderno

Tutti i comandi seguenti devono essere eseguiti dalla radice del repository.

## Demo end-to-end automatizzata

Dopo aver creato l'ambiente Python e installato le dipendenze del backend come
descritto piu avanti, l'intero scenario Edge-backend si esegue con un comando:

```bash
.venv/bin/python demo/run_end_to_end.py
```

La demo configura e compila l'Edge, avvia un backend con database temporaneo e
verifica automaticamente due zone con ricette diverse, telemetria, cambio
Strategy, cambio fase, fault recuperabile, ritorno a `Nominal` e rimozione di
una zona dall'Edge. In caso di successo termina entrambi i processi e rimuove
gli artefatti temporanei; in caso di errore conserva database, outbox e log e
ne stampa il percorso. `--keep-artifacts` conserva gli artefatti anche dopo un
successo, mentre `--skip-build` riusa `edge/build/bin/edge`.

Lo scenario modificabile e in `demo/end_to_end_scenario.json`; il comportamento
atteso e documentato in `demo/README.md`. Non serve un reset manuale tra due
esecuzioni, perche ogni avvio crea un database e una cache Edge nuovi.

## Compilazione dell'Edge Controller

```bash
cmake -S edge -B edge/build
cmake --build edge/build
ctest --test-dir edge/build --output-on-failure
./edge/build/bin/edge
```

L'eseguibile principale non carica una ricetta locale. Si collega al backend
usando `--edge-id` e puo avviarsi senza conoscere in anticipo il numero delle
zone. Il backend assegna i settori all'Edge; il servizio sincronizza
`GET /api/v1/edges/{edge_id}/zones` e registra dinamicamente ogni nuova zona
come inattiva. Una zona inattiva non possiede ancora un `EdgeRuntime`: sensori,
ambiente e attuatori vengono creati soltanto dopo un comando
`ActivateCultivation` valido.

Avvio normale, con provisioning gestito dal backend:

```bash
./edge/build/bin/edge --edge-id edge-serra-1
```

`--zones` e `--zone-id` restano disponibili per demo e fallback locali:

```bash
./edge/build/bin/edge \
  --zone-id r1-s1 \
  --zone-id r1-s2 \
  --backend-url http://127.0.0.1:8000
```

Quando una coltivazione e attiva, ogni ciclo:

1. legge i sensori fisici e le stime del modello N/P/K;
2. calcola i comandi tramite `RecipeControlSystem`;
3. applica i comandi sicuri a pompa, lampade e valvole;
4. fa avanzare l'ambiente e aggiorna lo storico delle dosi;
5. produce un campione progressivo con stato operativo ed eventuali eventi.

Lo stato iniziale e `Nominal`. Un errore transitorio di sensore o modello porta
il runtime in `Degraded`: viene isolato il solo controllo dipendente dal canale
guasto e i controlli indipendenti continuano a operare. Tre cicli sani
consecutivi riportano automaticamente il sistema in `Nominal`; tre guasti
recuperabili consecutivi lo portano invece in `EmergencyLockdown`.

Valori fuori dai limiti di sicurezza, errori interni del controllore e comandi
fisici rifiutati causano immediatamente `EmergencyLockdown`. Per uscirne occorre
chiamare `request_manual_reset()`: dopo un campione sano il runtime passa a
`Degraded` e ripete la verifica prima di riabilitare gli attuatori. Le soglie
sono configurabili tramite `OperationalStatePolicy`.

Ogni cambio produce `OperationalStateChanged` con stato precedente, nuovo
stato e causa. L'ingresso in emergenza produce anche
`EmergencyLockdownEntered`. I normali vincoli di dose bloccano invece soltanto
il comando interessato.

Il primo ciclo produce `RuntimeStarted`, mentre ogni passaggio automatico o
manuale di fase produce `RecipePhaseChanged`. Quando termina anche la durata
dell'ultima fase, l'Edge pubblica una sola volta `RecipeCompleted`, mantiene
l'ultima fase attiva e imposta `recipe_completed=true`: la sequenza non torna
mai automaticamente alla prima fase. `--step-seconds` definisce il quantum
fisso del controllo simulato; il valore predefinito e 900 secondi. Lo scheduler
misura il tempo reale con `std::chrono::steady_clock` e, per ogni zona, accumula
il tempo simulato moltiplicandolo per la velocita configurata. Il polling dei
comandi resta indipendente e continua anche mentre tutte le zone sono
inattive.

### Adapter e hardware

`EdgeRuntime` dipende dalle interfacce `ISensor`, `IActuator` e `IEnvironment`,
non dai simulatori concreti. Il costruttore normale crea automaticamente gli
adapter simulati per temperatura, umidita dell'aria, sonda capacitiva del
substrato, sonda resistiva di conducibilita, pH, luce, pompa, lampade, valvole
e ambiente.

Un secondo costruttore accetta gli adapter tramite `std::unique_ptr`. Un futuro
driver GPIO, Modbus o MQTT puo quindi implementare le stesse interfacce ed
essere inserito senza cambiare `EdgeRuntime`, `RecipeControlSystem` o gli
algoritmi delle Strategy. I sei adapter sensore simulati condividono un
campione sincronizzato per ogni tick.

### Struttura dei sorgenti Edge

Header pubblici e implementazioni sono raggruppati negli stessi domini:

```text
edge/
├── include/smarthydro/
│   ├── adapters/    interfacce e adapter
│   ├── control/     Strategy e controllo della ricetta
│   ├── events/      EventBus e observer
│   ├── faults/      iniezione e rilevamento separati dei guasti
│   ├── recipes/     caricamento delle ricette
│   ├── runtime/     runtime, FSM, comandi e gestione multi-zona
│   └── simulation/  simulatori
└── src/             implementazioni negli stessi domini
```

`EdgeRuntime` mantiene una sola API pubblica, ma la sua implementazione e
separata in `core`, `configuration`, `cycle`, `fsm`, `actuation` ed `events`.
I DTO del ciclo e della FSM sono dichiarati in
`smarthydro/runtime/edge_runtime_types.hpp`, mentre
`smarthydro/runtime/edge_runtime.hpp` contiene l'orchestratore. Gli header si
includono indicando il dominio, per esempio
`#include <smarthydro/runtime/edge_runtime.hpp>`.

### Multi-zona

`ZoneController` puo essere registrato senza ricetta. In stato inattivo non
possiede runtime o attuatori e viene ignorato da `step_all()`. Il comando
`ActivateCultivation` crea atomicamente ambiente, sensori, attuatori, ricetta,
controllori, FSM, storico e sequenze, quindi conferma le sei configurazioni
validate. Ogni zona mantiene una cache dei comandi indipendente dalle altre.

Il lifecycle applicativo di ogni zona e indipendente:

- `Idle`: nessuna coltivazione e nessun runtime;
- `Starting`: ricetta in validazione e runtime in costruzione;
- `Running`: controllo e simulazione attivi;
- `Paused`: runtime conservato, tempo fermo e attuatori spenti;
- `Stopping`: arresto sicuro e rilascio del runtime;
- `Error`: attivazione fallita, con diagnostica disponibile.

La FSM `Nominal`, `Degraded`, `EmergencyLockdown` rimane interna a
`EdgeRuntime` e descrive la sicurezza operativa soltanto mentre il lifecycle e
`Running`. `step_all()` ignora sia le zone inattive sia quelle in pausa.

Ogni zona attiva parte a `1x` e puo ricevere `SetSimulationSpeed` con un valore
finito fra `1x` e `60x`. `0x` viene rappresentato dal comando
`PauseCultivation`: la pausa conserva il residuo temporale senza accumulare
altro tempo, mentre stop, errore e nuova attivazione azzerano lo stato dello
scheduler. La velocita cambia la frequenza dei passi, non la durata passata a
`runtime.step()`, salvo l'ultimo passo ridotto necessario a rispettare
esattamente un limite temporale.

`SetSimulationDuration` permette di configurare una finestra futura espressa
in secondi simulati. La durata deve essere positiva e finita e decorre dal
timestamp simulato gia applicato alla zona; `duration_seconds: null` rimuove
il limite e mantiene la simulazione continua. Al raggiungimento del target lo
scheduler azzera il tempo eccedente, spegne gli attuatori e porta la zona in
`Paused`. Per ripartire occorre impostare una nuova durata oppure rimuovere il
limite prima di inviare `ResumeCultivation`.

Il recupero del tempo arretrato esegue piccoli passi in round-robin fra le
zone. Il limite globale predefinito e 8 passi per iterazione, configurabile con
`--max-catch-up-steps`; il residuo non viene scartato e l'ingresso e l'uscita
dallo stato di ritardo producono eventi diagnostici.

`GreenhouseManager` registra piu zone e permette di avanzarne una con
`step_zone()` oppure tutte con `step_all()`. Soltanto l'`EventBus` viene
condiviso; ogni evento mantiene il relativo `zone_id`. Gli identificatori
possono descrivere reparti e settori, ad esempio `reparto-a/settore-nord`.

L'eseguibile accetta comunque `--zones N` oppure piu opzioni `--zone-id`; per
registrare localmente due zone inattive:

```bash
./edge/build/bin/edge --zones 2
```

### Observer ed EventBus

`EventBus` distribuisce gli eventi dell'Edge senza rendere `EdgeRuntime`
dipendente da console, file o rete. Il runtime pubblica automaticamente:

- `TelemetrySample`;
- `StateChanged` e `EmergencyTriggered`;
- `RecipePhaseChanged`;
- `RecipeCompleted`;
- `SimulationSpeedChanged`, `SimulationDurationChanged`,
  `SimulationDurationCompleted` e `SchedulerLagStateChanged`;
- `CommandExecuted` e `CommandFailed`.

Il contratto include anche `FaultDetected`, `StrategyChanged` e
`BackendUnavailable`. `ConsoleLogger` stampa gli eventi e `CsvLogger` li salva
in un CSV uniforme. `HttpBackendClient` serializza telemetria, attuatori ed
eventi in JSON e li invia in un worker dedicato: `EventBus::publish()` non
esegue richieste di rete. Prima dell'invio ogni messaggio viene salvato
nell'outbox; errori e timeout producono retry con backoff esponenziale senza
interrompere il controllo locale.

### Collegamento HTTP al backend

L'Edge usa `http://127.0.0.1:8000` come backend predefinito. Per scegliere un
altro endpoint:

```bash
./edge/build/bin/edge \
  --backend-url http://127.0.0.1:8000 \
  --edge-id edge-serra-1 \
  --outbox-path edge-data/outbox \
  --command-poll-ms 1000
```

Il client esegue `POST` di telemetria, snapshot degli attuatori ed eventi,
sincronizza le zone assegnate, interroga la coda comandi con `GET` e invia
l'esito di ogni comando. Le assegnazioni apprese vengono salvate in
`assigned-zones.json` nella directory dell'outbox: un riavvio con backend
offline ripristina quindi le zone gia note.

La discovery esegue una riconciliazione completa a ogni polling valido: valida
l'intero manifesto restituito da `GET /api/v1/edges/{edge_id}/zones`, calcola
aggiunte e rimozioni rispetto all'insieme locale e persiste la nuova lista
prima di applicarla. Una zona rimossa viene decommissionata: attuatori spenti,
runtime e backlog dello scheduler eliminati, comandi pendenti scartati e ID
rimosso dal manager e da `assigned-zones.json`. Dopo la rimozione l'Edge non
accetta piu comandi per quell'ID.

Una risposta HTTP non 2xx, un JSON malformato o un manifesto non valido non
sono mai interpretati come lista vuota: in questi casi insieme locale e cache
restano invariati. Soltanto una risposta 2xx contenente un array valido, anche
se realmente vuoto, puo autorizzare una rimozione.

I progressivi sono idempotenti per
`(zone_id, boot_id, sequence_number)`; gli eventi e i comandi hanno un
identificativo idempotente proprio. I file dell'outbox vengono riletti al
riavvio e rimossi soltanto dopo una risposta HTTP 2xx. I comandi `LoadRecipe` e
`ActivateCultivation` possono includere soltanto `recipe_id`: il worker scarica
la ricetta validata con
`GET /api/v1/recipes/{recipe_id}` prima dell'esecuzione. Per l'attivazione il
payload deve includere anche `cultivation_id`.

Ogni `TelemetrySample` e uno snapshot atomico dello stato della zona. Oltre ai
canali ambientali contiene le stime N/P/K, l'EC apparente e corretta del
terriccio, `active_recipe_id`, `active_recipe_version`, `current_phase`,
`operational_state`, `lifecycle_state`, `current_strategies`,
`current_setpoints` e `time_scale`. Strategie e setpoint sono oggetti con le
sei chiavi `soil_moisture`, `light`, `ph`, `nitrogen`, `phosphorus` e
`potassium`.

Il backend conserva lo storico completo in `telemetry_samples`, ma aggiorna
anche direttamente la proiezione corrente nella riga `zones`. La lettura di
una zona non ricostruisce quindi ricetta, fase o FSM scorrendo `edge_events`.
Un campione arrivato in ritardo viene archiviato senza sovrascrivere la
proiezione prodotta da un campione cronologicamente piu recente.

`status` e calcolato da `last_edge_contact`: la zona e `online` soltanto se il
contatto rientra nella soglia configurata. Il valore predefinito e 60 secondi
e si modifica prima dell'avvio del backend:

```bash
export SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS=90
```

Il ricalcolo viene applicato durante le letture delle zone e da uno sweep
asincrono avviato con FastAPI, quindi l'offline viene persistito anche senza
richieste API. Telemetria, attuatori ed eventi validi aggiornano immediatamente
`last_edge_contact`. Gli eventi continuano a essere memorizzati come audit
storico e aggiornano la stessa proiezione quando rappresentano transizioni
immediate, per esempio pausa, errore, cambio Strategy o cambio velocita.

Per creare dal backend un settore assegnato all'Edge:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/zones \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "r1-s1",
    "name": "Piante Tropicali e da Fogliame - Settore 1",
    "department_number": 1,
    "sector_number": 1,
    "plant_species": "Calathea",
    "active_recipe_id": "recipe-calathea",
    "assigned_edge_id": "edge-serra-1"
  }'
```

La serra comprende quattro reparti produttivi e un quinto reparto nel quale
vengono spostate le piante in quarantena. I reparti da 1 a 4 dichiarano una
sola `plant_species`; il reparto 5 usa `plant_species: null`, perche puo
accogliere contemporaneamente esemplari di specie diverse. Ogni risposta zona
espone anche `department_name`, calcolato dal numero con questa configurazione
fissa:

1. `Piante Tropicali e da Fogliame`;
2. `Piante da Fiore`;
3. `Piante Grasse e Succulente`;
4. `Piante da Frutto e Ortaggi`;
5. `Quarantena`.

Il quinto nome descrive soltanto la destinazione fisica: la quarantena rimane
il flag temporaneo `is_quarantined` della singola pianta.

Per registrare uno dei settori misti di quarantena:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/zones \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "quarantena-1",
    "name": "Quarantena - Settore 1",
    "department_number": 5,
    "sector_number": 1,
    "plant_species": null
  }'
```

I dati configurabili di un settore si aggiornano in modo parziale con
`PATCH /api/v1/zones/{zone_id}`. Sono ammessi soltanto `name`,
`plant_species`, `assigned_edge_id`, `active_recipe_id` e
`administrative_status` (`active`, `inactive` oppure `maintenance`):

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/zones/r1-s1 \
  -H 'Content-Type: application/json' \
  -d '{
    "assigned_edge_id": "edge-serra-2",
    "administrative_status": "maintenance"
  }'
```

La ricetta deve essere gia presente nel backend; la specie non puo diventare
incompatibile con le piante registrate; il reparto 5 continua a non avere una
specie unica. Una zona la cui proiezione `lifecycle_state` e `Running`
deve essere arrestata o messa in pausa prima di cambiare Edge.

La quarantena e una proprieta della singola pianta, non del settore. Ogni
esemplare conserva specie, settore di origine, settore corrente e il flag
`is_quarantined`. Prima si registra la pianta nel reparto produttivo:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/plants \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "pomodoro-001",
    "species": "Pomodoro",
    "home_zone_id": "r1-s1"
  }'
```

Impostando il flag, il backend sposta la pianta nel reparto 5 e registra il
movimento. Impostandolo nuovamente a `false`, la pianta torna nel settore di
origine:

```bash
curl -X PATCH \
  http://127.0.0.1:8000/api/v1/plants/pomodoro-001/quarantine \
  -H 'Content-Type: application/json' \
  -d '{
    "is_quarantined": true,
    "quarantine_zone_id": "quarantena-1",
    "reason": "foglie con sintomi sospetti"
  }'
```

Per proteggere le API versionate, impostare lo stesso token nei processi
backend ed Edge:

```bash
export SMARTHYDRO_API_TOKEN='scegliere-un-segreto'
uvicorn backend.app.main:app
```

L'Edge legge il token dalla variabile e invia
`Authorization: Bearer <token>`. Se la variabile non e impostata,
l'autenticazione resta disattivata. Il polling viene eseguito dal worker HTTP
e rimane attivo indipendentemente dall'intervallo dei cicli agronomici.

### Comandi runtime

`RuntimeCommandProcessor` e il punto di ingresso idempotente dei comandi
operativi. Ogni richiesta contiene un `command_id` e un payload tipizzato:

- cambio della Strategy;
- caricamento di una nuova versione della ricetta;
- attivazione di una coltivazione in una zona inattiva;
- pausa, ripresa e arresto di una coltivazione;
- conferma o rifiuto di una configurazione;
- simulazione tipizzata e reset delle anomalie di sensori e attuatori;
- avanzamento forzato della fase;
- arresto di emergenza e richiesta di reset da `EmergencyLockdown`.

Il primo esito, positivo o negativo, viene memorizzato. Un retry con lo stesso
`command_id` non riesegue il comando e restituisce lo stesso risultato con
`replayed=true`. Il processore converte inoltre gli errori di validazione in un
`RuntimeCommandResult` rifiutato, evitando di propagare eccezioni al futuro
trasporto HTTP o MQTT.

### Simulazione e gestione delle anomalie

L'utente non sceglie direttamente la gravita o lo stato della FSM. Invia invece
un comando `InjectFault` che descrive un componente e un comportamento fisico
anomalo. La gestione e divisa in tre componenti indipendenti:

1. `FaultInjector` altera soltanto letture o uscite della simulazione; non
   assegna severita, non pubblica eventi e non modifica la FSM;
2. `FaultDetector` osserva letture, comandi e uscite senza modificarli e produce
   evidenze `DetectedFault`;
3. la FSM operativa consuma esclusivamente le evidenze e applica la politica di
   `Degraded`, escalation, recovery e lockdown.

Il detector non conosce ID, durata o modalita dei fault iniettati: la stessa
anomalia proveniente da un adapter hardware viene quindi trattata nello stesso
modo di quella simulata.

Esempio: offset di pH attivo per 30 minuti simulati:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/zones/zone-1/commands \
  -H 'Content-Type: application/json' \
  -d '{
    "command_id": "fault-ph-offset-1",
    "command_type": "InjectFault",
    "payload": {
      "fault_id": "temporary-ph-offset",
      "target_type": "sensor",
      "target": "ph",
      "mode": "sensor_offset",
      "value": 0.4,
      "duration_seconds": 1800
    }
  }'
```

Esempio: illuminazione bloccata accesa fino al reset esplicito:

```json
{
  "command_id": "fault-lighting-1",
  "command_type": "InjectFault",
  "payload": {
    "fault_id": "lighting-stuck-on",
    "target_type": "actuator",
    "target": "lighting",
    "mode": "actuator_stuck_on"
  }
}
```

I target sensore sono `temperature`, `air_humidity`, `soil_moisture`,
`soil_conductivity`, `ph` e `light`. I target attuatore sono `water_pump`, `lighting`,
`nitrogen_valve`, `phosphorus_valve`, `potassium_valve`, `ph_up_valve` e
`ph_down_valve`. Le modalita supportate sono:

| Componente | Modalita | Uso di `value` |
| --- | --- | --- |
| Sensore | `sensor_dropout` | nessuno |
| Sensore | `sensor_stuck` | valore congelato opzionale; senza valore usa la prima lettura |
| Sensore | `sensor_offset` | offset additivo obbligatorio |
| Attuatore | `actuator_stuck_off` | nessuno |
| Attuatore | `actuator_stuck_on` | nessuno |
| Attuatore | `actuator_slow_response` | fattore obbligatorio strettamente fra 0 e 1 |

Le regole osservazionali minime sono:

| Regola | Componente | Comportamento |
| --- | --- | --- |
| `missing_value` | sensore o valore derivato | scatta dopo `missing_cycles` consecutivi |
| `outside_physical_range` | sensore o modello | valore impossibile rispetto ai limiti fisici |
| `maximum_rate_exceeded` | sensore o modello | variazione/secondo superiore alla politica del canale |
| `frozen_value` | sensore abilitato | valore invariato per `frozen_cycles` |
| `model_limit_incompatible` | N/P/K | stima esterna ai limiti della fase della ricetta |
| `non_finite_model_value` | N/P/K | stima NaN o infinita |
| `commanded_without_response` | attuatore | comando presente ma nessuna uscita osservata |
| `active_without_command` | attuatore | uscita attiva senza comando; severita critica |
| `persistent_low_response` | attuatore | rapporto uscita/valore nominale troppo basso per piu cicli |

`FaultDetectorConfig` rende configurabili conteggi, rapporto minimo di risposta,
tolleranze, range fisici e velocita massime dei singoli canali. I limiti N/P/K
della fase attiva vengono invece letti direttamente dalla ricetta runtime.

`duration_seconds` e opzionale ed e espresso in tempo simulato. Alla scadenza
il fault viene rimosso automaticamente. In alternativa l'utente invia:

```json
{
  "command_id": "fault-reset-1",
  "command_type": "ResetFault",
  "payload": {"fault_id": "lighting-stuck-on"}
}
```

Un fault recuperabile porta a `Degraded`: la sola variabile non affidabile
rimane senza comando, mentre gli altri controlli continuano a funzionare. Se
persiste per il numero di cicli configurato, passa a `EmergencyLockdown`.
Un attuatore rilevato attivo senza comando e invece critico e causa il lockdown
immediato. Dopo un fault temporaneo la zona recupera automaticamente da
`Degraded` dopo campioni sani; dopo un lockdown servono sia `ResetFault` sia
`ResetEmergency`, seguiti dal periodo di verifica gia previsto dalla FSM. Il
reset manuale non viene accettato se il detector continua a osservare un'uscita
fisica guasta.

Ogni nuova violazione pubblica `FaultDetected` con `component`, `rule`,
`severity` e `diagnostic`. Nel payload HTTP resta anche `fault_type`, come alias
compatibile di `rule` per i consumer precedenti.

Una nuova ricetta deve avere versione maggiore e lo stesso substrato fisico
della zona; il suo caricamento ferma gli attuatori, riavvia la timeline dalla
prima fase e invalida le conferme. Un fault persistente rimane attivo fino al
relativo reset, mentre un fault temporaneo scade sul tempo simulato. Dopo un
`EmergencyStop`, `ResetEmergency` abilita soltanto il recovery controllato: gli
attuatori restano fermi finche la FSM non verifica campioni sani.

L'eseguibile principale collega un `ConsoleLogger` alla zona `zone-1`. Altri
observer possono essere registrati con `EventBus::subscribe()` e rimossi con
`unsubscribe()`; `EdgeRuntime::detach_event_bus()` disattiva la pubblicazione.

I test C++ di ambiente, sensori, attuatori e controllori usano GoogleTest 1.15.2. CMake
scarica automaticamente la versione fissata al primo comando di configurazione
con `BUILD_TESTING=ON`; le esecuzioni successive riutilizzano la copia nella
cartella di build. `gtest_discover_tests` registra in CTest ciascun caso di test
separatamente.

### Ambiente e sensori simulati

`EnvironmentSimulator` possiede lo stato fisico condiviso della serra in
terriccio. Il sistema non modella vasche, livelli d'acqua o ricircolo. Il
metodo `step(delta_time_seconds, actuator_output)` evolve gradualmente:

- temperatura e umidita relativa, accoppiate al profilo esterno, alla luce,
  alla traspirazione e al ricambio d'aria;
- pH ed EC dell'acqua presente nei pori del terriccio;
- disponibilita stimata di azoto, fosforo e potassio in mg/L nell'acqua della
  zona radicale, conservata tramite un bilancio di massa;
- umidita del terriccio in percentuale, con ritenzione e drenaggio diversi per
  substrato universale aerato, drenante e organico ritentivo;
- luce naturale e supplementare espressa come PPFD in `umol/(m2 s)`.

La configurazione predefinita usa un substrato universale aerato, con alba alle
06:00 e fotoperiodo di 14 ore. Sono disponibili anche un substrato drenante e
uno organico ritentivo. La dinamica ambientale non dipende dalla specie
coltivata. I cinque profili liquidi predefiniti rappresentano azoto, fosforo,
potassio, pH+ e pH-. I coefficienti sono didattici e possono essere sostituiti
in `EnvironmentConfig` usando le schede tecniche dei prodotti. N/P/K
appartengono allo stato fisico interno ma non sono letture di tre sensori.

La dose d'acqua viene applicata direttamente al substrato: una parte viene
trattenuta, una parte e assorbita dalle radici, una parte evapora e l'eccesso
viene drenato. I contenitori dei prodotti N, P, K, pH+ e pH- alimentano i soli
dosatori e non costituiscono una vasca o una soluzione idroponica.

| Prodotto | N (mg/mL) | P (mg/mL) | K (mg/mL) | Delta EC (mS/cm per mL) | Delta pH per mL |
| --- | ---: | ---: | ---: | ---: | ---: |
| Azoto | 50 | 0 | 0 | 0.040 | -0.001 |
| Fosforo | 0 | 20 | 0 | 0.030 | -0.002 |
| Potassio | 0 | 0 | 50 | 0.035 | 0 |
| pH+ | 0 | 0 | 0 | 0.010 | 0.020 |
| pH- | 0 | 0 | 0 | 0.010 | -0.020 |

Le concentrazioni iniziali sono 150 mg/L di N, 50 mg/L di P e 200 mg/L di K.
Gli assorbimenti nominali configurabili sono rispettivamente 1.5, 0.3 e
1.8 mg/h, scalati dall'attivita della pianta.

La luce naturale combina il ciclo solare con due livelli di nuvolosita
stocastica. Ogni giorno viene estratto un regime atmosferico piu sereno o piu
coperto; variazioni correlate su scala oraria simulano invece il passaggio
graduale delle nuvole. Media, variabilita giornaliera, variabilita oraria e
tempo di persistenza sono configurabili in `EnvironmentConfig`. Specificando
lo stesso seed si ottiene la stessa sequenza meteorologica.

`SensorSimulator::read(const EnvironmentState&)` non fa avanzare il tempo e
non modifica l'ambiente. Aggiunge rumore gaussiano, bias, correzione di
calibrazione, quantizzazione e possibili dropout. Le letture sono
`std::optional<double>` e un campione assente e rappresentato da
`std::nullopt`. I seed dell'ambiente e dei sensori sono distinti, cosi il
rumore fisico e quello strumentale restano indipendenti.

La sonda capacitiva restituisce la percentuale di umidita dopo la propria curva
di calibrazione. La sonda resistiva viene convertita in EC apparente del
terriccio; poiche la conducibilita cala quando il substrato si asciuga, il
runtime la corregge con l'umidita capacitiva per stimare la EC dell'acqua nei
pori. Dalla EC netta ricava il fertilizzante totale in mg/L con un coefficiente
empirico. Le quote N/P/K sono infine ripartite secondo la composizione nota al
bilancio di massa. Tutti questi valori vengono inviati e storicizzati nella
telemetria del backend.

Il modello ha finalita didattica ed e progettato per produrre dinamiche
plausibili e confronti causali. Non e calibrato per decisioni agronomiche reali.

### Simulatore degli attuatori

`ActuatorSimulator` mantiene separati il comando del controllore e l'uscita
fisica dell'attuatore. La configurazione predefinita rappresenta:

- pompa ON/OFF con portata fissa di 2 L/h e dose massima di 5 L;
- cinque contenitori di concentrato liquido per N, P, K, pH+ e pH-;
- cinque elettrovalvole ON/OFF da 20 mL/h collegate alla stessa pompa
  dell'acqua;
- lampade LED con potenza elettrica massima di 200 W.

Gli attuatori partono spenti. Per l'irrigazione,
`request_irrigation_volume_liters()` riceve direttamente la dose decisa dal
controllore. `step(delta_time_seconds)` mantiene la pompa accesa alla portata
fissa finche la dose non e stata completata e registra i litri realmente
erogati nel passo. Le elettrovalvole sono comandi binari; le lampade ricevono
un comando tra 0% e 100%, convertito in watt.

Una valvola di concentrato puo essere aperta solo durante un'irrigazione
attiva. N, P e K possono fluire insieme e con uno solo fra pH+ e pH-; i due
correttori sono interbloccati. La portata d'acqua resta invariata. Se la dose
d'acqua termina durante uno `step()`, i millilitri vengono integrati soltanto
per il tempo effettivo di pompaggio e tutte le valvole vengono chiuse.
`cancel_irrigation()` e `stop_all()` applicano la stessa protezione.

I volumi fisici vengono applicati all'ambiente: l'acqua modifica l'umidita e
diluisce i nutrienti; N/P/K aggiungono masse separate; drenaggio e assorbimento
le riducono; pH+ e pH- correggono il pH. `EdgeRuntime` converte le dosi in mL
nei tempi di apertura delle valvole e le chiude quando il volume richiesto e
stato raggiunto.

La sonda capacitiva di umidita continua a restituire una percentuale:
l'attuatore eroga una dose in litri, l'ambiente aggiorna l'umidita fisica e la
sonda ne produce una stima con gli errori strumentali configurati.

### Controllori

Il file `controllers.cpp` implementa tre controllori scalari compatibili con
l'interfaccia comune `IController`. `ControllerFactory` crea la Strategy scelta
tramite `StrategyType`. Le API precedenti `update()` restano disponibili. Ogni
controllore restituisce un comando nell'unita scelta dall'anello: per
l'irrigazione una dose in litri, per le lampade una percentuale e per i prodotti
liquidi una dose in mL.

- `ThresholdController` usa due soglie e mantiene lo stato nella zona
  intermedia, introducendo isteresi ed evitando accensioni e spegnimenti
  continui vicino a una singola soglia.
- `PidController` combina termine proporzionale, integrale e derivativo,
  richiede la durata del passo e limita il comando. Include una protezione
  essenziale contro l'accumulo dell'integrale durante la saturazione.
- `PredictiveController` calcola il trend tra due misure, lo proietta su un
  orizzonte configurabile e regola il comando rispetto al valore previsto.
  Si tratta di una previsione lineare iniziale, non di MPC o machine learning.

`ControlDirection` permette di indicare se l'attuatore associato aumenta o
diminuisce la variabile controllata. L'integrazione stabilisce esplicitamente
quali controllori comandano pompa, concime e lampade.

Un controllo a soglia dell'irrigazione puo essere collegato cosi:

```cpp
ThresholdController irrigation(40.0, 60.0,
    ControlDirection::INCREASES_PROCESS_VALUE, 0.5, 0.0);
const double requested_liters = irrigation.update(soil_moisture_percent);
if (requested_liters > 0.0 && !actuators.output().water_pump_on) {
    actuators.request_irrigation_volume_liters(requested_liters);
}
actuators.step(delta_time_seconds);
environment.step(delta_time_seconds, actuators.output());
```

### Ricette e conferma agronomica

`RecipeControlSystem` associa una ricetta a sei variabili: umidita del
terriccio, luce PPFD, pH dell'acqua interstiziale e disponibilita stimata di
azoto, fosforo e potassio. Temperatura e umidita dell'aria restano telemetria
osservata e non hanno controllori. Ogni fase contiene setpoint,
intervallo ammesso, limiti di sicurezza, fotoperiodo e dosi N/P/K suggerite.
Ogni `ControllerConfiguration` registra inoltre `input_source` (sensore o
modello), attuatore,
Strategy predefinita e selezionata, parametri, unita, limiti d'uscita, stato di
conferma e versione.

Il tipo di terriccio e un campo obbligatorio della ricetta JSON (`substrate`):
non viene applicato alcun valore predefinito se manca. La scelta alimenta il
modello predittivo N/P/K e deve essere una fra `aerated-universal`, `draining`
e `organic-retentive`.

Le impostazioni predefinite sono:

| Variabile | Strategy predefinita | Sorgente |
| --- | --- | --- |
| Umidita del terreno | Threshold con isteresi | sensore di umidita |
| Luce | Threshold con isteresi e fotoperiodo | sensore PPFD |
| pH | PID bidirezionale con piccoli dosaggi | sensore pH |
| N, P, K | Predictive | EC corretta, composizione del modello e storico dosi |

L'agronomo puo sostituire una Strategy con Threshold, PID o Predictive e
modificarne i parametri. Nessun comando viene calcolato prima della conferma.
Gli stati possibili sono `PENDING_CONFIRMATION`, `CONFIRMED`, `REJECTED` e
`INVALID`. Un cambio di ricetta, Strategy o parametri invalida tutte le
conferme; il passaggio automatico tra fasi della stessa versione le mantiene.

N/P/K non espongono sensori selettivi inesistenti: usano la concentrazione
totale stimata dalla EC, la composizione del modello, il target della fase, il
substrato, l'acqua erogata e la dose
cumulativa. Threshold e PID vengono quindi rifiutati per N/P/K come
incompatibili con la sorgente disponibile.

Il JSON canonico usa `input_source`. Il precedente campo `sensor` viene ancora
accettato in lettura per compatibilita, ma ogni nuova serializzazione usa il
nome non ambiguo.

Prima dell'uscita, il supervisore applica con priorita:

- volume massimo d'acqua e durata massima della pompa;
- dose massima per comando e giornaliera;
- intervallo minimo tra dosaggi;
- tempo di assestamento del pH;
- mutua esclusione fra pH+ e pH-;
- blocco per input non valido, superamento dei limiti fisici o configurazione
  non confermata.

`load_recipe_json()` e `save_recipe_json()` serializzano l'intero modello. La
ricetta dimostrativa e in `config/example_recipe.json` e descrive due fasi del
pomodoro su substrato universale aerato. I coefficienti sono didattici.

La directory `config/recipe_catalog/recipes/` contiene un file JSON di
bootstrap per ciascuna delle 20 piante, cinque per ognuno dei quattro reparti
produttivi. `config/recipe_catalog/profiles.json` raccoglie i profili condivisi
di luce, irrigazione e concimazione e quattro sequenze di fasi riutilizzabili:
fogliame, fioritura, succulente e produzione. Ogni file pianta puo scegliere
una sequenza diversa e sovrascrivere durata, fotoperiodo, fattori dei setpoint o
uno dei profili soltanto per una fase; `recipe-pomodorino` contiene un override
della fase di produzione come esempio.

Durante `init_db()` il bootstrap viene validato ed espanso in una ricetta
completa di quattro fasi. L'intero contratto risultante, inclusi tutti i target
di ogni fase e i controllori, viene serializzato nella colonna `recipes.data`
di SQLite. `recipe_catalog_imports.catalog_version` permette la migrazione del
vecchio catalogo monofase v1 al catalogo v2 senza rendere i JSON una sorgente
runtime. Dopo l'importazione, repository, API ed Edge leggono esclusivamente
SQLite; `GET /api/v1/recipes/{recipe_id}` e il canale usato dall'Edge. Gli
eventuali JSON prodotti tramite l'utilita di export separata sono soltanto
snapshot espliciti per avvio offline: `POST /recipes` non li genera e il
normale runtime non li legge. Ogni ricetta include
`department_number`,
il `department_name` calcolato e un `care_profile` che conserva testualmente
luce, irrigazione, temperatura e concimazione del ricettario. I target
numerici di luce, umidita del terreno, pH e N/P/K sono una traduzione
operativa prudenziale delle indicazioni qualitative e richiedono comunque la
conferma dell'agronomo.

L'elenco si puo restringere, per esempio, con
`GET /api/v1/recipes?department_number=3`. Il backend impedisce di assegnare
una ricetta di catalogo a un reparto diverso o a una zona con una specie
diversa da `plant_type`; le ricette personalizzate prive dei metadati di
catalogo mantengono il comportamento precedente.

Una ricetta iniziale del catalogo e alla versione 2. Puo essere personalizzata tramite
`POST /api/v1/recipes` usando lo stesso identificativo e una versione
superiore; la copia salvata nel database prende allora il posto di quella
iniziale. Le inizializzazioni successive non rileggono il bootstrap della
stessa versione e non sovrascrivono ricette SQLite con versione uguale o
superiore; una nuova versione dello schema di catalogo abilita invece una
migrazione esplicita dei soli record piu vecchi.

## Experiments C++

Gli experiments sono programmi dimostrativi separati dagli unit test e usano
direttamente le librerie di sensori, attuatori e controllori. Gli experiment di
sensori e attuatori usano una finestra gnuplot interattiva. Quello dei sensori
salva un solo CSV multivariato ma nessun PNG; quello degli attuatori non salva
file. I confronti dei controllori usano soltanto finestre gnuplot interattive.
Anche l'experiment della ricetta apre una finestra interattiva e salva i soli
dati CSV, senza generare immagini.

L'experiment dei sensori e un unico programma interattivo che simula una
giornata o una settimana con passo di 15 minuti. Tutti gli attuatori rimangono
spenti, cosi le misure rappresentano esclusivamente l'evoluzione naturale
dell'ambiente.

### Installazione di gnuplot

Su macOS con Homebrew:

```bash
brew install gnuplot
```

Su Ubuntu o Debian:

```bash
sudo apt update
sudo apt install gnuplot
```

Su Fedora:

```bash
sudo dnf install gnuplot
```

Su Windows con winget:

```powershell
winget install gnuplot.gnuplot
```

Verificare che il comando sia raggiungibile dal terminale:

```bash
gnuplot --version
```

Se gnuplot non e presente, gli experiment interattivi di sensori, attuatori,
ricette e confronto dei controllori segnalano che gnuplot e necessario e
terminano senza avviare la sessione.

### Compilazione degli experiments

Dalla radice del repository, configurare CMake abilitando gli experiments. I
test possono essere disabilitati se si desiderano compilare soltanto le demo:

```bash
cmake -S edge -B edge/build -DBUILD_EXPERIMENTS=ON -DBUILD_TESTING=OFF
cmake --build edge/build --target \
    sensor_simulation \
    actuator_simulation \
    controller_generic_comparison \
    controller_irrigation_comparison \
    recipe_control_simulation
```

`BUILD_EXPERIMENTS` e attivo per impostazione predefinita, quindi una normale
compilazione completa include gia questi programmi. Per visualizzare l'elenco
dei target disponibili:

```bash
cmake --build edge/build --target help
```

Gli eseguibili sono creati in `edge/build/bin`. Avviarli separatamente con:

```bash
./edge/build/bin/sensor_simulation
./edge/build/bin/actuator_simulation
./edge/build/bin/controller_generic_comparison
./edge/build/bin/controller_irrigation_comparison
./edge/build/bin/recipe_control_simulation
```

`sensor_simulation` apre una sola finestra gnuplot con quattro pannelli
sincronizzati sullo stesso asse temporale:

- temperatura dell'aria in gradi Celsius;
- umidita dell'aria e del terriccio nello stesso pannello, entrambe in
  percentuale e con colori distinti;
- pH dell'acqua presente nei pori del terriccio;
- luce espressa come PPFD in micromoli per metro quadrato al secondo.

Temperatura e pH usano intervalli verticali adattati ai dati, le umidita
mantengono la scala fisica 0-100% e il PPFD parte da zero. Il programma salva
tutte le misure in un unico CSV, non crea PNG e mantiene il grafico aperto
finche non si preme Invio nel terminale. Gli attuatori restano sempre spenti.

La simulazione dei sensori non accetta opzioni da riga di comando e richiede
interattivamente:

- giornata oppure settimana;
- substrato universale aerato, drenante oppure organico ritentivo;
- seed numerico riproducibile oppure invio per generarne uno casuale.

`actuator_simulation` apre un'unica finestra gnuplot con tre grafici: pompa di
irrigazione, cinque elettrovalvole dei concentrati e illuminazione. Per ogni
irrigazione si inseriscono il volume d'acqua, le scelte N/P/K e al massimo un
correttore di pH. Il programma mostra le cinque portate in mL/h separatamente,
la portata d'acqua invariata e la chiusura sicura al termine. Le lampade
ricevono una percentuale. Il comando `q` termina il ciclo e chiude il grafico;
l'experiment richiede gnuplot e non crea CSV o PNG.

`controller_generic_comparison` isola il comportamento matematico di Threshold,
PID e Predictive su tre copie identiche di un processo normalizzato del primo
ordine. Il processo include inerzia dell'attuatore, ritardo di trasporto,
saturazione, rumore di misura e un disturbo temporaneo. Una finestra gnuplot
mostra risposta, comando, errore istantaneo con segno e variazione cumulativa
del comando.

`controller_irrigation_comparison` verifica invece l'integrazione completa
dell'edge in tre anelli chiusi di irrigazione. Gli anelli partono dallo stesso
stato, usano gli stessi seed, lo stesso rumore di misura, lo stesso setpoint e
gli stessi limiti della pompa. Una finestra gnuplot mostra risposta
dell'umidita del terriccio, dose richiesta, errore istantaneo con segno e acqua
cumulativa.

Entrambi i confronti mantengono i dati in memoria e non creano CSV o PNG; la
finestra rimane aperta finche non si preme Invio nel terminale.

`recipe_control_simulation` carica `config/example_recipe.json`, simula la
conferma dell'agronomo ed esegue per intero la prima fase con passo di 15
minuti. Il processo dimostrativo applica i comandi sicuri di acqua, luce, pH e
N/P/K. Salva `recipe_phase_simulation.csv`, con stato, comandi e dosi
cumulative, e apre un'unica finestra gnuplot con otto pannelli affiancati:
umidita e acqua, PPFD e lampade, pH e relativo dosaggio, concentrazioni N/P/K e
relative dosi. Non genera file PNG e mantiene il grafico aperto fino alla
pressione di Invio nel terminale.

La directory di output puo essere passata come primo argomento:

```bash
./edge/build/bin/recipe_control_simulation edge/build/recipe_results
```

I dropout dei sensori vengono salvati come celle CSV vuote e visualizzati come
interruzioni delle curve. Il CSV dei sensori viene salvato in
`experiment_results` relativa alla directory di avvio.

## Preparazione del backend Python

Creare e attivare un virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Su Windows, il comando di attivazione equivalente e:

```powershell
.venv\Scripts\activate
```

Installare le dipendenze:

```bash
python -m pip install -r backend/requirements.txt
```

Avviare il backend:

```bash
uvicorn backend.app.main:app --reload
```

Il servizio risponde all'indirizzo `http://127.0.0.1:8000`. Per verificare
l'endpoint di salute:

```bash
curl http://127.0.0.1:8000/health
```

La risposta attesa e `{"status":"healthy"}`.

Le API Edge sono disponibili anche con prefisso `/api/v1`. Comprendono:

- telemetria e snapshot degli attuatori per zona;
- eventi Edge;
<<<<<<< HEAD
- elenco e distribuzione delle ricette, con lo storico delle versioni;
- accodamento, polling e conferma dei comandi runtime;
- coltivazioni: bozza, conferma, esito di attivazione, lettura, pausa e
  conclusione, con al piu una coltivazione non conclusa per settore.
=======
- elenco e distribuzione delle ricette;
- anagrafica delle piante, flag di quarantena e storico degli spostamenti;
- accodamento, polling e conferma dei comandi runtime.
>>>>>>> 9739fd89a2d793974daf9df49e585172e2fac6ec

Gli endpoint senza prefisso rimangono disponibili per compatibilita.

Per eseguire i test automatici:

```bash
python -m pytest backend/tests
```

## Dashboard

Aprire direttamente il file `dashboard/index.html` con un browser. Non e
necessario avviare un server web. Il pulsante **Check local status** aggiorna
lo stato visualizzato a `Dashboard ready`.

## Ricetta JSON

Il file `config/example_recipe.json` e caricato dai test, dal sistema di
controllo Edge e dall'experiment dedicato. Contiene le fasi
`VegetativeGrowth` e `Flowering`, i target delle sei variabili, le
configurazioni Strategy e tutti i limiti prioritari. I valori hanno finalita
dimostrativa e non sostituiscono la validazione di un agronomo.

Il catalogo iniziale modificabile e organizzato in
`config/recipe_catalog/profiles.json` e
`config/recipe_catalog/recipes/<pianta>.json`. Per aggiungere una pianta basta
aggiungere un file valido nella directory `recipes`, senza modificare Python.
Il modulo `backend/app/features/recipes/catalog.py` valida profili e ricette,
segnala il percorso esatto dei file errati, applica la sequenza condivisa della
categoria e gli override della singola pianta, quindi costruisce il contratto
multifase richiesto dall'Edge. I JSON sono input di bootstrap: il repository
non possiede fallback in memoria e, terminata l'inizializzazione, elenco,
lettura, transizioni e versionamento usano la ricetta completa in `recipes`.
