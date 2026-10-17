# Mii-nagement: one command to run everything.
#
#   make           same as `make up`: backend + simulator + the game, Ctrl+C stops all three
#   make setup     install deps (first time; also run by `make up`). No .env needed.
#   make up        backend + simulator + game
#   make real      backend (no reload, no simulator) + game: for real agents (MOCK_AGENTS=0)
#   make server    just the backend (http://localhost:8000)
#   make sim       just the simulator (fake tasks; needs the backend)
#   make watch     print the live event stream (needs the backend)
#   make game      just the game
#   make test      backend tests + Godot script check
#   make reset     wipe the database (stop the server first)
#
# Needs: uv (https://docs.astral.sh/uv), Godot 4 (set GODOT=/path/to/godot if it isn't found).

SHELL := /bin/bash
BACKEND := backend
UV := uv
PORT ?= 8000

# Find Godot: $GODOT, then `godot` on PATH, then the macOS app bundle.
GODOT ?= $(shell command -v godot 2>/dev/null || ([ -x /Applications/Godot.app/Contents/MacOS/Godot ] && echo /Applications/Godot.app/Contents/MacOS/Godot))

.PHONY: all up real setup server sim watch game test reset check-tools

all: up

check-tools:
	@command -v $(UV) >/dev/null || { echo "uv is missing. macOS: brew install uv   Linux: curl -LsSf https://astral.sh/uv/install.sh | sh   Windows: winget install astral-sh.uv"; exit 1; }
	@[ -n "$(GODOT)" ] || echo "Godot not found: install Godot 4 and run 'make GODOT=/path/to/godot ...' (backend still works)"

setup: check-tools
	@cd $(BACKEND) && $(UV) sync
	@[ -z "$(GODOT)" ] || [ -d game/.godot ] || "$(GODOT)" --headless --path game --import >/dev/null 2>&1 || true
	@echo "setup done"

server: setup
	cd $(BACKEND) && $(UV) run python -m uvicorn mii.asgi:application --reload --port $(PORT)

sim:
	cd $(BACKEND) && $(UV) run python manage.py seed --loop --url http://localhost:$(PORT)

watch:
	cd $(BACKEND) && $(UV) run python manage.py watch ws://localhost:$(PORT)/ws

game:
	@[ -n "$(GODOT)" ] || { echo "Godot not found (set GODOT=/path/to/godot)"; exit 1; }
	"$(GODOT)" --path game

# Backend + simulator in the background, the game in front. Ctrl+C (or closing the game) stops everything.
up: setup
	@set -e; \
	cd $(BACKEND) && $(UV) run python -m uvicorn mii.asgi:application --port $(PORT) & SERVER=$$!; \
	trap 'kill $$SERVER $$SIM 2>/dev/null; wait $$SERVER $$SIM 2>/dev/null; echo; echo "stopped"' EXIT INT TERM; \
	for i in $$(seq 1 60); do curl -s localhost:$(PORT)/health >/dev/null && break; sleep 0.5; done; \
	curl -s localhost:$(PORT)/health >/dev/null || { echo "backend did not start"; exit 1; }; \
	echo "backend ready at http://localhost:$(PORT)"; \
	(cd $(BACKEND) && $(UV) run python manage.py seed --loop --url http://localhost:$(PORT)) & SIM=$$!; \
	if [ -n "$(GODOT)" ]; then "$(GODOT)" --path game; else echo "Godot not found: backend + simulator running, Ctrl+C to stop"; wait $$SERVER; fi

# Real agents: no simulator (its fake tasks would become real agent work) and no
# --reload (editing the code would restart the server and stop a running task).
real: setup
	@if lsof -nP -iTCP:$(PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Port $(PORT) is already in use (an older backend?). Stop it first:"; \
		lsof -nP -iTCP:$(PORT) -sTCP:LISTEN | tail -n +2 | awk '{print "  kill " $$2 "    # " $$1}' | sort -u; \
		echo "or use another port: make real PORT=8001"; \
		exit 1; \
	fi
	@set -e; \
	cd $(BACKEND) && $(UV) run python -m uvicorn mii.asgi:application --port $(PORT) & SERVER=$$!; \
	trap 'kill $$SERVER 2>/dev/null; wait $$SERVER 2>/dev/null; echo; echo "stopped"' EXIT INT TERM; \
	for i in $$(seq 1 60); do curl -s localhost:$(PORT)/health >/dev/null && break; sleep 0.5; done; \
	curl -s localhost:$(PORT)/health >/dev/null || { echo "backend did not start (is another one on port $(PORT)?)"; exit 1; }; \
	echo "backend ready at http://localhost:$(PORT)"; \
	if [ -n "$(GODOT)" ]; then MII_SERVER=localhost:$(PORT) "$(GODOT)" --path game; else echo "Godot not found: backend running, Ctrl+C to stop"; wait $$SERVER; fi

test: setup
	cd $(BACKEND) && $(UV) run pytest -q
	@[ -z "$(GODOT)" ] || "$(GODOT)" --headless --path game --quit 2>&1 | grep -i "error" || true

reset:
	cd $(BACKEND) && $(UV) run python manage.py seed --reset
