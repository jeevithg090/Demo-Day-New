#!/usr/bin/env bash
# Local development launcher: creates a venv, installs deps, starts Redis if
# available, seeds sample data and runs the server with auto-reload.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if [ ! -d venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f .env ] && [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "WARNING: GEMINI_API_KEY not set (copy .env.example to .env). LLM replies will use a fallback message."
fi

python3 -c "import torch; print('CUDA available:', torch.cuda.is_available())"

if command -v redis-cli >/dev/null 2>&1; then
  if ! redis-cli ping >/dev/null 2>&1 && command -v redis-server >/dev/null 2>&1; then
    echo "Starting Redis..."
    redis-server --daemonize yes
    sleep 1
  fi
  if redis-cli ping >/dev/null 2>&1; then
    echo "Seeding hospital data into Redis..."
    python3 scripts/seed_data.py --clear || echo "Seeding failed; continuing."
  fi
else
  echo "Redis not installed; using in-memory sessions and data/sample_hospital.json."
fi

echo ""
echo "  Main UI:  http://localhost:${PORT}"
echo "  Admin:    http://localhost:${PORT}/admin"
echo "  Health:   http://localhost:${PORT}/health"
echo "  API docs: http://localhost:${PORT}/docs"
echo ""
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --reload
