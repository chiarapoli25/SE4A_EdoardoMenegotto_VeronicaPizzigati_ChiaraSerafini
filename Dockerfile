# Immagine di produzione per Railway: compila l'Edge C++ (edge_simulator,
# necessario alla pagina Simulatore della dashboard, vedi
# backend/app/edge_runner.py) in uno stage separato e lo copia
# nell'immagine Python finale che serve il backend FastAPI.
#
# Il rilevamento automatico di Railway (Nixpacks) installa solo le
# dipendenze Python di backend/requirements.txt e non compila mai edge/,
# quindi ogni simulazione falliva con "Edge simulator is not available at
# .../edge/build/bin/edge_simulator". La presenza di questo Dockerfile fa
# passare Railway al build Docker, che include anche questo stage.

# ---- Stage 1: build del simulatore batch Edge (C++17, CMake) ----
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
# GoogleTest (scaricato via FetchContent) ne' le demo interattive, solo il
# target edge_simulator usato dal backend per le anteprime batch.
RUN cmake -S edge -B edge/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF \
        -DBUILD_EXPERIMENTS=OFF \
    && cmake --build edge/build --target edge_simulator -j "$(nproc)"

# ---- Stage 2: runtime Python che serve backend FastAPI + dashboard ----
FROM python:3.12-slim-bookworm

# Libreria runtime di libcurl (senza header/-dev, non serve compilare qui):
# edge_simulator vi e' linkato dinamicamente dallo stage precedente.
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
COPY --from=edge-builder /src/edge/build/bin/edge_simulator edge/build/bin/edge_simulator

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# $PORT e' impostata da Railway a runtime; 8000 resta il fallback locale.
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
