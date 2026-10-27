#!/usr/bin/env bash
# Start everything for local development: Postgres, the API (auto-reload), and the console.
# Ctrl+C stops the API and console; Postgres keeps running (scripts/db.sh stop to stop it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"

"$ROOT/scripts/db.sh" start
(cd "$ROOT/backend" && uv run parley db init >/dev/null)

if [ ! -f "$ROOT/backend/.env" ]; then
  echo "backend/.env is missing: copy backend/.env.example and add a model API key." >&2
  exit 1
fi

for port in 8000 5173; do
  if lsof -ti tcp:$port >/dev/null 2>&1; then
    echo "Port $port is already in use (an earlier run?). Stopping it."
    lsof -ti tcp:$port | xargs kill 2>/dev/null || true
    sleep 1
  fi
done

(cd "$ROOT/backend" && uv run parley serve --reload) &
API=$!
(cd "$ROOT/frontend" && [ -d node_modules ] || npm install >/dev/null; cd "$ROOT/frontend" && npm run dev -- --port 5173 --strictPort) &
WEB=$!
trap 'kill $API $WEB 2>/dev/null; wait 2>/dev/null' INT TERM EXIT

echo
echo "  Parley console:  http://localhost:5173"
echo "  API:             http://localhost:8000/api/config"
echo
wait
