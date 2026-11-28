#!/usr/bin/env bash
# Local development only. Never reads, replaces or deploys production secrets.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$PWD"
DB_NAME=scadable-dev-postgres
DB_PORT="${SCADABLE_DB_PORT:-5432}"
if ! [[ "$DB_PORT" =~ ^[1-9][0-9]{0,4}$ ]] || (( DB_PORT > 65535 )); then
  echo "Setup: Invalid SCADABLE_DB_PORT." >&2
  exit 1
fi
DB_URL=postgresql+psycopg://scadable:scadable_local_only@127.0.0.1:${DB_PORT}/live_swot
MODE=${1:-setup}
fail() { printf 'Setup: %s\n' "$*" >&2; exit 1; }
usage() { printf '%s\n' 'Usage: ./setup.sh [--check|--prepare|--start|--help]' 'Default: install dependencies, start local PostgreSQL and initialize tables.' '--check: check prerequisites only; --prepare: create missing env files only.' '--start: perform setup and launch both local servers (Ctrl-C stops them).'; }
case "$MODE" in setup|--check|--prepare|--start) ;; --help|-h) usage; exit 0;; *) usage; exit 2;; esac
[ "$#" -le 1 ] || fail 'Only one option is supported.'

prepare() {
  if [ ! -e .env ]; then (umask 077; cp .env.example .env); fi
  if [ ! -e backend/.env ]; then
    (umask 077; sed "s|^DATABASE_URL=.*|DATABASE_URL=$DB_URL|" backend/.env.example > backend/.env)
  fi
  if [ ! -e frontend/.env.local ]; then (umask 077; cp frontend/.env.example frontend/.env.local); fi
  printf '%s\n' 'Environment files prepared (existing files preserved).'
}

if [ "$MODE" = --prepare ]; then prepare; exit 0; fi
command -v node >/dev/null || fail 'Install Node.js 22.'
[ "$(node -p 'process.versions.node.split(".")[0]')" = 22 ] || fail 'Use Node.js 22 (matches CI).'
command -v npm >/dev/null || fail 'Install npm with Node.js.'
command -v docker >/dev/null || fail 'Install Docker Desktop or Docker Engine.'
docker info >/dev/null 2>&1 || fail 'Start Docker before running setup.'
if command -v uv >/dev/null; then
  PYTHON_MODE=uv
elif command -v python3.12 >/dev/null; then
  PYTHON_MODE=python
else
  fail 'Install Python 3.12 or uv (see DEVELOPERS.md).'
fi
printf '%s\n' 'Prerequisites available.'
[ "$MODE" != --check ] || exit 0
prepare
# Refuse to operate on any pre-existing custom/remote database configuration.
# Use the manual setup instructions if you intentionally use another local DB.
[ "$(grep '^DATABASE_URL=' backend/.env || true)" = "DATABASE_URL=$DB_URL" ] || fail 'backend/.env has a custom DATABASE_URL. Existing files are untouched; follow the manual setup guide.'
if [ -n "${DATABASE_URL:-}" ] && [ "$DATABASE_URL" != "$DB_URL" ]; then fail 'Unset your shell DATABASE_URL before setup.'; fi

if docker container inspect "$DB_NAME" >/dev/null 2>&1; then
  [ "$(docker inspect -f '{{ index .Config.Labels "live-swot.checkout" }}' "$DB_NAME")" = "$ROOT" ] || fail "Container $DB_NAME already belongs to another setup. Use manual setup or remove it yourself if safe."
  docker start "$DB_NAME" >/dev/null
else
  docker run -d --name "$DB_NAME" --label "live-swot.checkout=$ROOT" \
    -e POSTGRES_USER=scadable -e POSTGRES_PASSWORD=scadable_local_only \
    -e POSTGRES_DB=live_swot -p "127.0.0.1:${DB_PORT}:5432" \
    postgres:16 >/dev/null
fi
for ((attempt=0; attempt<60; attempt++)); do
  if docker exec "$DB_NAME" pg_isready -U scadable -d live_swot >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$DB_NAME" pg_isready -U scadable -d live_swot >/dev/null || fail 'PostgreSQL did not become ready.'

if [ ! -x backend/.venv/bin/python ]; then
  if [ "$PYTHON_MODE" = uv ]; then uv venv --python 3.12 backend/.venv; else python3.12 -m venv backend/.venv; fi
fi
PY="$ROOT/backend/.venv/bin/python"
[ "$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = 3.12 ] || fail 'Existing backend/.venv is not Python 3.12. Recreate it manually.'
if [ "$PYTHON_MODE" = uv ]; then
  uv pip install --python "$PY" -r backend/requirements.txt ./scraper -e ./llm -e '.[dev]'
else
  "$PY" -m pip install -r backend/requirements.txt ./scraper -e ./llm -e '.[dev]'
fi
mkdir -p scraper/.uv-cache/playwright
PLAYWRIGHT_BROWSERS_PATH="$ROOT/scraper/.uv-cache/playwright" "$PY" -m playwright install chromium
(cd backend && DATABASE_URL="$DB_URL" "$PY" -m app.db.init_db)
(cd frontend && npm ci)
printf '\n%s\n' 'Setup complete. Configure development credentials if you need real research.' \
  'Backend:  cd backend && .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app' \
  'Frontend: cd frontend && npm run dev' \
  'Dashboard: http://localhost:3000   API docs: http://localhost:8000/docs' \
  'Database remains running; stop it with docker stop scadable-dev-postgres.'

if [ "$MODE" = --start ]; then
  # Keep startup local and prevent automatic paid background work for this launch.
  (cd backend && exec env DATABASE_URL="$DB_URL" AUTO_REFRESH_ENABLED=false GITHUB_SYNC_ENABLED=false LANGFUSE_COST_RECOVERY_ENABLED=false "$PY" -m uvicorn app.main:app --host 127.0.0.1 --reload --reload-dir app) &
  backend_pid=$!
  (cd frontend && exec ./node_modules/.bin/next dev --turbopack --hostname 127.0.0.1) &
  frontend_pid=$!
  cleanup() { kill "$backend_pid" "$frontend_pid" 2>/dev/null || true; wait "$backend_pid" "$frontend_pid" 2>/dev/null || true; }
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  # Bash 3.2 (macOS) has no wait -n. Stop both if either launcher exits.
  while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do sleep 1; done
  fail 'A local server exited; inspect the output above.'
fi
