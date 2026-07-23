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
metodo `step(delta_time_seconds, actuator_state)` evolve gradualmente:

- temperatura e umidita relativa, accoppiate al profilo esterno, alla luce,
  alla traspirazione e al ricambio d'aria;
- pH ed EC della soluzione presente nei pori del terriccio;
- disponibilita idrica radicale, con ritenzione e drenaggio diversi per
  substrato universale aerato, drenante e organico ritentivo;
- luce naturale e supplementare espressa come PPFD in `umol/(m2 s)`.

La configurazione predefinita rappresenta pomodoro in fase vegetativa su
substrato universale aerato, con alba alle 06:00 e fotoperiodo di 14 ore. Sono
disponibili anche un substrato drenante e uno organico ritentivo. Il profilo di
concime incluso e `tomato-growth`.

`SensorSimulator::read(const EnvironmentState&)` non fa avanzare il tempo e
non modifica l'ambiente. Aggiunge rumore gaussiano, bias, correzione di
calibrazione, quantizzazione e possibili dropout. Le letture sono
`std::optional<double>` e un campione assente e rappresentato da
`std::nullopt`. I seed dell'ambiente e dei sensori sono distinti, cosi il
rumore fisico e quello strumentale restano indipendenti.

Il modello ha finalita didattica ed e progettato per produrre dinamiche
plausibili e confronti causali. Non e calibrato per decisioni agronomiche reali.

### Simulatore degli attuatori

`ActuatorSimulator` rappresenta attuatori ideali:

- pompa di irrigazione, regolabile tra 0% e 100%;
- selettore del concime tramite un identificativo testuale;
- dosaggio del concime liquido, regolabile tra 0% e 100%;
- lampade, regolabili tra 0% e 100%.

Gli attuatori partono spenti. I metodi `set_*_percent()` applicano
immediatamente la percentuale richiesta allo stato erogato. Il modello non
introduce ritardi, guasti o differenze tra comando e uscita: se viene richiesto
il 60%, l'attuatore eroga subito il 60%.

Non e possibile avviare il dosaggio senza avere prima selezionato un concime;
rimuovere la selezione arresta anche il dosaggio. Il metodo `stop_all()` riporta
immediatamente lo stato alla condizione sicura. Il simulatore degli
attuatori non contiene logica decisionale. Il suo stato effettivo viene pero
applicato all'ambiente: la pompa irriga il terriccio, le lampade aggiungono
PPFD e calore, il dosatore modifica pH ed EC secondo il profilo chimico
selezionato. L'irrigazione distribuisce il concime e puo diluire o lisciviare i
sali, soprattutto nel substrato drenante.

### Controllori

Il file `controllers.cpp` implementa tre controllori scalari. Ciascuno riceve
una misura e restituisce un comando normalizzato tra 0% e 100%; non e ancora
collegato automaticamente a uno specifico sensore o attuatore.

- `ThresholdController` usa due soglie e mantiene lo stato nella zona
  intermedia, introducendo isteresi ed evitando accensioni e spegnimenti
  continui vicino a una singola soglia.
- `PidController` combina termine proporzionale, integrale e derivativo,
  richiede la durata del passo e limita l'uscita. Include una protezione
  essenziale contro l'accumulo dell'integrale durante la saturazione.
- `PredictiveController` calcola il trend tra due misure, lo proietta su un
  orizzonte configurabile e regola l'uscita rispetto al valore previsto.
  Si tratta di una previsione lineare iniziale, non di MPC o machine learning.

`ControlDirection` permette di indicare se l'attuatore associato aumenta o
diminuisce la variabile controllata. La successiva integrazione dovra stabilire
esplicitamente quali controllori comandano pompa, concime e lampade.

## Experiments C++

Gli experiments sono programmi dimostrativi separati dagli unit test e usano
direttamente le librerie di sensori, attuatori e controllori. Quelli di sensori
e controllori salvano i campioni in CSV e, se gnuplot e disponibile, generano
un grafico PNG. L'experiment degli attuatori usa invece una finestra gnuplot
interattiva e non salva file.

Gli experiments dei sensori sono interattivi e simulano una giornata o una
settimana con passo di 15 minuti. Tutti gli attuatori rimangono spenti, cosi le
misure rappresentano esclusivamente l'evoluzione naturale dell'ambiente.

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

Se gnuplot non e presente, gli experiments terminano comunque correttamente e
conservano il CSV, mostrando un avviso per il grafico non generato.

### Compilazione degli experiments

Dalla radice del repository, configurare CMake abilitando gli experiments. I
test possono essere disabilitati se si desiderano compilare soltanto le demo:

```bash
cmake -S edge -B edge/build -DBUILD_EXPERIMENTS=ON -DBUILD_TESTING=OFF
cmake --build edge/build --target \
    temperature_simulation \
    humidity_simulation \
    ph_simulation \
    light_simulation \
    environment_simulation \
    actuator_live_simulation \
    threshold_step_response \
    pid_step_response \
    predictive_step_response
```

`BUILD_EXPERIMENTS` e attivo per impostazione predefinita, quindi una normale
compilazione completa include gia questi programmi. Per visualizzare l'elenco
dei target disponibili:

```bash
cmake --build edge/build --target help
```

Gli eseguibili sono creati in `edge/build/bin`. Avviarli separatamente con:

```bash
./edge/build/bin/temperature_simulation
./edge/build/bin/humidity_simulation
./edge/build/bin/ph_simulation
./edge/build/bin/light_simulation
./edge/build/bin/environment_simulation
./edge/build/bin/actuator_live_simulation
./edge/build/bin/threshold_step_response
./edge/build/bin/pid_step_response
./edge/build/bin/predictive_step_response
```

I primi quattro programmi isolano nel grafico una misura. Il quinto registra
temperatura, umidita, pH e PPFD nello stesso CSV. Anche questo experiment usa
sempre attuatori spenti.

Gli experiments non accettano opzioni da riga di comando. Dopo l'avvio
le simulazioni dei sensori richiedono interattivamente:

- giornata oppure settimana;
- substrato universale aerato, drenante oppure organico ritentivo;
- seed numerico riproducibile oppure invio per generarne uno casuale.

`actuator_live_simulation` apre un'unica finestra gnuplot con tre grafici:
pompa di irrigazione, dosatore di concime e illuminazione. Dal terminale si
sceglie ripetutamente quale attuatore modificare e si inserisce una potenza tra
0% e 100%. Il nuovo valore viene erogato e mostrato immediatamente, mentre gli
altri due rimangono invariati. Il comando `q` termina il ciclo e chiude il
grafico. Questo experiment richiede gnuplot e non crea CSV o PNG.

Gli experiments dei controllori eseguono invece scenari standard senza
richiedere input:

- `threshold_step_response` evidenzia l'isteresi tra le soglie 40% e 60%;
- `pid_step_response` applica gradini attorno al setpoint 50 e mostra la
  combinazione dei termini PID;
- `predictive_step_response` combina gradini e una rampa per mostrare come il
  trend modifica previsione e uscita.

I nomi dei sensori includono durata, scenario `natural`, terriccio e seed. Se
un nome esiste gia viene aggiunto un suffisso `run-N`, senza sovrascriverlo. Le
celle CSV vuote rappresentano i dropout e gnuplot le visualizza come
interruzioni.
Per impostazione predefinita i risultati vengono salvati in
`experiment_results` relativa alla directory di avvio; il percorso assoluto
viene sempre mostrato nel terminale.

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
