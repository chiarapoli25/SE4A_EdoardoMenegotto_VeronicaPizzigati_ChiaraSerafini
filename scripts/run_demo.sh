#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_directory="$(cd "${script_directory}/.." && pwd)"
virtualenv_python="${project_directory}/.venv/bin/python"
backend_url="http://127.0.0.1:8000"
dashboard_url="${backend_url}/dashboard/"
edge_id="${SMARTHYDRO_DEFAULT_EDGE_ID:-smarthydro-edge}"
backend_pid=""
edge_pid=""

cleanup() {
  trap - EXIT
  set +e

  if [[ -n "${edge_pid}" ]] && kill -0 "${edge_pid}" 2>/dev/null; then
    kill "${edge_pid}" 2>/dev/null
  fi
  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
    kill "${backend_pid}" 2>/dev/null
  fi

  if [[ -n "${edge_pid}" ]]; then
    wait "${edge_pid}" 2>/dev/null
  fi
  if [[ -n "${backend_pid}" ]]; then
    wait "${backend_pid}" 2>/dev/null
  fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "${project_directory}"

if [[ ! -x "${virtualenv_python}" ]]; then
  echo "Creazione dell'ambiente Python locale..."
  python3 -m venv .venv
fi

if ! "${virtualenv_python}" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "Installazione delle dipendenze Python..."
  "${virtualenv_python}" -m pip install -r backend/requirements.txt
fi

echo "Compilazione dell'Edge Controller..."
cmake -S edge -B edge/build
cmake --build edge/build --target edge edge_simulator --parallel

echo
echo "Avvio del backend SmartHydro..."

"${virtualenv_python}" -m uvicorn \
  backend.app.main:app \
  --host 127.0.0.1 \
  --port 8000 &
backend_pid=$!

backend_ready=false
for ((attempt = 1; attempt <= 100; attempt++)); do
  if curl --fail --silent --output /dev/null "${backend_url}/health"; then
    backend_ready=true
    break
  fi
  if ! kill -0 "${backend_pid}" 2>/dev/null; then
    echo "Il backend si è arrestato durante l'avvio." >&2
    exit 1
  fi
  sleep 0.1
done

if [[ "${backend_ready}" != true ]]; then
  echo "Il backend non è diventato disponibile entro 10 secondi." >&2
  exit 1
fi

echo "Avvio dell'Edge Controller (${edge_id})..."
"${project_directory}/edge/build/bin/edge" \
  --backend-url "${backend_url}" \
  --edge-id "${edge_id}" &
edge_pid=$!

echo
echo "SmartHydro è disponibile su ${dashboard_url}"
echo "Premi Ctrl+C per arrestare la demo."
echo

if command -v open >/dev/null 2>&1; then
  if ! open "${dashboard_url}"; then
    echo "Browser non aperto automaticamente: visita ${dashboard_url}"
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${dashboard_url}" >/dev/null 2>&1 &
else
  echo "Browser non aperto automaticamente: visita ${dashboard_url}"
fi

while kill -0 "${backend_pid}" 2>/dev/null && \
      kill -0 "${edge_pid}" 2>/dev/null; do
  sleep 1
done

echo "Un servizio SmartHydro si è arrestato inaspettatamente." >&2
exit 1
