# CLAUDE.md

Context for Claude Code sessions working on this repo.

## What this is

**Mii-nagement**: a Tomodachi Life-style multi-agent manager for real software
work (hackathon project). An *island* is a project linked to a GitHub repo.
*Residents* have jobs and personality sliders, and a *brain*: a coding agent
(Claude Code, Codex, Gemini CLI) driven over the Agent Client Protocol (ACP), or `mock`. *Buildings* are
workflow steps and *roads* are handoffs (with `always` / `pass` / `fail`
conditions). A *task package* travels the road graph.

Two halves talk over one WebSocket:

- `backend/`: Python 3.12, uv, FastAPI, SQLModel + SQLite, pytest. Agents over ACP.
  Owns **all** logic: workflow engine, agents, persistence, safety.
- `game/`: Godot 4 (GDScript), a 3D island inside a 2D UI, 1280×720. **Display and input only.** It
  renders events and sends user input. Never put agent or workflow logic here.

**Protocol:** [docs/protocol.md](docs/protocol.md) is the single source of truth
for every message. Change it first, then update both halves to match.
**Backend design:** [docs/architecture.md](docs/architecture.md).

## Structure

```
backend/mii/
  config.py       settings loaded from the root .env
  models.py       tables (Island, Resident, Building, Road, Task, Step, Event) + DB setup + snapshot
  personality.py  Resident -> prompt preamble
  events.py       event bus (persist to SQLite + broadcast) + bubbles
  engine.py       island graph + building handlers + task loop (step timeout, Step rows)
  residents.py    create_resident / update_resident (the resident creator)
  islands.py      POST /islands: found an island from a town plan
  sandbox.py      one git worktree per task + is_inside()
  agents/         __init__ (AgentJob, BrainRunner, make_runner), mock.py, acp.py (BRAINS table)
  github.py       repo + branch lists via `gh`; issue in / PR out (boilerplate)
  insights.py     stats from the Step table                     (mostly boilerplate)
  replay.py       replay a recorded task into the game          (boilerplate)
  server.py       FastAPI app: /ws, /health, /brains, /islands/{id}, tasks, insights, replay
  seed.py         seed island; `--loop` = simulator
  watch.py        tiny WebSocket client that prints events
backend/tests/    pytest
game/
  net.gd          autoload singleton `Net`: WebSocket -> signals, send_answer(), visit_island()
  islands.gd      autoload `Islands`: every island (GET /islands + snapshots) + 3D thumbnails
  scenes/game.tscn     MAIN SCENE: top bar, framed 3D island, nav, Bubbles sidebar. Driven by
                       Net.island (kept live by net.gd); day/weather are still placeholders.
  scenes/island_3d.tscn  3D world (models, sun, water); rendered inside game.tscn's SubViewport
  scenes/camera_rig.gd   zoom (wheel/trackpad/buttons) + drag-to-pan
  scenes/apple_tree.tscn, bush.tscn  plants (origin at their base), instanced under Trees/Bushes in
                       island_3d.tscn; both use swaying_plant.gd. Bushes cover the patches left where
                       the trees baked into base_island.glb were removed: keep those spots covered
                       (even under buildings, which are hidden on islands that lack them). New
                       island decorations also go in SeaView.ISLAND_PARTS (scenes/sea/sea_view.gd)
  scenes/residents/    Residents tab (residents_screen, resident_card), resident_creator,
                       resident_data (jobs/brains/keys/gifts lists + mock residents)
  scenes/home/         Archipelago home screen (3D sea, plates, Waiting on you), island_plate,
                       island_dropdown (switcher in the top bar), island_creator (found an island)
  scenes/sea/          sea_view: a 3D sea with any number of islands (water shader, sun, camera fit)
  scenes/news/         News tab: news_stories turns Net events (+ GET /islands/{id}/events, planned)
                       into stories; unread count drives the nav badge; daily digest setting
  scenes/insights/     Insights tab: GET /islands/{id}/insights (shape in architecture.md) + live
                       waiting packages and moods from Net.island
  scenes/mii/          mii (the 3D Mii: tint + idle/walk/run), mii_preview (creator's 3D stage)
  scenes/island/       residents on the 3D island: island_residents (follows Net events, routes
                       along roads), island_resident (one walker + tags), island_paths (sand path)
  scenes/ui/           building_label (follows a 3D building, resident count), bubbles_sidebar,
                       bubble_card (with reply box), answer_button, dashed_button
  ui/theme.tres        every color, font weight, and pill/button style (theme type variations)
  shaders/water.gdshader stylized water
  shaders/pond.gdshader  additive shimmer + ripple rings over the pond painted into the island model
  assets/models/materials/  building + island materials (used via each .glb.import). Mipmaps are
                       OFF on purpose: the AI texture atlases pack islands edge to edge, so mipmaps
                       bleed neighbouring texels along UV seams (white streaks on roofs/dock, blue
                       streaks on grass)
  assets/icons/        Lucide icons (ISC license), recolored white so modulate can tint them
  assets/font/         DreamLife (single weight; heavier weights are FontVariation embolden)
  assets/ui/           nav button illustrations
  assets/models/  .glb models (island + 5 buildings + mii.glb, the blank Mii; see docs/mii-customization.md). AI-generated, normalized to ~1.9 units and
                  centered on their middle, so the scene scales them and lifts them onto the grass
                  (*.gd.uid files are generated by Godot 4.4+; keep them next to their scripts)
docs/godot-tour.md  beginner tour of the Godot project
docs/mii-customization.md  the Mii model, how creator fields reach ACP, customization plan
```

