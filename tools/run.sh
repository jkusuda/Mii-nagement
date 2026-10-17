#!/usr/bin/env bash
# Run the whole stack and stop it as one: the backend (and, with --sim, the simulator)
# in the background, the game in front. Used by `make` (real agents) and `make dev`.
#
#   tools/run.sh            backend + game, real agents (MOCK_AGENTS=0)
#   tools/run.sh --sim      backend (--reload) + simulator (fake tasks) + game, mock brains (MOCK_AGENTS=1)
#
# Whichever ends first takes the rest down: Ctrl+C, closing the game window, the backend
# dying, or the terminal going away (SIGHUP). Shutdown is graceful first: the backend gets
# SIGTERM, uvicorn finishes its lifespan shutdown (which stops the engine worker) and exits.
# uvicorn also waits for open connections, so the game's WebSocket is given a few seconds
# (--timeout-graceful-shutdown) and anything still alive after that is killed, including
# whatever still holds the port. Nothing is left behind listening on the port.
#
# Environment: PORT (default 8000), GODOT (path to the Godot executable; empty = no game),
# UV (default uv).

set -u
cd "$(dirname "$0")/.."

PORT=${PORT:-8000}
GODOT=${GODOT:-}
UV=${UV:-uv}
SIM=0
[ "${1:-}" = "--sim" ] && SIM=1

listeners() { command -v lsof >/dev/null && lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null; }

if [ -n "$(listeners)" ]; then
  echo "Port $PORT is already in use (an older backend?). Stop it first:"
  for pid in $(listeners); do echo "  kill $pid    # $(ps -o comm= -p "$pid")"; done
  echo "or use another port: PORT=8001 make"
  exit 1
fi

PIDS=()

stop() {
  trap - EXIT INT TERM HUP
  echo
  echo "stopping..."
  kill -TERM "${PIDS[@]}" 2>/dev/null
  # Up to 10 s for a clean exit (uvicorn shuts the engine down, then closes).
  for _ in $(seq 1 20); do
    alive=0
    for pid in "${PIDS[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [ $alive = 0 ] && break
    sleep 0.5
  done
  # Whatever is left gets killed, including anything still holding the port
  # (it can only be ours: the port was free when we started).
  kill -KILL "${PIDS[@]}" 2>/dev/null
  for pid in $(listeners); do kill -KILL "$pid" 2>/dev/null; done
  wait 2>/dev/null
  echo "stopped"
}
trap stop EXIT
trap 'stop; exit 130' INT TERM HUP

# Real agents by default, mock brains with --sim. Exported before the backend starts, so it
# wins over a root .env and over the saved setting (the environment locks it; see prefs.py).
if [ $SIM = 1 ]; then export MOCK_AGENTS=1; else export MOCK_AGENTS=0; fi

RELOAD=""
[ $SIM = 1 ] && RELOAD="--reload"
# Windows: with --reload, uvicorn picks an event loop that can't start subprocesses, and
# every agent (and git/test command) is one. The Proactor loop can, with or without reload.
LOOP=""
[ "${OS:-}" = "Windows_NT" ] && LOOP="--loop asyncio:ProactorEventLoop"
# shellcheck disable=SC2086  # $RELOAD and $LOOP are empty or flags
(cd backend && exec "$UV" run python -m uvicorn mii.asgi:application --port "$PORT" \
  --timeout-graceful-shutdown 5 $RELOAD $LOOP) &
SERVER=$!
PIDS+=("$SERVER")

for _ in $(seq 1 60); do
  curl -s "localhost:$PORT/health" >/dev/null && break
  kill -0 "$SERVER" 2>/dev/null || break
  sleep 0.5
done
curl -s "localhost:$PORT/health" >/dev/null || { echo "backend did not start"; exit 1; }
echo "backend ready at http://localhost:$PORT"

if [ $SIM = 1 ]; then
  (cd backend && exec "$UV" run python manage.py seed --loop --url "http://localhost:$PORT") &
  PIDS+=("$!")
fi

if [ -z "$GODOT" ]; then
  echo "Godot not found: backend running, Ctrl+C to stop"
  wait "$SERVER"
  exit 0
fi

MII_SERVER="localhost:$PORT" "$GODOT" --path game &
GAME=$!
PIDS+=("$GAME")

# Watch the backend and the game; the first to end stops everything (see stop()).
while kill -0 "$SERVER" 2>/dev/null && kill -0 "$GAME" 2>/dev/null; do sleep 0.5; done
kill -0 "$SERVER" 2>/dev/null || echo "backend exited"
