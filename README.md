# Mii-nagement 🏝️

A Tomodachi Life-style manager for teams of coding agents doing real software work.

- Each **project is an island** linked to a GitHub repo.
- Each **agent is a resident** with a name, a job, and a personality. Their
  **brain** is a real coding agent you already have (**Claude Code**, **Codex**,
  or **Gemini CLI**), driven over the open
  [Agent Client Protocol](https://agentclientprotocol.com). Use your subscription
  login or an API key.
- **The town layout is the workflow.** Buildings are steps (plan, code, test,
  review, ship) and roads are handoffs. A task is a little package that residents
  carry from building to building.
- When a resident needs you, a **bubble** pops up over their head. Click it to
  answer.

> **Status:** hackathon skeleton. Runs end to end in **mock mode** (fake agents, no
> API cost). Real features are marked with `TODO(mvp)` / `TODO(stretch)` in the
> code; the roadmap is in [TODO.md](TODO.md).

## How it fits together

```
┌───────────────────────────┐   WebSocket (JSON events)   ┌──────────────────────┐
│ backend/  (Python)        │ ──────────────────────────▶ │ game/  (Godot 4)     │
│ FastAPI · workflow engine │     ws://localhost:8000/ws  │ draws the island,    │
│ ACP agents · SQLite       │ ◀────────────────────────── │ sends your clicks    │
│ git worktrees             │      answer_bubble          │ (no agent logic)     │
└───────────────────────────┘                             └──────────────────────┘
```

Every message is defined in [docs/protocol.md](docs/protocol.md). How the
backend works: [docs/architecture.md](docs/architecture.md).

## Setup (once)

You need macOS, [Homebrew](https://brew.sh), and
[Godot 4](https://godotengine.org/download) (4.3 or newer; built with 4.7) in
`/Applications`. Run these from the repo root:

```bash
brew install uv
```

```bash
cp .env.example .env
```

```bash
cd backend && uv sync
```

- `.env` defaults to `MOCK_AGENTS=1`, which needs no API keys.
- `uv sync` downloads Python 3.12 for this project only, creates `backend/.venv`,
  and installs the dependencies.

Optional: make `godot` a terminal command. The commands below use the full path,
so this is only for convenience.

```bash
echo 'alias godot="/Applications/Godot.app/Contents/MacOS/Godot"' >> ~/.zshrc && source ~/.zshrc
```

## Run it

Open a separate terminal for each step, starting in the repo root.

**1. Backend server.** Creates and seeds `backend/mii.db` on first run. Check it at http://localhost:8000/health.
```bash
cd backend && uv run uvicorn mii.server:app --reload --port 8000
```

**2. Simulator.** Sends fake tasks through the island, one after another.
```bash
cd backend && uv run python -m mii.seed --loop
```

**3. The game.** Starts it directly, without the editor:
```bash
/Applications/Godot.app/Contents/MacOS/Godot --path game
```
Or open the editor and press **F5** (▶ Play, top right):
```bash
/Applications/Godot.app/Contents/MacOS/Godot -e --path game
```
The first time, you can also open Godot normally, click **Import**, and pick
`game/project.godot`.

**Optional: watch the raw event stream** in a terminal:
```bash
cd backend && uv run python -m mii.watch
```

**Tests and checks:**
```bash
cd backend && uv run pytest
```
```bash
/Applications/Godot.app/Contents/MacOS/Godot --headless --path game --quit
```
The second one loads the game without a window and prints any script errors.

**Start over with a fresh database.** Stop the server first.
```bash
cd backend && uv run python -m mii.seed --reset
```

## What you should see

The main screen is a **UI mockup**: a top bar, the 3D island in a rounded frame,
the bottom nav (Build / Residents / News / Insights), and the Bubbles sidebar.
Most of it is static placeholder data for now. Live so far:
- **Scroll / pinch / − + buttons** zoom the island. **Click and drag** pans it.
- Building name pills follow their buildings.
- The connection pill turns green when the backend is running.

Residents, live bubbles in the sidebar, and activity text aren't connected yet;
see TODO.md.

New to Godot? Read [docs/godot-tour.md](docs/godot-tour.md).

## Real agents (in progress)

The ACP runner is still boilerplate (`backend/mii/agents/acp.py`). When it's
built, real mode will need:

- [Node.js](https://nodejs.org) (the Claude Code and Codex adapters run through `npx`).
- The agent CLIs you want, logged in: `claude`, `codex`, `gemini`. Or set their
  API keys in `.env` instead.
- The [GitHub CLI](https://cli.github.com), logged in (`gh auth login`), for
  issues in and PRs out.
- In `.env`: `MOCK_AGENTS=0` and `SANDBOX_REPO_PATH` (a local git repo the
  agents may work on). Each task gets its own git worktree of that repo.

Check which agents the backend can see: http://localhost:8000/brains

## Repo layout

```
backend/   Python server: workflow engine, ACP agents, event bus, insights
game/      Godot 4 client: 3D island, UI, residents, bubbles
docs/      protocol.md (WebSocket events), architecture.md (backend design),
           godot-tour.md (Godot for beginners)
TODO.md    roadmap (MVP vs stretch)
CLAUDE.md  context for Claude Code sessions
```

Find every open TODO:
```bash
grep -rn "TODO(mvp)" backend/mii game/net.gd game/scenes
```
