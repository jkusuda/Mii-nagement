# CLAUDE.md

Context for Claude Code sessions working on this repo.

## What this is

**Mii-nagement**: a Tomodachi Life-style multi-agent manager for real software
work (hackathon project). An *island* is a project: a local git repository
(created with the island; see `islands.py`), optionally linked to a GitHub repo.
*Residents* have jobs and personality sliders, and a *brain*: a coding agent
(Claude Code, Codex, Gemini CLI) driven over the Agent Client Protocol (ACP) on the
user's subscription, `openrouter` (any OpenRouter model, no CLI), or `mock`. *Buildings* are
workflow steps and *roads* are handoffs (with `always` / `pass` / `fail`
conditions). A *task package* travels the road graph.

Two halves talk over one WebSocket:

- `backend/`: Python 3.12, uv, Django + Channels (ASGI via uvicorn), Django ORM +
  SQLite, pytest-django. Agents over ACP, plus the OpenRouter brain.
  Owns **all** logic: workflow engine, agents, persistence, safety.
- `game/`: Godot 4 (GDScript), a 3D island inside a 2D UI, 1280×720. **Display and input only.** It
  renders events and sends user input. Never put agent or workflow logic here.

**Protocol:** [docs/protocol.md](docs/protocol.md) is the single source of truth
for every message. Change it first, then update both halves to match.
**Backend design:** [docs/architecture.md](docs/architecture.md).

## Structure

