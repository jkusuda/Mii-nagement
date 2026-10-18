# Mii-nagement 🏝️

A Tomodachi Life-style manager for teams of coding agents doing real software work.

- Each **project is an island**, and every island **is a git repository** on your
  machine (created for you, cloned from GitHub, or a folder you already have).
- Each **agent is a resident** with a name, a job and a personality. Their **brain** is
  a real coding agent you already have (**Claude Code**, **Codex** or **Gemini CLI**),
  driven over the open [Agent Client Protocol](https://agentclientprotocol.com) on your
  subscription login (or an API key). Or pick the **OpenRouter** brain: any model from
  [openrouter.ai](https://openrouter.ai/models), no CLI to install.
- **The town layout is the workflow.** Buildings are steps (plan, code, test, review,
  ship) and roads are handoffs. A task is a little package that residents carry from
  building to building, in its own git worktree, ending in a pull request.
- When a resident needs you (a question, a ✋ permission for a shell command, an
  "approve & merge"), a **bubble** pops up over their head. Click it to answer.

```
┌───────────────────────────┐   WebSocket (JSON events)   ┌──────────────────────┐
│ backend/  (Python)        │ ──────────────────────────▶ │ game/  (Godot 4)     │
│ Django · workflow engine  │     ws://localhost:8000/ws  │ draws the island,    │
│ ACP agents · SQLite       │ ◀────────────────────────── │ sends your clicks    │
│ git worktrees             │   commands + HTTP requests  │ (no agent logic)     │
└───────────────────────────┘                             └──────────────────────┘
```

The backend decides everything (workflow, agents, permissions, moods); the game only
shows events and sends your input.

## Quick start

**On a Mac, just the app:** download it from the website, or build it with `make app`
(`game/builds/macos/Mii-nagement.dmg`). It starts its own backend on port 8765; the
first launch downloads uv and Python, so it needs the internet and takes a minute. It
isn't notarized: the first time macOS refuses it, open System Settings > Privacy &
Security > Open Anyway.

**On Windows, just the app:** download the installer from the website, or build it with
`make windows` from Git Bash (`game/builds/windows/Mii-nagement-Setup.exe`). Everything
comes with it (the backend, a Python with its packages, Git and Node), so nothing else
needs installing and the first launch is quick. It installs for the current user (no admin
rights) and uninstalls from Settings > Apps, leaving your islands. It isn't code-signed: the
first time, SmartScreen warns, so choose More info > Run anyway. The backend's log is
`%APPDATA%\Godot\app_userdata\Mii-nagement\backend.log`.

**From source** (macOS, Linux or Windows), with [uv](https://docs.astral.sh/uv) and
[Godot 4.7](https://godotengine.org/download) installed:

```bash
make
```

That installs the backend's dependencies, starts the backend, and opens the game. No
configuration file is needed. Ctrl+C (or closing the game) stops both. `make` finds
Godot on your PATH or in `/Applications`; anywhere else, pass it:

```bash
make GODOT=/path/to/godot
```

Installing uv: `brew install uv` (macOS), `curl -LsSf https://astral.sh/uv/install.sh | sh`
(Linux) or `winget install astral-sh.uv` (Windows).

## Give your residents a brain

Each resident runs one of these. Install the ones you want; the Settings screen (and
http://localhost:8000/brains/detail) shows which ones the backend can see.

| Brain | Needs | Pays with |
|---|---|---|
| `claude-code` | [Node.js](https://nodejs.org) and the `claude` CLI, run once to log in | Claude Pro/Max, or `ANTHROPIC_API_KEY` |
| `codex` | Node.js; `codex login` (or run `npx -y @agentclientprotocol/codex-acp` once) | ChatGPT, or `OPENAI_API_KEY` |
| `gemini` | the Gemini CLI, run once to log in with Google | Google, or `GEMINI_API_KEY` |
| `openrouter` | nothing to install | an OpenRouter key; the resident's model is any OpenRouter model id |

API keys go in the game's **Settings** popup, which saves them to your OS keychain
(macOS Keychain, Windows Credential Manager, Linux Secret Service; an owner-only file
where there is none), never to the database.

For GitHub (clone a repo, start a task from an issue, open and merge PRs) install the
[GitHub CLI](https://cli.github.com) and run `gh auth login`.

## Using it

The game opens on the **Archipelago**: every island in one sea, plus everything
"Waiting on you".

- **New island**: name, code (see below), branch, town plan, residents, house rules.
- Click an island to visit it. **Send a package** (top right) drops a task in the
  Mailbox, either a title and details or a GitHub issue. Residents walk it along the
  roads; answer their bubbles in the sidebar. Click a building to see the packages and
  residents there, or talk to the resident at work.
- **Residents**: create and edit them (job, personality sliders, brain and model, gifts
  (MCP servers), house keys that decide what they may do without asking).
- **Build**: drag buildings around (the paths follow), add or remove land, plant trees,
  pick which workflow steps the island has, size the pond, choose the season.
- **News** (stories from what happened, merge a shipped package), **Insights** (stats).
- **Settings**: islands folder, packages at once, the master key, API keys.
- Scroll or pinch to zoom the island, click and drag to pan.

### Where the code comes from

The island creator's **Code** row has three choices:

- **New empty repository**: a fresh repo with a README, under `~/Documents/Mii-nagement`
  (or `~/Mii-nagement` without a Documents folder; change it in Settings).
- **Clone from GitHub**: pick one of your repos and the branch residents start from.
  Residents work on the real code, and the Post Office pushes `mii/<task_id>` and opens a
  PR against that branch. The target folder must be empty.
- **Use a folder on this computer**: a git folder you already have (with at least one
  commit). Residents branch off whatever is checked out there, in their own worktrees,
  so your checkout isn't touched. With a GitHub `origin` the island opens PRs too.

### What a package does

The **Mailbox** plans, the **Workshop** codes and commits, the **Lab** runs the
project's tests (the resident there works out how), the **Courthouse** reviews (and, on an island linked to GitHub, asks you to
approve & merge), and the **Post Office** opens the PR (and merges it when approved). A failure goes back down the `fail`
road. A task gives up after 3 visits to one building or 20 steps.

Agents only read and write inside a git worktree of the island's own repository, and
shell commands ask first unless the resident's house keys allow them.

## The HTTP API

Everything the game does is also an HTTP or WebSocket call, documented in
[docs/http-api.md](docs/http-api.md) and [docs/protocol.md](docs/protocol.md). For example:

```bash
curl -X POST localhost:8000/islands -H 'content-type: application/json' \
  -d '{"name": "Rocky Cove"}'                                              # new repo
curl -X POST localhost:8000/islands -H 'content-type: application/json' \
  -d '{"name": "Fern Harbor", "repo": "me/fern-api", "branch": "develop"}' # clone
curl -X POST localhost:8000/islands/rocky_cove/tasks -H 'content-type: application/json' \
  -d '{"title": "Add hello.txt", "description": "Create hello.txt with one line: hello"}'
curl 'localhost:8000/islands/rocky_cove/tasks?status=done'                 # finished tasks
```

`GET /settings` shows the effective settings and which API keys are set (never their
values); `PUT /settings` changes them. Developers can also copy `.env.example` to `.env`
to force values; anything there wins over saved settings.

## Development

**New here (person or AI agent)?** Read [docs/architecture.md](docs/architecture.md)
first: how the two halves fit, a map of every file, and a "where to look" table for
common changes. Then [CLAUDE.md](CLAUDE.md) for the working rules, and
[docs/godot-tour.md](docs/godot-tour.md) if Godot is new to you.

```bash
make check     # ruff + backend tests + gdlint/gdformat + the Godot script check (CI runs the same)
make fmt       # format both halves
make server    # just the backend, with --reload: http://localhost:8000/health
make game      # just the game
make watch     # print the live event stream
make help      # everything else
```

- One backend test file: `cd backend && uv run pytest tests/test_engine.py -q`.
- After changing `backend/mii/models.py`: `cd backend && uv run python manage.py makemigrations mii`
  (the server applies migrations when it starts).
- Open the game in the editor with `godot -e --path game` and press F5.
- Start over with an empty database: stop the server and delete `backend/mii.db` (the
  islands' repositories stay on disk).
- Optional: `uvx pre-commit install` runs the formatters on every commit.

**Two players, one island:** start the backend with `--host 0.0.0.0` and run the game on
another machine on the LAN with `MII_SERVER=<host-ip>:8000`. Both see the same island
and answer the same bubbles; the agents run on the host.

## Deployment

`deploy/` hosts the landing page and the Mac and Windows downloads on one server (Caddy in Docker
Compose, with HTTPS), redeployed by GitHub Actions after CI passes on `main`. The game
runs its own backend on the player's computer, so the server has none. See
[deploy/README.md](deploy/README.md).

## Repo layout

```
backend/   Django + Channels server: workflow engine, ACP and OpenRouter agents, events, insights
game/      Godot 4 client: the 3D island, UI, residents, bubbles
docs/      architecture.md (start here), protocol.md (WebSocket), http-api.md (HTTP),
           godot-tour.md (Godot for beginners), mii-customization.md (faces and hair)
deploy/    the website: Compose, Caddy, landing page, push and Vultr scripts
tools/     run.sh (`make`), package_mac.sh (`make app`), package_windows.sh (`make windows`),
           build_mii_parts.py (Mii parts)
video/     the demo video (Remotion) and its Godot captures
.github/   CI (the same checks as `make check`) and the deploy workflow
CLAUDE.md  working rules for Claude Code sessions (a short read for humans too)
Makefile   runs everything above: `make help`
```
