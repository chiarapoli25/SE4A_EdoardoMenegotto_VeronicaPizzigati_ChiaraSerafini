# SmartHydro

SmartHydro e un progetto per il monitoraggio e il controllo di una coltivazione
in terriccio. Questa Fase 0 prepara una base di lavoro avviabile composta da un
Edge Controller in C++17, un backend HTTP in Python, una dashboard statica e
una ricetta di coltivazione di esempio.

L'area Edge include un simulatore dinamico della serra, sensori con errori
strumentali, attuatori e controllori. Non sono ancora presenti dispositivi reali,
controllori automatici, database, autenticazione, comunicazione tra Edge e
backend o Docker.

## Struttura del progetto

```text
.
|-- backend/       Backend FastAPI e test automatici
|-- config/        Ricette e configurazioni di esempio
|-- dashboard/     Dashboard statica HTML, CSS e JavaScript
|-- demo/          Spazio per futuri scenari dimostrativi
|-- docs/          Documentazione di progetto
|-- edge/          Edge Controller C++17 compilato con CMake
|-- .gitignore
`-- README.md
```

## Prerequisiti

- CMake 3.16 o successivo
- Un compilatore compatibile con C++17
- gnuplot (facoltativo per l'Edge, necessario per i grafici degli experiments)
- Python 3.10 o successivo
- Un browser web moderno

Tutti i comandi seguenti devono essere eseguiti dalla radice del repository.

## Compilazione dell'Edge Controller

```bash
cmake -S edge -B edge/build
cmake --build edge/build
ctest --test-dir edge/build --output-on-failure
./edge/build/bin/edge
```

L'eseguibile stampa nome, versione e stato dell'Edge Controller, seguiti da un
campione sincronizzato ottenuto applicando lo stato degli attuatori
all'avanzamento dell'ambiente e leggendo infine i sensori.

I test C++ di ambiente, sensori, attuatori e controllori usano GoogleTest 1.15.2. CMake
scarica automaticamente la versione fissata al primo comando di configurazione
con `BUILD_TESTING=ON`; le esecuzioni successive riutilizzano la copia nella
cartella di build. `gtest_discover_tests` registra in CTest ciascun caso di test
separatamente.

### Ambiente e sensori simulati

`EnvironmentSimulator` possiede lo stato fisico condiviso della serra. Il
metodo `step(delta_time_seconds, actuator_output)` evolve gradualmente:

- temperatura e umidita relativa, accoppiate al profilo esterno, alla luce,
  alla traspirazione e al ricambio d'aria;
- pH ed EC della soluzione presente nei pori del terriccio;
- concentrazioni disponibili di azoto, fosforo e potassio in mg/L, conservate
  tramite un bilancio di massa;
- umidita del terriccio in percentuale, con ritenzione e drenaggio diversi per
  substrato universale aerato, drenante e organico ritentivo;
- luce naturale e supplementare espressa come PPFD in `umol/(m2 s)`.

La configurazione predefinita usa un substrato universale aerato, con alba alle
06:00 e fotoperiodo di 14 ore. Sono disponibili anche un substrato drenante e
uno organico ritentivo. La dinamica ambientale non dipende dalla specie
coltivata. I cinque profili liquidi predefiniti rappresentano azoto, fosforo,
potassio, pH+ e pH-. I coefficienti sono didattici e possono essere sostituiti
in `EnvironmentConfig` usando le schede tecniche dei prodotti. N/P/K
appartengono allo stato fisico ma non sono letture di sensori.

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

Il modello ha finalita didattica ed e progettato per produrre dinamiche
plausibili e confronti causali. Non e calibrato per decisioni agronomiche reali.

### Simulatore degli attuatori

`ActuatorSimulator` mantiene separati il comando del controllore e l'uscita
fisica dell'attuatore. La configurazione predefinita rappresenta:

- pompa ON/OFF con portata fissa di 2 L/h e dose massima di 5 L;
- cinque serbatoi di concentrato liquido per N, P, K, pH+ e pH-;
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
le riducono; pH+ e pH- correggono il pH. L'accodamento delle dosi esatte in mL
e la chiusura al raggiungimento del volume appartengono al futuro regolatore.

Il sensore di umidita del terriccio continua a restituire una percentuale:
l'attuatore eroga una dose in litri, l'ambiente aggiorna l'umidita fisica e il
sensore osserva quel valore aggiungendo i soli errori strumentali configurati.

### Controllori

Il file `controllers.cpp` implementa tre controllori scalari. Ciascuno riceve
una misura e restituisce un comando nell'unita scelta dall'anello di controllo.
Per l'irrigazione il comando puo quindi essere una dose in litri; per le
lampade puo rimanere una percentuale. I controllori non sono collegati
automaticamente a uno specifico sensore o attuatore.

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

## Experiments C++

Gli experiments sono programmi dimostrativi separati dagli unit test e usano
direttamente le librerie di sensori, attuatori e controllori. Gli experiment di
sensori e attuatori usano una finestra gnuplot interattiva. Quello dei sensori
salva un solo CSV multivariato ma nessun PNG; quello degli attuatori non salva
file. I confronti dei controllori usano soltanto finestre gnuplot interattive.

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

Se gnuplot non e presente, gli experiment interattivi di sensori, attuatori e
confronto dei controllori segnalano che gnuplot e necessario e terminano senza
avviare la sessione.

### Compilazione degli experiments

Dalla radice del repository, configurare CMake abilitando gli experiments. I
test possono essere disabilitati se si desiderano compilare soltanto le demo:

```bash
cmake -S edge -B edge/build -DBUILD_EXPERIMENTS=ON -DBUILD_TESTING=OFF
cmake --build edge/build --target \
    sensor_simulation \
    actuator_simulation \
    controller_generic_comparison \
    controller_irrigation_comparison
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
```

`sensor_simulation` apre una sola finestra gnuplot con quattro pannelli
sincronizzati sullo stesso asse temporale:

- temperatura dell'aria in gradi Celsius;
- umidita dell'aria e del terriccio nello stesso pannello, entrambe in
  percentuale e con colori distinti;
- pH della soluzione presente nei pori del terriccio;
- luce espressa come PPFD in micromoli per metro quadrato al secondo.

Temperatura e pH usano intervalli verticali adattati ai dati, le umidita
mantengono la scala fisica 0-100% e il PPFD parte da zero. Il programma salva
tutte le misure in un unico CSV, non crea PNG e mantiene il grafico aperto
finche non si preme Invio nel terminale. Gli attuatori restano sempre spenti.

Gli experiments non accettano opzioni da riga di comando. Dopo l'avvio
la simulazione dei sensori richiede interattivamente:

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

Per eseguire i test automatici:

```bash
python -m pytest backend/tests
```

## Dashboard

Aprire direttamente il file `dashboard/index.html` con un browser. Non e
necessario avviare un server web. Il pulsante **Check local status** aggiorna
lo stato visualizzato a `Dashboard ready`.

## Ricetta JSON

Il file `config/example_recipe.json` contiene una ricetta di esempio per il
pomodoro (`Tomato`) nella fase di crescita vegetativa
(`VegetativeGrowth`). Definisce gli intervalli consigliati di temperatura,
umidita relativa e pH, oltre al nome, rapporto NPK, concentrazione e frequenza
di somministrazione del fertilizzante. I valori hanno finalita dimostrativa e
non vengono ancora utilizzati da Edge Controller o backend.