```
backend/manage.py Django CLI: migrate, makemigrations mii, seed [--reset|--loop], watch
backend/mii/      one package = Django project + app
  settings.py     Django settings (reads config.py)
  config.py       developer overrides from the optional root .env (`config`) + defaults
  prefs.py        user settings (Setting table) + API keys (OS keychain via `keyring`); env > saved > default
  asgi.py         ASGI entry: HTTP + WebSocket + lifespan (starts the engine worker)
  urls.py / views.py   async JSON endpoints: /health, /brains, /models, /settings, /islands/{id}, tasks, insights, events, replay
  tasks.py        submit_task: a request ({"title"} or {"issue"}) -> a queued task (HTTP POST + `create_task`)
  consumers.py    the /ws WebSocket consumer (Channels, no channel layer)
  runtime.py      one sandbox / event bus / bubbles / engine per process (get_runtime())
  models.py       tables (Island, Resident, Building, Road, Task, Step, Event) + snapshot
  migrations/     Django migrations (checked in)
  islands.py      an island = a git repo + its map: create_island() (shape + steps), SHAPES,
                  build_roads (steps -> roads), validate_map / set_map (the game's build mode),
                  default_folder(), ensure_repo(), delete_island()
  personality.py  Resident -> prompt preamble
  events.py       event bus (persist to SQLite + broadcast) + bubbles
  engine.py       island graph + building handlers (commit, test run, PR) + task loop
  residents.py    create_resident / update_resident / delete_resident (the resident creator; `buildings` = works at)
  layout.py       workflow editing by hand: validate + save buildings/roads (set_layout,
                  PUT /islands/{id}/layout without a "map")
  weather.py      island weather (sunny/cloudy/rainy/night) from recent steps; snapshot field + `weather` event
  sandbox.py      Sandboxes (one per island repo) + worktree per task + commit_all/diff_stat + is_inside()
  agents/         __init__ (AgentJob, BrainRunner, make_runner, parse_verdict, permission_for), mock.py,
                  acp.py (AcpRunner + BRAINS + GIFTS tables), openrouter.py (OpenRouterRunner)
  github.py       repo + branch lists via `gh`; issue in / PR out
  insights.py     the Insights payload: Step + Task tables + stored bubble events, per range
  replay.py       replay a recorded task into the game
  seed.py         seed island + simulator (via `manage.py seed`)
  watch.py        tiny WebSocket client that prints events (via `manage.py watch`)
  management/commands/   seed, watch
backend/tests/    pytest (pytest-django; fake_acp_agent.py is a mini ACP agent)
game/
  net.gd          autoload singleton `Net`: WebSocket -> signals, send_answer(), visit_island(),
                  http_url() + request_json() for the HTTP endpoints
  islands.gd      autoload `Islands`: every island (GET /islands + snapshots) + 3D thumbnails
  clock.gd        autoload `Clock`: real time of day (+ dev override), Day N from created_at, daily weather
  scenes/game.tscn     MAIN SCENE: top bar, framed 3D island, nav, Bubbles sidebar. Driven by
                       Net.island (kept live by net.gd); day/time/weather from Clock.
  scenes/island_3d.tscn  3D world (sun, water, camera, the Island builder, residents); rendered
                         inside game.tscn's SubViewport
  scenes/day_night.gd    `DayNight`: the time-of-day table; moves the sun, recolors light and water in
                         every copy of island_3d.tscn (island view, home sea, the window background)
  scenes/camera_rig.gd   zoom (wheel/trackpad/buttons) + drag-to-pan, click vs. drag, focus_on()/unfocus()
  scenes/apple_tree.tscn, bush.tscn  plants (origin at their base), both use swaying_plant.gd
  scenes/seasons.gd      `Seasons`: per-season looks (grass, foliage, snow, weather particles, light tint)
  scenes/buildings/      one wrapper scene per building type (model on its 3x3 lot, door facing +Z,
                         porch light at night; building.gd)
  scenes/build/          build_mode.gd: Build mode (move buildings, land, plants, steps, pond, season)
  scenes/residents/    Residents tab (residents_screen, resident_card), resident_creator,
                       resident_data (jobs/brains/keys/gifts lists + mock residents)
  scenes/home/         Archipelago home screen (3D sea, plates, Waiting on you), island_plate,
                       island_dropdown (switcher in the top bar), island_creator (found an island)
  scenes/sea/          sea_view: a 3D sea with any number of islands (water shader, sun, camera fit);
                       with none it's open sea, used as game.tscn's full-window Background
  scenes/news/         News tab: news_stories turns Net events (+ GET /islands/{id}/events)
                       into stories; unread count drives the nav badge; daily digest setting;
                       Replay on shipped stories (POST /replay/{task_id})
  scenes/insights/     Insights tab: GET /islands/{id}/insights (shape in architecture.md) + live
                       waiting packages and moods from Net.island
  scenes/mii/          mii (the 3D Mii: tint + idle/walk/run), mii_preview (creator's 3D stage)
  scenes/island/       the island, built from data (snapshot `layout` + buildings):
                       island_layout (tiles, shapes, rules, paths between doors, auto plants),
                       island_terrain (ground mesh + "field" texture), island_builder (the Island
                       node: ground, pond, buildings, plants, lamps); residents: island_residents
                       (follows Net events), island_resident (one walker + tags), island_paths (walking)
                       building_data (per-type name/icon/color/stage words), building_picker
                       (which building is under the mouse: ray vs. bounding boxes)
  scenes/ui/           building_label (follows a 3D building, resident count), bubbles_sidebar,
                       bubble_card, answer_popup (answer a bubble: review, key request, question),
                       confirm_popup ("are you sure?"), toast (server errors), answer_button, dashed_button,
                       building_panel (click a building: Mailbox issues + send, or what's at
                       that stage), package_form (POST /islands/{id}/tasks),
                       settings_popup (GET/PUT /settings, /health, /brains/detail)
  ui/theme.tres        every color, font weight, and pill/button style (theme type variations)
  shaders/water.gdshader stylized water
  shaders/terrain.gdshader  grass, beach, paths, pond banks, snow (reads the island's field texture)
  shaders/pond.gdshader  the pond's water (shimmer, ripples, ice in winter)
  shaders/season_overlay.gdshader  material_overlay on plants/buildings: autumn colors, blossoms,
                       snow, lit windows at night
  shaders/lamp.gdshader  path lamp heads (glow with the global `night` shader parameter)
  assets/models/materials/  building materials (used via each .glb.import). Mipmaps are OFF on
                       purpose: the AI texture atlases pack islands edge to edge, so mipmaps bleed
                       neighbouring texels along UV seams (white streaks on roofs)
  assets/icons/        Lucide icons (ISC license), recolored white so modulate can tint them
  assets/font/         DreamLife (single weight; heavier weights are FontVariation embolden)
  assets/ui/           nav button illustrations
  assets/models/  .glb models (5 buildings, apple_tree, bush, mii.glb (the blank Mii; see
                  docs/mii-customization.md)). AI-generated, normalized to ~1.9 units and centered
                  on their middle, so the scenes scale them and lift them onto the grass.
                  base_island.glb is the old hand-made island, no longer used (the ground is
                  generated now); kept for comparison, safe to delete
                  (*.gd.uid files are generated by Godot 4.4+; keep them next to their scripts)
docs/godot-tour.md  beginner tour of the Godot project
docs/mii-customization.md  the Mii model, how creator fields reach ACP, customization plan
```

