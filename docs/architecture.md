# Backend architecture

Mii-nagement is a Tomodachi Life-style manager for coding agents. The backend
runs the workflow; the Godot game only shows events and sends your input
([protocol.md](protocol.md)).

## The idea in one paragraph

An island is a repo: creating an island creates a local git repository for it
(`islands.py`), by default under `~/Documents/Mii-nagement` (or `~/Mii-nagement`
without a Documents folder), or in a folder you pick, or it adopts a repo you
already have. Buildings are workflow steps, roads are handoffs with
`always` / `pass` / `fail` conditions, and a task is a package that travels the
roads. Each building has a resident, and each resident has a **brain**: a real
coding agent (Claude Code, Codex, or Gemini CLI) that we drive over the
**Agent Client Protocol (ACP)** on the subscription you already pay for, or any
OpenRouter model through the direct `openrouter` brain. When an agent needs
you, a bubble pops up over the resident's head.

## Brains

| `Resident.brain` | Runs | How we start it | Pays with |
|---|---|---|---|
| `mock` | MockRunner | n/a | free (the default) |
| `claude-code` | Claude Code over ACP | `npx -y @agentclientprotocol/claude-agent-acp` | Claude Pro/Max login, or `ANTHROPIC_API_KEY` |
| `codex` | Codex over ACP | `npx -y @agentclientprotocol/codex-acp` (bundles Codex) | ChatGPT login (`codex login`), or `OPENAI_API_KEY` |
| `gemini` | Gemini CLI over ACP | `gemini --experimental-acp` | Google login, or `GEMINI_API_KEY` |
| `openrouter` | OpenRouterRunner (built-in tool loop) | HTTPS, no process | `OPENROUTER_API_KEY`; `Resident.model` = any OpenRouter model id |

The three ACP brains share one code path (`agents/acp.py`); only the `BRAINS`
table entry differs (command, login hint, session modes). The `openrouter`
brain is the "bring any model without installing a CLI" path: a ~150-line agent
loop with five tools (list/read/write files inside the worktree, run a command
after a ✋ bubble, ask the user). `MOCK_AGENTS=1` forces every resident onto
the mock brain.

`Resident.model`: for ACP brains, a model the agent itself offers (set through
ACP's `model` config option when available, otherwise the agent's default).
Claude Code offers `sonnet`, `opus`, `haiku`, `fable` and dated ids such as
`claude-sonnet-5`. For `openrouter`, an OpenRouter model id such as
`anthropic/claude-sonnet-4.5`.

## Files

Django project and app are the same package, so one `mii/` holds everything.

```
backend/manage.py      Django's CLI: migrate, makemigrations mii, seed, watch
backend/mii/
  settings.py      Django settings (reads config.py)
  config.py        settings from .env (mock switch, repo path, keys, timeouts)
  asgi.py          ASGI entry: HTTP (views) + WebSocket (consumer) + lifespan (starts the engine)
  urls.py          HTTP routes
  views.py         async JSON views
  consumers.py     the /ws WebSocket consumer (Channels, no channel layer)
  runtime.py       one sandbox / event bus / bubbles / engine per process
  models.py        tables + snapshot builder
  migrations/      Django migrations (checked in)
  islands.py       found an island (POST /islands): town plan + a git repo per island, default_folder(), ensure_repo()
  personality.py   job + sliders -> the prompt preamble
  events.py        event bus (save + broadcast) + bubbles
  engine.py        island graph + building handlers + task loop
  residents.py     create / edit residents (from the game's resident creator); a new
                   resident takes over their island's empty buildings for their job
  sandbox.py       Sandboxes (one Sandbox per island repo): worktree per task, commit_all(), diff_stat(), is_inside()
  agents/
    __init__.py    AgentJob, StepResult, BrainRunner, make_runner(), parse_verdict(), build_prompt()
    mock.py        MockRunner
    acp.py         AcpRunner + BRAINS table + available_brains()
    openrouter.py  OpenRouterRunner + list_models()
  github.py        issue in / PR out, via the `gh` CLI
  insights.py      stats from the Step table
  replay.py        replay a recorded task into the game
  seed.py          seed island + mock simulator (used by `manage.py seed`)
  watch.py         prints the event stream (used by `manage.py watch`)
  management/commands/   seed, watch
```

## Tables

| Table | What it holds |
|---|---|
| `Island` | id, name, GitHub repo, `repo_path` (its local git repository), branch, town plan, house rules |
| `Resident` | name, job, 4 personality sliders, `brain`, optional `model`, catchphrase, color, house `keys`, `gifts`, live state / building / mood |
| `Building` | type, name, lot position, resident |
| `Road` | from, to, condition |
| `Task` | title, status, where the package is, who carries it, `issue_number`, `branch`, `pr_url` |
| `Step` | one building visit: task, building, resident, brain, outcome, timed out?, bubbles raised, start/finish time |
| `Event` | every event sent to the game (history + replay) |

After changing a model: `uv run python manage.py makemigrations mii`, then
`migrate` (the server also migrates at startup).

## One step

1. The engine hands the package over (`handoff`), walks the resident to the
   building, and sets them `working`.
2. It builds an `AgentJob`: the resident, the building's instructions, the
   package history, the task's worktree, `read_only` for plan/review/PR steps,
   plus `ask()` (show a bubble, wait for the answer) and `report()` (send an
   `activity` event).
