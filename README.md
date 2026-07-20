# SmartHydro

SmartHydro e un progetto per il monitoraggio e il controllo di un sistema
idroponico. Questa Fase 0 prepara una base di lavoro avviabile composta da un
Edge Controller in C++17, un backend HTTP in Python, una dashboard statica e
una ricetta di coltivazione di esempio.

In questa fase non sono ancora presenti database, autenticazione, sensori,
attuatori, controllo PID, comunicazione tra Edge e backend, Docker o gestione
delle colture.

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
- Python 3.10 o successivo
- Un browser web moderno

Tutti i comandi seguenti devono essere eseguiti dalla radice del repository.

## Compilazione dell'Edge Controller

```bash
cmake -S edge -B edge/build
cmake --build edge/build
./edge/build/smarthydro_edge
```

L'eseguibile stampa nome, versione e stato dell'Edge Controller.

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
