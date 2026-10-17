# CLAUDE.md

Working rules for Claude Code sessions on this repo. Humans: this is a good short read too.

## What this is

**Mii-nagement**: a Tomodachi Life-style manager for teams of coding agents doing real
software work (hackathon project). An *island* is a project: a local git repository,
optionally linked to GitHub. *Residents* have jobs, personality sliders and a *brain*
(Claude Code, Codex or Gemini CLI over the Agent Client Protocol, an OpenRouter model, or
`mock`). *Buildings* are workflow steps, *roads* are handoffs (`always` / `pass` /
`fail`), and a *task package* travels the roads.

- `backend/`: Python 3.12, uv, Django + Channels, SQLite. Owns **all** logic.
- `game/`: Godot 4 (GDScript), a 3D island in a 2D UI. **Display and input only.**

## Read first

1. [docs/architecture.md](docs/architecture.md): the map. Its **"Where to look"** table
   lists, for each kind of change, the files to open in order and the check to run.
   Start every task there instead of searching the tree.
2. [docs/protocol.md](docs/protocol.md): every WebSocket message. Change it **first**,
   then both halves. HTTP endpoints: [docs/http-api.md](docs/http-api.md).
3. The skills in `.claude/skills/`: `godot-animation` for anything about motion, moods,
   speech or island life; `demo-video` for recordings.

## Commands

Run from the repo root (`make help` lists them all):

```bash
make check        # after every change: ruff + backend tests + gdlint/gdformat + Godot script check
make fmt          # format both halves
make              # backend + game, for real agents (tools/run.sh; Ctrl+C or closing the game stops both)
make dev          # backend (--reload) + simulator (fake tasks) + game: mock-mode development
make server       # just the backend, http://localhost:8000
cd backend && uv run pytest tests/test_engine.py -q        # one test file
cd backend && uv run python manage.py makemigrations mii   # after changing models.py
```

`make` finds Godot on PATH or in `/Applications`. Anywhere else, pass it:
`make check GODOT=/path/to/Godot`. Before trusting a Godot check, make sure the
binary exists: a missing Godot is skipped, and that looks like a pass.

- **Windows machine:** Godot 4.7.2 is in `C:\Users\lucas\Downloads\Godot_v4.7.2-stable_win64.exe\`
  (use the `_console.exe` for headless runs); `~/.local/bin` has `uv`, `gh`, `make` and a
  `godot` wrapper. A TLS-intercepting proxy needs `UV_SYSTEM_CERTS=1` and
  `git config --global http.sslBackend schannel`.

## Rules

- **Simple, readable code over clever code.** The owner knows Python and is **new to
  Godot**: the first time a file uses a Godot concept (signals, autoloads, `_process`,
  SubViewport, Tween...), explain it in a short comment. Comments say what and why in a
  line or two; no history ("we used to..."), no long banners.
- **No helper layers for their own sake.** Reuse what exists: `vocab.py` and
  `scenes/protocol.gd` for shared names, `Net.request_json` for HTTP, `Net.find_*` for
  lookups, `UI.label` for labels, the theme (`game/ui/theme.tres`) for colours.
- **The game never decides.** Workflow, agents, permissions and moods live in the backend.
- **Safety:** agents only read and write inside git worktrees of the island's own repo;
  shell commands are gated by a ✋ bubble. Never loosen `sandbox.py` to make something work.
- **Async engine:** use the async ORM (`aget`, `asave`, `async for`); git, `gh` and the
  keychain go through `asyncio.to_thread` (or `sync_to_async` for ORM code).
- **Brains:** each ACP brain is one entry in `BRAINS` (`agents/acp.py`); brain-specific
  details stay in the runners, never in the engine, protocol or game.
- **Mock mode is the default** (`MOCK_AGENTS=1`) and every feature must work in it.
- **No `.env` required.** Settings go through `PUT /settings` (`prefs.py`); secrets go to
  the OS keychain, never the database or the snapshot. No machine-specific paths in code.
- **Migrations are Django's:** change `models.py`, run `makemigrations mii`, check it in.
- **Island map rules** exist twice: `island_map.py` (backend) and `island_layout.gd`
  (game). Change them together.
- **Godot files:** keep `.tscn` files simple (logic in `.gd`), keep every `*.gd.uid` next
  to its script, and `git mv` a script together with its `.uid`.
- **Markers:** `TODO(mvp): ...` / `TODO(stretch): ...`; the roadmap is [TODO.md](TODO.md).
- **Git:** `origin` on GitHub, branch `main`. Start new work on a branch made from a
  freshly pulled `main`. Commit or push only when the owner asks. Other sessions may edit
  this checkout at the same time: re-read a file right before changing it.

## Scope

On purpose out of scope: LangChain or more direct-API runners (OpenRouter is the one), an
ML router, several packages at once on one island (islands run in parallel, one package
each), multi-account islands. Island News is written by the game from stored events.