3. The building handler runs. Most hand the job to the resident's brain:
   - **AcpRunner**: start the agent in the worktree, `initialize`, `session/new`
     (a missing login becomes a 💤 bubble), switch on the agent's edit or
     read-only mode, pick the resident's model if offered, send personality +
     instructions + history as one prompt. While it runs: tool calls become
     `activity` events; permission requests are decided by the resident's house
     keys (`yes` = allow, `no` = reject, `ask` = a ✋ bubble whose buttons are
     the agent's own options; no answer = reject); file reads/writes are served only inside the worktree. The final
     text's `VERDICT: PASS/FAIL` line decides. The process is always closed.
   - **OpenRouterRunner**: the same contract over OpenRouter's chat API with
     built-in tools.
   Some handlers do more: the **Workshop** commits the worktree after the
   coder, the **Lab** runs `TEST_COMMAND` itself and uses the exit code, the
   **Courthouse** prepends `git diff --stat`, the **Post Office** turns the
   reply into a PR title/body and opens the PR when the island has a repo.
4. The outcome picks the next road. A `Step` row is saved for insights. When
   the task ends the worktree is removed (unless `KEEP_WORKTREES=1`).

## Safety

- Agents only run inside a git worktree of the island's own repository (`sandbox.py`).
- Each agent's own safety mode is switched on (Claude Code `acceptEdits` /
  `plan`, Codex `workspace-write` / `read-only`).
- Permission requests become bubbles; file requests are path-checked. Shell
  commands are gated by a bubble, not path-restricted.
- Every step has a timeout, and a package can visit at most 20 buildings.
- The OpenRouter key is never passed to agent processes.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws?island=<id>` | snapshot + live events; the game sends `answer_bubble`, `create_resident`, `update_resident` |
| `GET /health` | is the server up, which mode |
| `GET /brains` | `{brain: true/false}`: which brains can run here (the creator greys out the rest) |
| `GET /brains/detail` | per brain: `ok`, `installed`, `logged_in` (best effort), `api_key`, `hint` |
| `GET /models` | the OpenRouter catalogue (needs `OPENROUTER_API_KEY`) |
| `GET /islands` | every island (the Archipelago screen), plus `default_folder` for new repositories |
| `POST /islands` | found an island: `{"name", "repo"?, "branch"?, "plan": "classic" \| "quick_fixes", "rules"?, "folder"?, "existing_repo"?}` → its snapshot. Creates the git repo (or adopts `existing_repo`) and lays out the town plan |
| `GET /github/repos?q=` | the user's repos, through their logged-in `gh` (island creator) |
| `GET /github/branches?repo=` | a repo's branches, through `gh` |
| `GET /islands/{id}` | the island snapshot |
| `POST /islands/{id}/tasks` | `{"title": ...}` or `{"issue": 42}` |
| `GET /islands/{id}/insights` | stats for the Insights screen |
| `GET /islands/{id}/events` | stored events for the News tab (planned, see below) |
| `POST /replay/{task_id}?speed=4` | replay a recorded task (demo safety net) |

## Insights payload

`GET /islands/{id}/insights?range=today|week|month` feeds the game's Insights
screen (`game/scenes/insights/`). The game shows whatever is there and puts a
dash on anything missing, so fields can be added one at a time. **Today the
`island` section has `tasks_done`, `tasks_failed`, `prs_opened` and
`avg_issue_to_pr_seconds`, and `residents` / `brains` / `buildings` are filled
from the Step table**; the rest is the shape the screen expects.

```json
{
  "range": "week",
  "island": {
    "tasks_done": 12, "tasks_failed": 1, "prs_opened": 9,
    "tasks_done_before": 8, "prs_opened_before": 6,
    "answer_seconds": 360, "answer_seconds_before": 420,
    "tests_pass_rate": 0.96, "flaky_tests": 2,
    "bubbles_answered": 11
  },
  "per_day": [{"day": "2026-10-12", "done": 3}, {"day": "2026-10-13", "done": 5}],
  "tip": "The Mailbox is looking busy. Another Planner could sort some letters.",
  "residents": [], "brains": [], "buildings": []
}
```

- `*_before`: the same number for the previous range ("↑ 4 on last week").
- `answer_seconds`: average time from a bubble appearing to your answer.
- `tests_pass_rate` (0-1) and `flaky_tests`: from the lab's test runs.
- `per_day`: tasks finished per day in the range, oldest first, today last.
- `tip`: one friendly suggestion. Without it, the game words a simple one itself.
- "Where packages are waiting" doesn't need anything here: the game counts the
  open tasks in the live snapshot by `building`.

## Events history (for Island News)

`GET /islands/{id}/events?limit=200` lets the game's News tab
(`game/scenes/news/`) show what happened before it connected. **Not built
yet**: until it exists, the News tab only shows events from the current game
session. It's one query on the `Event` table:

```json
{"events": [
  {"event_id": 41, "type": "task", "id": "t_12", "title": "Fix login typo", "status": "done", "agent": "jun", "pr_url": "https://github.com/...", "ts": "2026-10-16T09:40:12+00:00"},
  {"event_id": 42, "type": "bubble", "agent": "carlos", "kind": "confused", "id": "b_3f2a", "text": "Which test file?", "ts": "2026-10-16T09:41:03+00:00"}
]}
```

- Each entry is the stored event payload exactly as it was broadcast (protocol
  shapes, with `ts`), plus the row's `id` as `event_id`. Oldest first, the
  newest `limit` events.
- The game decides what's a story and how to word it; the backend doesn't
  need to know about News.

## Status

Backend MVP complete: engine, roads, bubbles, event bus, personality, mock
brain, worktrees, Step recording, step timeouts, ACP runner, OpenRouter brain,
GitHub issue in / PR out, Lab test runs, insights, replay. Real-mode runs
against the live CLIs still need a hands-on check (see README "Real agents").

## Not in scope (on purpose)

LangChain or other direct-API runners beyond OpenRouter, friendship scores, the
ML team router, running several tasks at once. (Several islands work: one task
queue for all of them. Island News is written by the game from stored events;
the backend only serves them, see "Events history".)