## Commands

All backend commands run from `backend/`:

```bash
uv sync                                          # install
uv run uvicorn mii.server:app --reload --port 8000   # server
uv run python -m mii.seed --loop                 # simulator
uv run python -m mii.watch                       # print event stream
uv run pytest                                    # tests
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
"C:\Users\lucas\Downloads\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64.exe" --editor --path game res://scenes/island_3d.tscn  # edit island layout
```

## Conventions

- **TODO markers:** `# TODO(mvp): ...` or `# TODO(stretch): ...` (same format in
  GDScript). Keep them greppable; the roadmap lives in [TODO.md](TODO.md).
- **Simple, readable code over clever code.** The owner is comfortable with
  Python but is **new to Godot**. When introducing a Godot concept for the first
  time (nodes, scenes, signals, autoloads, `_process`, SubViewport, Tween, etc.),
  explain it briefly in a comment or in chat.
- **Brains over ACP.** Real residents are ACP coding agents (`claude-code`,
  `codex`, `gemini`), all through one `AcpRunner`; each brain is an entry in the
  `BRAINS` table in `agents/acp.py`. Agents authenticate the way their CLI does
  (subscription login or API key env var). Keep brain-specific details inside
  that table/runner, never in the engine, protocol, or game.
- **Keep the backend small.** Out of scope on purpose: LangChain/direct API
  runners, friendship, ML router, parallel tasks. Island News
  is written in the game from stored events; the backend only serves them.
- **No migrations yet.** After changing a table in `models.py`, reset the dev
  database: `uv run python -m mii.seed --reset` (stop the server first).
- **Mock mode is the default** (`MOCK_AGENTS=1`): fake work, no API cost. Every
  feature should keep working in mock mode.
- **Safety:** agents may only read and write inside git worktrees of
  `SANDBOX_REPO_PATH`. Never loosen `sandbox.py` checks to make something work.
- Keep `.tscn` files simple. Put logic in `.gd` files and build nodes in code
  where that's clearer.
- Residents walk along the roads in `scenes/island/` (`island_residents.gd` picks
  the route, `island_paths.gd` follows the sand path). The owner asked for it to
  be written; it's still a good file to read when learning Godot.
- No version control yet: the project is local-only for now, so don't run git commands.
