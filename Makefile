# Mii-nagement: one command to run everything.
#
#   make           same as `make up`: backend (no reload, no simulator) + the game, for real agents
#   make setup     install deps (first time; also run by `make up`). No .env needed.
#   make up        backend + game with real agents (sets MOCK_AGENTS=0, whatever .env or the settings say)
#   make dev       backend (--reload) + simulator (fake tasks) + game: mock-mode development
#   make server    just the backend (http://localhost:8000)
#   make sim       just the simulator (fake tasks; needs the backend)
#   make watch     print the live event stream (needs the backend)
#   make game      just the game
#   make check     lint + test: run this after every change
#   make lint      ruff (backend) + gdlint / gdformat --check (game)
#   make fmt       ruff format (backend) + gdformat (game)
#   make test      backend tests + Godot script check
#   make godot-check   load the game headless and fail on any script error
#   make reset     wipe the database (stop the server first)
#   make web       export the game for browsers into game/builds/web (deploy/README.md)
#
# Needs: uv (https://docs.astral.sh/uv) and Godot 4. Godot is found on PATH or in
# /Applications; anywhere else, pass it: `make GODOT=/path/to/godot check`.
# gdlint and gdformat run through `uvx`, so nothing else is installed.

SHELL := /bin/bash
BACKEND := backend
UV := uv
GDTOOLKIT := uvx --from 'gdtoolkit>=4'
PORT ?= 8000
# Windows: --reload makes uvicorn use an event loop that can't start agent processes (tools/run.sh).
UVICORN_LOOP := $(if $(filter Windows_NT,$(OS)),--loop asyncio:ProactorEventLoop)

# Find Godot: $GODOT, then `godot` on PATH, then the macOS app bundle.
GODOT ?= $(shell command -v godot 2>/dev/null || ([ -x /Applications/Godot.app/Contents/MacOS/Godot ] && echo /Applications/Godot.app/Contents/MacOS/Godot))

.PHONY: all help up dev setup server sim watch game check lint fmt test godot-check reset check-tools web

all: up

help:
	@sed -n '2,21p' Makefile | sed 's/^#//'

check-tools:
	@command -v $(UV) >/dev/null || { echo "uv is missing. macOS: brew install uv   Linux: curl -LsSf https://astral.sh/uv/install.sh | sh   Windows: winget install astral-sh.uv"; exit 1; }
	@[ -n "$(GODOT)" ] || echo "Godot not found: install Godot 4 and run 'make GODOT=/path/to/godot ...' (backend still works)"

setup: check-tools
	@cd $(BACKEND) && $(UV) sync
	@[ -z "$(GODOT)" ] || [ -d game/.godot ] || "$(GODOT)" --headless --path game --import >/dev/null 2>&1 || true
	@echo "setup done"

server: setup
	cd $(BACKEND) && $(UV) run python -m uvicorn mii.asgi:application --reload --port $(PORT) $(UVICORN_LOOP)

sim:
	cd $(BACKEND) && $(UV) run python manage.py seed --loop --url http://localhost:$(PORT)

watch:
	cd $(BACKEND) && $(UV) run python manage.py watch ws://localhost:$(PORT)/ws

game:
	@[ -n "$(GODOT)" ] || { echo "Godot not found (set GODOT=/path/to/godot)"; exit 1; }
	"$(GODOT)" --path game

# Both run tools/run.sh: backend (+ simulator) in the background, the game in front.
# Ctrl+C, closing the game, or the backend dying stops everything; nothing stays on the port.

# Real agents (the default `make`): no simulator (its fake tasks would become real agent
# work) and no --reload (editing the code would restart the server and stop a running task).
up: setup
	@PORT=$(PORT) GODOT="$(GODOT)" UV="$(UV)" tools/run.sh

# Mock-mode development: backend (--reload) + the simulator posting fake tasks.
dev: setup
	@PORT=$(PORT) GODOT="$(GODOT)" UV="$(UV)" tools/run.sh --sim

# --- Checks -----------------------------------------------------------------------

check: lint test

lint:
	cd $(BACKEND) && $(UV) run ruff check . && $(UV) run ruff format --check .
	$(GDTOOLKIT) gdlint game
	$(GDTOOLKIT) gdformat --check -l 120 game

fmt:
	cd $(BACKEND) && $(UV) run ruff check --fix . && $(UV) run ruff format .
	$(GDTOOLKIT) gdformat -l 120 game

test: godot-check
	cd $(BACKEND) && $(UV) run pytest -q

# Load every script without a window. Godot exits 0 even when a script fails to
# parse, so the output is searched for errors and the target fails on any.
godot-check:
	@[ -n "$(GODOT)" ] || { echo "Godot not found: skipping the script check (set GODOT=/path/to/godot)"; exit 0; }
	@[ -d game/.godot ] || "$(GODOT)" --headless --path game --import >/dev/null 2>&1 || true
	@out=$$("$(GODOT)" --headless --path game --quit 2>&1); \
	if echo "$$out" | grep -qiE 'SCRIPT ERROR|ERROR:|Parse Error'; then echo "$$out" | grep -iE -B1 -A3 'SCRIPT ERROR|ERROR:|Parse Error'; echo "Godot script check failed"; exit 1; fi; \
	echo "Godot script check passed"

reset:
	cd $(BACKEND) && $(UV) run python manage.py seed --reset

# Needs Godot's web export templates (Editor > Manage Export Templates).
web:
	@[ -n "$(GODOT)" ] || { echo "Godot not found (set GODOT=/path/to/godot)"; exit 1; }
	@mkdir -p game/builds/web
	"$(GODOT)" --headless --path game --export-release Web builds/web/index.html
