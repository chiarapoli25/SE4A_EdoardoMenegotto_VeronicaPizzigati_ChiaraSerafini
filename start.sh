#!/bin/bash
# Avvia insieme, in un solo container/servizio Railway, il vero Edge
# Controller C++ (in background) e il backend FastAPI (in foreground) —
# nessuna interazione umana richiesta: a differenza di demo/seed_dev_data.py
# (pensato per un test locale rigoroso, dove l'Edge lo avvia una persona a
# mano in un altro terminale, vedi wait_for_edge_start() li' dentro), qui
# l'Edge deve partire da solo ad ogni avvio del container.
#
# Stessa porta per entrambi (unico servizio, un solo container): l'Edge
# parla con il backend su 127.0.0.1:$PORT esattamente come farebbe un
# chiamante esterno via /api/v1, mai in-process.
set -u

PORT="${PORT:-8000}"
# Stesso edge-id usato ovunque nei nostri script demo (demo/seed_dev_data.py,
# demo/seed_test_scenario.py): qualunque zona registrata con
# assigned_edge_id="edge-serra-1" viene scoperta automaticamente da questo
# stesso processo, senza bisogno di alcuna opzione --zones/--zone-id.
EDGE_ID="edge-serra-1"
OUTBOX_PATH="/app/edge-data/outbox"

mkdir -p "$OUTBOX_PATH"

EDGE_PID=""
UVICORN_PID=""

cleanup() {
    echo "[start] arresto in corso: fermo Edge e backend..."
    [ -n "$EDGE_PID" ] && kill "$EDGE_PID" 2>/dev/null
    [ -n "$UVICORN_PID" ] && kill "$UVICORN_PID" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup TERM INT

echo "[start] avvio l'Edge Controller reale (edge-id=${EDGE_ID}) in background..."
# SMARTHYDRO_API_TOKEN, se impostata sul servizio Railway, viene letta in
# automatico sia da questo edge (edge/src/main.cpp) sia dal backend
# (require_api_token su /api/v1): nessun'altra variabile d'ambiente o
# opzione da propagare a mano fra i due processi.
./edge/build/bin/edge \
    --backend-url "http://127.0.0.1:${PORT}" \
    --edge-id "${EDGE_ID}" \
    --outbox-path "${OUTBOX_PATH}" &
EDGE_PID=$!

# Diamo all'Edge un attimo per fallire subito in modo visibile (es. libcurl
# mancante, argomenti invalidi) invece di scoprirlo solo quando la prima
# zona non passa mai a Running: se muore nei primi due secondi, il
# container si ferma con un errore chiaro nei log di Railway invece di
# restare su un backend "muto" senza alcun Edge reale dietro.
sleep 2
if ! kill -0 "$EDGE_PID" 2>/dev/null; then
    echo "[start] ERRORE: l'Edge Controller e' terminato subito dopo l'avvio (vedi il log sopra per il motivo)." >&2
    exit 1
fi
echo "[start] Edge Controller avviato (pid ${EDGE_PID})."

echo "[start] avvio uvicorn (backend FastAPI + dashboard statica) su 0.0.0.0:${PORT}..."
uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT}" &
UVICORN_PID=$!

# Se uno dei due muore, l'altro da solo non serve a nulla (l'Edge senza
# backend non ha nessuno a cui parlare; il backend senza Edge non fa mai
# avanzare alcuna zona reale): fermiamo anche l'altro e usciamo, cosi'
# Railway vede il container come fallito e lo riavvia per intero.
wait -n "$EDGE_PID" "$UVICORN_PID"
EXIT_CODE=$?
echo "[start] un processo e' terminato (exit ${EXIT_CODE}): fermo l'altro e chiudo il container."
cleanup
exit "$EXIT_CODE"
