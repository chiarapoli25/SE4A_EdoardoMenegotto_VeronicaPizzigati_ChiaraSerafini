# Immagine di produzione per Railway: compila l'Edge C++ in uno stage
# separato e lo copia nell'immagine Python finale che serve il backend
# FastAPI + la dashboard statica. Un solo servizio Railway ospita quindi
# backend, dashboard ED Edge Controller reale — avviati insieme da
# start.sh, senza alcuna interazione umana (vedi start.sh).
#
# Due eseguibili Edge, due scopi distinti (nessuno sostituisce l'altro):
#   - edge_simulator: invocato SINCRONO, una volta per richiesta, dal
#     backend (backend/app/edge_runner.py) per le anteprime batch di
#     "Simula questa ricetta" nella pagina Simulatore. Non è un demone.
#   - edge: il vero Edge Controller, un demone che interroga il backend
#     in polling per scoprire le zone assegnate ed eseguirne il ciclo di
#     controllo reale (telemetria, fault, comandi, allarmi...). Senza
#     questo processo attivo nessuna zona registrata su Railway potrebbe
#     mai passare a lifecycle_state=Running.
#
# Il rilevamento automatico di Railway (Nixpacks) installa solo le
# dipendenze Python di requirements.txt e non compila mai edge/, quindi
# senza questo Dockerfile ogni simulazione fallirebbe con "Edge simulator
# is not available at .../edge/build/bin/edge_simulator" e nessuna zona
# avrebbe mai un Edge reale. La presenza di un Dockerfile alla radice del
# repository fa passare Railway al build Docker in automatico (nessuna
# configurazione aggiuntiva richiesta: vedi comunque railway.toml, che lo
# rende esplicito e vince su qualunque override rimasto nella dashboard
# Railway).

# ---- Stage 1: build dei due eseguibili Edge (C++17, CMake) ----
FROM debian:bookworm-slim AS edge-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake \
        g++ \
        make \
        libcurl4-openssl-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY edge/ edge/

# BUILD_TESTING/BUILD_EXPERIMENTS OFF: la produzione non deve compilare
# GoogleTest (scaricato via FetchContent) ne' le demo interattive, solo i
# due target usati a runtime. Un'unica invocazione di build con due
# --target condivide la compilazione delle librerie comuni (control_system,
# edge_runtime, http_backend_client, ...) fra i due eseguibili invece di
# ricompilarle due volte.
RUN cmake -S edge -B edge/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF \
        -DBUILD_EXPERIMENTS=OFF \
    && cmake --build edge/build --target edge --target edge_simulator -j "$(nproc)"

# ---- Stage 2: runtime Python che serve backend FastAPI + dashboard ----
FROM python:3.12-slim-bookworm

# Libreria runtime di libcurl (senza header/-dev, non serve compilare qui):
# entrambi gli eseguibili Edge vi sono linkati dinamicamente dallo stage
# precedente.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY dashboard/ dashboard/
COPY config/ config/
COPY demo/ demo/
COPY --from=edge-builder /src/edge/build/bin/edge edge/build/bin/edge
COPY --from=edge-builder /src/edge/build/bin/edge_simulator edge/build/bin/edge_simulator
COPY start.sh start.sh
RUN chmod +x start.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# $PORT e' impostata da Railway a runtime; 8000 resta il fallback locale
# (es. `docker run -p 8000:8000 ...` senza PORT). start.sh avvia l'Edge
# reale in background e poi il backend FastAPI in foreground: vedi
# start.sh per i dettagli e perche' l'ordine conta.
CMD ["./start.sh"]
