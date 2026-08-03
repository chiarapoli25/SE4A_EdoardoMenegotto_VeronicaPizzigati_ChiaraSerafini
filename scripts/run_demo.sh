#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_directory="$(cd "${script_directory}/.." && pwd)"
virtualenv_python="${project_directory}/.venv/bin/python"

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
echo "SmartHydro è disponibile su http://127.0.0.1:8000/dashboard/"
echo "Premi Ctrl+C per arrestare la demo."
echo

exec "${virtualenv_python}" -m uvicorn \
  backend.app.main:app \
  --host 127.0.0.1 \
  --port 8000
