# SmartHydro

SmartHydro e un progetto per il monitoraggio e il controllo di un sistema
idroponico. Questa Fase 0 prepara una base di lavoro avviabile composta da un
Edge Controller in C++17, un backend HTTP in Python, una dashboard statica e
una ricetta di coltivazione di esempio.

Il primo incremento dell'area Edge aggiunge un simulatore dei sensori e un
gestore simulato degli attuatori. Non sono ancora presenti dispositivi reali,
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
campione simulato dei sensori e dallo stato sicuro iniziale degli attuatori.

I test C++ di sensori, attuatori e controllori usano GoogleTest 1.15.2. CMake
scarica automaticamente la versione fissata al primo comando di configurazione
con `BUILD_TESTING=ON`; le esecuzioni successive riutilizzano la copia nella
cartella di build. `gtest_discover_tests` registra in CTest ciascun caso di test
separatamente.

### Simulatore dei sensori

`SensorSimulator` produce una lettura aggregata contenente:

- temperatura in gradi Celsius, tra 18 e 30;
- umidita dell'aria, tra 30% e 90%;
- pH, tra 4 e 8;
- luce, espressa come percentuale tra 0% e 100%.

Il costruttore senza argomenti genera sequenze diverse a ogni esecuzione. Un
seed esplicito, per esempio `SensorSimulator(42)`, rende invece la sequenza
riproducibile per test e debug.

Ogni chiamata a `read()` rappresenta un passo temporale discreto. I valori non
vengono estratti nuovamente sull'intero intervallo, ma evolvono a partire dalla
lettura precedente con queste variazioni massime per passo:

- temperatura: 0,25 °C;
- umidita dell'aria: 1%;
- pH: 0,05;
- luce: 2,5%.

La durata reale del passo non e ancora definita. Gli intervalli e le variazioni
servono esclusivamente alla simulazione e non rappresentano valori obiettivo
per una coltura.

### Simulatore degli attuatori

`ActuatorSimulator` distingue lo stato obiettivo comandato dallo stato
effettivamente raggiunto da:

- pompa dell'acqua, regolabile tra 0% e 100%;
- selettore del concime tramite un identificativo testuale;
- dosaggio del concime liquido, regolabile tra 0% e 100%;
- lampade, regolabili tra 0% e 100%.

Gli attuatori partono spenti. I metodi `set_*_target_percent()` impostano il
valore desiderato; ogni chiamata a `step()` avvicina lo stato effettivo al
target con una variazione massima del 20% per la pompa dell'acqua, 5% per il
dosaggio del concime e 25% per le lampade.

Non e possibile avviare il dosaggio senza avere prima selezionato un concime;
rimuovere la selezione arresta anche il dosaggio. Il metodo `stop_all()` riporta
immediatamente stato e target alla condizione sicura. Il simulatore non
contiene logica decisionale e non riproduce ancora gli effetti fisici degli
attuatori sull'ambiente.

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

Gli experiments sono programmi interattivi separati dagli unit test. Usano le
librerie dell'Edge per produrre una giornata o una settimana di campioni
simulati, salvano tutte le letture in CSV e, se gnuplot e disponibile, generano
un grafico PNG. Per convenzione dei soli experiments, un passo simulato
corrisponde a 15 minuti; il programma non attende il trascorrere del tempo
reale.

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
    light_simulation
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
```

Ogni programma chiede se simulare una giornata o una settimana. I CSV e i PNG
vengono salvati nella cartella `experiment_results` relativa alla directory da
cui viene avviato l'eseguibile, e il percorso completo viene mostrato nel
terminale.

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
