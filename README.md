# Mii-nagement 🏝️

A Tomodachi Life-style manager for teams of coding agents doing real software work.

- Each **project is an island**, and every island **is a git repository** on your
  machine (created for you, in a folder you choose), optionally linked to GitHub.
- Each **agent is a resident** with a name, a job, and a personality. Their
  **brain** is a real coding agent you already have (**Claude Code**, **Codex**,
  or **Gemini CLI**), driven over the open
  [Agent Client Protocol](https://agentclientprotocol.com) on your subscription
  login (or an API key). Or pick the **OpenRouter** brain: any model from
  [openrouter.ai](https://openrouter.ai/models), no CLI to install.
- **The town layout is the workflow.** Buildings are steps (plan, code, test,
  review, ship) and roads are handoffs. A task is a little package that residents
  carry from building to building.
- When a resident needs you, a **bubble** pops up over their head. Click it to
  answer.

> **Status:** the backend MVP is done (Django + Channels, ACP brains, OpenRouter
> brain, GitHub in/out, insights, replay) and runs end to end in **mock mode**
> (fake agents, no API cost). The game is wired to every backend endpoint. Open work is
> marked `TODO(mvp)` / `TODO(stretch)` in the code; the roadmap is in [TODO.md](TODO.md).

## How it fits together

```
┌───────────────────────────┐   WebSocket (JSON events)   ┌──────────────────────┐
│ backend/  (Python)        │ ──────────────────────────▶ │ game/  (Godot 4)     │
│ Django · workflow engine  │     ws://localhost:8000/ws  │ draws the island,    │
│ ACP agents · SQLite       │ ◀────────────────────────── │ sends your clicks    │
│ git worktrees             │      answer_bubble          │ (no agent logic)     │
└───────────────────────────┘                             └──────────────────────┘
```

Every message is defined in [docs/protocol.md](docs/protocol.md). How the
backend works: [docs/architecture.md](docs/architecture.md).

## The lazy way

With [uv](https://docs.astral.sh/uv) and Godot 4 installed:

```bash
make
```

That installs everything, starts the backend and the simulator, and opens the
game. No configuration file is needed. Ctrl+C stops all three. Other targets: `make server`,
`make sim`, `make watch`, `make game`, `make test`, `make reset`. If Godot isn't
found, pass it: `make GODOT=/path/to/godot`.

## Setup (once, by hand)

You need macOS, [Homebrew](https://brew.sh), and
[Godot 4](https://godotengine.org/download) (4.3 or newer; built with 4.7) in
`/Applications`. Run these from the repo root:

```bash
brew install uv
```

```bash
cd backend && uv sync
```

- `uv sync` downloads Python 3.12 for this project only, creates `backend/.venv`,
  and installs the dependencies.
- No `.env` is needed. The backend starts in mock mode; settings and API keys are
  saved at runtime (see "Settings" below). Developers can still copy
  `.env.example` to `.env` to force values.

Optional: make `godot` a terminal command. The commands below use the full path,
so this is only for convenience.

```bash
echo 'alias godot="/Applications/Godot.app/Contents/MacOS/Godot"' >> ~/.zshrc && source ~/.zshrc
```

## Run it

Open a separate terminal for each step, starting in the repo root.

**1. Backend server.** Creates the tables and seeds `backend/mii.db` on first run. Check it at http://localhost:8000/health.
```bash
cd backend && uv run python -m uvicorn mii.asgi:application --reload --port 8000
```

**2. Simulator.** Sends fake tasks through the island, one after another.
```bash
cd backend && uv run python manage.py seed --loop
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
cd backend && uv run python manage.py watch
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
cd backend && uv run python manage.py seed --reset
```
After changing a table in `models.py`: `uv run python manage.py makemigrations mii`
(the server applies migrations when it starts).

## What you should see

The game opens on the **Archipelago** (every island in one sea, plus everything
"Waiting on you"). From there:
- **Settings** (bottom nav): mock mode on/off, test command, islands folder, API
  keys (saved to your OS keychain), and which brains this computer can run.
- **New island**: name, GitHub repo, branch, town plan, residents, house rules.
- Click an island to visit it. **Send a package** (top right) drops a task in the
  Mailbox; residents walk it along the roads. Answer their bubbles in the sidebar.
- **Residents** (create / edit, including OpenRouter models), **News** (stories
  from stored events; ↻ replays a shipped package), **Insights** (stats).
- **Scroll / pinch / − + buttons** zoom the island. **Click and drag** pans it.

New to Godot? Read [docs/godot-tour.md](docs/godot-tour.md).

## Real agents

Switch mock mode off (`PUT /settings {"settings": {"mock_agents": false}}`, or
`MOCK_AGENTS=0` in `.env`). Every island already has its own git repository
(the seed island's is created on first start under `~/Documents/Mii-nagement`,
or `~/Mii-nagement` without a Documents folder; change the root with
`ISLANDS_ROOT`). Each task gets its own git worktree of that repo and a
`mii/<task_id>` branch. Optional: `TEST_COMMAND` (what the Lab runs) and the
island's `repo` (`owner/name`, for issues in and PRs out via the logged-in `gh` CLI).

New islands (the game's island creator, or `POST /islands` with
`{"name": "Rocky Cove"}`) get their buildings from a town plan and a fresh repo in
the default folder; add `"folder": "/some/dir"` to put it elsewhere or
`"existing_repo": "/path/to/repo"` to adopt one you already have.
`GET /islands` lists islands and the default folder.

**ACP brains (your subscriptions, no API key needed):**

- [Node.js](https://nodejs.org): the Claude Code and Codex adapters run through `npx`.
- `claude-code`: install the `claude` CLI, run it once and log in (Claude Pro/Max).
- `codex`: run `npx -y @agentclientprotocol/codex-acp` once or `codex login` (ChatGPT).
- `gemini`: install the Gemini CLI, run it once and log in with Google.
- Or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` in `.env` instead.

**OpenRouter brain (any model, no CLI):** save the key with
`PUT /settings {"secrets": {"openrouter_api_key": "sk-or-..."}}` (it goes into
your OS keychain, never the database) or set `OPENROUTER_API_KEY` in `.env`, then
give a resident `brain = "openrouter"` and a `model` from http://localhost:8000/models.

## Settings

`GET /settings` shows the effective settings (`mock_agents`, `islands_root`,
`test_command`, `openrouter_model`, `keep_worktrees`), which ones `.env` locks,
and which API keys are set. `PUT /settings` saves settings to the database and
API keys (`openrouter_api_key`, `anthropic_api_key`, `openai_api_key`,
`gemini_api_key`) to the OS keychain: macOS Keychain, Windows Credential
Manager, or Linux Secret Service, with an owner-only file as the fallback. Keys
are never sent back. Changes apply from the next task.

The game's Settings popup does all of this for you. Check which brains the
backend can see: http://localhost:8000/brains/detail

While a real resident works you'll see `activity` events and ✋ permission
bubbles (the agent's own options as buttons). No answer within
`PERMISSION_TIMEOUT` means "reject". The Workshop commits the coder's changes,
the Lab runs `TEST_COMMAND`, and the Post Office pushes the branch and opens the PR.

## Repo layout

```
backend/   Django server: workflow engine, ACP + OpenRouter agents, event bus, insights
game/      Godot 4 client: 3D island, UI, residents, bubbles
docs/      protocol.md (WebSocket events), architecture.md (backend design),
           godot-tour.md (Godot for beginners)
TODO.md    roadmap (MVP vs stretch)
CLAUDE.md  context for Claude Code sessions
```

`Makefile` runs the commands above for you.

Find every open TODO:
```bash
grep -rn "TODO(mvp)" backend/mii game/net.gd game/scenes
```
