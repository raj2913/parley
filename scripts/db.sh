#!/usr/bin/env bash
# Project-local Postgres on port 5433. Data lives in .pgdata/ (git-ignored).
# Usage: scripts/db.sh start|stop|status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/.pgdata"
PORT="${PARLEY_PG_PORT:-5433}"
PG_BIN="${PG_BIN:-$(dirname "$(command -v pg_ctl || echo /opt/homebrew/opt/postgresql@16/bin/pg_ctl)")}"

case "${1:-start}" in
  start)
    if [ ! -d "$DATA" ]; then
      "$PG_BIN/initdb" -D "$DATA" -U parley --auth=trust >/dev/null
    fi
    if ! "$PG_BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      "$PG_BIN/pg_ctl" -D "$DATA" -o "-p $PORT -k /tmp" -l "$DATA/server.log" start >/dev/null
    fi
    for _ in $(seq 1 20); do
      "$PG_BIN/pg_isready" -h localhost -p "$PORT" >/dev/null 2>&1 && break
      sleep 0.5
    done
    for db in parley parley_test; do
      "$PG_BIN/psql" -h localhost -p "$PORT" -U parley -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
        || "$PG_BIN/createdb" -h localhost -p "$PORT" -U parley "$db"
    done
    echo "Postgres running on localhost:$PORT (databases: parley, parley_test)"
    ;;
  stop) "$PG_BIN/pg_ctl" -D "$DATA" stop ;;
  status) "$PG_BIN/pg_ctl" -D "$DATA" status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