## Commands

All backend commands run from `backend/`:

```bash
uv sync                                                        # install
uv run python -m uvicorn mii.asgi:application --reload --port 8000   # server (migrates + seeds at startup)
uv run python manage.py seed --loop                            # simulator
uv run python manage.py watch                                  # print event stream
uv run python manage.py makemigrations mii                     # after changing models.py
uv run pytest                                                  # tests
```

Godot is **not on PATH** on this machine. Use the full path:

```bash
/Applications/Godot.app/Contents/MacOS/Godot --headless --path game --import  # first time: build .godot/ cache
/Applications/Godot.app/Contents/MacOS/Godot --headless --path game --quit    # script check
/Applications/Godot.app/Contents/MacOS/Godot --path game                      # play
MII_SERVER=localhost:8001 /Applications/Godot.app/Contents/MacOS/Godot --path game  # play against another backend
```

On the Windows machine, Godot 4.7.2 lives in Downloads (use the `_console.exe` for headless runs):

```bash
"C:\Users\lucas\Downloads\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64_console.exe" --headless --path game --quit
"C:\Users\lucas\Downloads\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64.exe" --editor --path game  # open the editor
```

Windows tooling lives in `~/.local/bin` (on the user PATH): `uv`, `gh`, GNU `make`, and a
`godot` shell script that launches the Downloads console build, so `make` works from Git
Bash. A TLS-intercepting proxy/antivirus is on this machine: `UV_SYSTEM_CERTS=1` (user
env var) and `git config --global http.sslBackend schannel` make uv and git trust it.

## Conventions

- **TODO markers:** `# TODO(mvp): ...` or `# TODO(stretch): ...` (same format in
  GDScript). Keep them greppable; the roadmap lives in [TODO.md](TODO.md).
- **Simple, readable code over clever code.** The owner is comfortable with
  Python but is **new to Godot**. When introducing a Godot concept for the first
  time (nodes, scenes, signals, autoloads, `_process`, SubViewport, Tween, etc.),
  explain it briefly in a comment or in chat.
- **Brains over ACP first.** Real residents are ACP coding agents (`claude-code`,
  `codex`, `gemini`), all through one `AcpRunner`; each brain is an entry in the
  `BRAINS` table in `agents/acp.py`. Agents authenticate the way their CLI does
  (subscription login first, API key env var as fallback). The one direct-API
  brain is `openrouter` (`agents/openrouter.py`): any OpenRouter model, no CLI.
  Keep brain-specific details inside those runners, never in the engine,
  protocol, or game.
- **Keep the backend small.** Out of scope on purpose: LangChain or more direct
  API runners (OpenRouter is the one), ML router, several packages at once on
  one island. Islands work in parallel (one `Lane` per island in engine.py),
  one package at a time each. Island News is written in the game
  from stored events; the backend only serves them.
- **Migrations are Django's.** After changing a table in `models.py`, run
  `uv run python manage.py makemigrations mii` and check the migration in. The
  server runs `migrate` at startup. `manage.py seed --reset` wipes the rows.
- **Mock mode is the default** (`MOCK_AGENTS=1`): fake work, no API cost. Every
  feature should keep working in mock mode.
- **No `.env` required.** Users change settings through `PUT /settings`
  (`prefs.py`). Secrets go to the OS keychain, never the database or the
  snapshot; the environment (a developer's `.env`) wins over saved values.
- **No machine-specific defaults.** Paths come from `Path.home()` / `.env`, never
  from a hardcoded user folder, so the project runs on any macOS, Windows or Linux box.
- **Safety:** agents may only read and write inside git worktrees of the
  island's own repository; shell commands are gated by a ✋ bubble. Never loosen
  `sandbox.py` checks to make something work. The engine is async: use the
  async ORM (`aget`/`asave`/`async for`), and `asyncio.to_thread` for subprocesses.
- Keep `.tscn` files simple. Put logic in `.gd` files and build nodes in code
  where that's clearer.
- Islands are data (`Island.map` + building lots, docs/protocol.md "Island
  map"); the game builds the 3D island from it (`scenes/island/`). Keep the
  shape presets and rules in `IslandLayout` (GDScript) and `islands.py` in step.
- Residents walk along the roads in `scenes/island/` (`island_residents.gd` picks
  the route, `island_paths.gd` follows the sand path). The owner asked for it to
  be written; it's still a good file to read when learning Godot.
- Git: the repo lives on GitHub (`origin`, branch `main`). Commit or push only when the owner asks.
