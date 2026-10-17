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
  config.py        developer overrides from the optional .env (+ defaults)
  prefs.py         user settings (Setting table) + API keys (OS keychain); env > saved > default
  asgi.py          ASGI entry: HTTP (views) + WebSocket (consumer) + lifespan (starts the engine)
  urls.py          HTTP routes
  views.py         async JSON views
  consumers.py     the /ws WebSocket consumer (Channels, no channel layer)
  runtime.py       one sandbox / event bus / bubbles / engine per process
  models.py        tables + snapshot builder
  migrations/      Django migrations (checked in)
  islands.py       found an island (POST /islands): shape + steps + a git repo per island; island layouts
                   (SHAPES, build_roads, validate_layout, set_layout for build mode), default_folder(), ensure_repo()
  personality.py   job + sliders -> the prompt preamble
  events.py        event bus (save + broadcast) + bubbles
  engine.py        island graph + building handlers + task loop
  residents.py     create / edit residents (from the game's resident creator); a new
                   resident takes over their island's empty buildings for their job
  layout.py        build mode: validate + save a whole layout (set_layout / PUT layout), assign buildings
  sandbox.py       Sandboxes (one Sandbox per island repo): worktree per task, commit_all(), diff_stat(), is_inside()
  agents/
    __init__.py    AgentJob, StepResult, BrainRunner, make_runner(), parse_verdict(), build_prompt()
    mock.py        MockRunner
    acp.py         AcpRunner + BRAINS table + GIFTS table (MCP servers) + available_brains() / available_gifts()
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
| `Island` | id, name, GitHub repo, `repo_path` (its local git repository), branch, town plan, house rules, `map` (land tiles, pond, plants, season) |
| `Resident` | name, job, 4 personality sliders, `brain`, optional `model`, catchphrase, color, house `keys`, `gifts`, live state / building / mood / `notebook` |
| `Building` | type, name, lot (`x`, `y`, `facing`), resident |
| `Road` | from, to, condition |
| `Task` | title, status, where the package is, who carries it, `issue_number`, `branch`, `pr_url`, `merged_at`, `created_at` / `finished_at` |
| `Step` | one building visit: task, building, resident, brain, outcome, timed out?, bubbles raised, start/finish time |
| `Event` | every event sent to the game (history + replay) |
| `Setting` | user settings saved from the game (never secrets: those live in the OS keychain) |

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
     the agent's own options; no answer = reject); file reads/writes are served only inside the worktree.
     ACP has no "ask the user" request, so the prompt tells the agent it may
     end a turn with `QUESTION: ...`: the runner shows a ❓ bubble and prompts
     the same session again with your answer (up to 3 per step). The final
     turn's `VERDICT: PASS/FAIL` line decides. The process is always closed.
   - **OpenRouterRunner**: the same contract over OpenRouter's chat API with
     built-in tools.
   Some handlers do more: the **Workshop** commits the worktree after the
   coder, the **Lab** runs `TEST_COMMAND` itself and uses the exit code, the
   **Courthouse** prepends `git diff --stat` and, once the reviewer passes it on
   an island with a GitHub repo, asks you with a ✅ `ready` bubble ("Approve &
   merge" / "Send back"; skipped when the `merge_pr` house rule is off, which
   approves it). Send back fails the step with your note. The **Post Office**
   turns the reply into a PR title/body, opens the PR, and merges it with
   `gh pr merge` if you approved (squash, else merge, else rebase). Unanswered:
   the PR stays open.
4. The outcome picks the next road. A `Step` row is saved for insights. When
   the task ends the worktree is removed (unless `KEEP_WORKTREES=1`).

The task's worktree branches off the island's `branch` (a local branch, else
`origin/<branch>`, else HEAD), and the PR is opened against it. The resident's
gifts go to `session/new` as MCP servers (`GIFTS` in `agents/acp.py`; a gift
that can't run here is skipped with an `activity` note), the agent's usage
updates become `notebook` events, and permission requests are decided by the
resident's house keys tightened by the island's house rules
(`agents.permission_for`: `shell` makes every command ask, `install_packages`
makes commands that look like an install ask). The OpenRouter brain applies
the same keys and rules to its `run_command` tool.

## Safety

- Agents only run inside a git worktree of the island's own repository (`sandbox.py`).
- Each agent's own safety mode is switched on (Claude Code `acceptEdits`,
  Codex `workspace-write` / `read-only`). On read-only steps (plan, review, PR
  text) edit permissions are refused and file writes are rejected by our side
  of the ACP connection. Claude Code's `plan` mode isn't used: refusing its
  "Approve plan" ends the turn as cancelled and loses the plan.
- Permission requests become bubbles; file requests are path-checked. Shell
  commands are gated by a bubble, not path-restricted.
- Every step has a timeout, and a package can visit at most 20 buildings.
- The OpenRouter key is never passed to agent processes. API keys live in the OS
  keychain (or an owner-only file where there is none), never in the database.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws?island=<id>` | snapshot + live events; the game sends `answer_bubble`, `create_resident`, `update_resident`, `delete_resident`, `create_task`, `cancel_task`, `set_layout` |
| `GET /health` | is the server up, which mode |
| `GET /brains` | `{brain: true/false}`: which brains can run here (the creator greys out the rest) |
| `GET /brains/detail` | per brain: `ok`, `installed`, `logged_in` (best effort), `api_key`, `hint` |
| `GET /gifts` | per gift (MCP server): `ok`, `label`, `hint` (the creator greys out the rest) |
| `GET /models` | the OpenRouter catalogue (needs `OPENROUTER_API_KEY`) |
| `GET /islands` | every island (id, name, repo, created_at, ...: the Archipelago screen), plus `default_folder` for new repositories |
| `POST /islands` | found an island: `{"name", "repo"?, "branch"?, "shape"?, "steps"? \| "plan"?: "classic" \| "quick_fixes", "season"?, "rules"?, "folder"?, "existing_repo"?}` → its snapshot (400 `{"error"}` with a readable reason). The repository comes one of three ways: no `repo` → a new repo with a README in `<folder>/<id>`; `repo` → `gh repo clone owner/name <folder>/<id>` (needs `gh` installed and logged in; the folder must be empty or missing; checks out `branch`, default the repo's default branch; an empty GitHub repo gets a first README commit); `existing_repo` → that git folder as is (needs a commit; `repo` is read from its GitHub `origin` when not given, `branch` defaults to its checked-out branch). Then it lays out the shape's lots and the roads for the steps. Cloning runs in a worker thread and can take a while |
| `GET /github/repos?q=` | the user's repos, through their logged-in `gh` (island creator) |
| `GET /github/branches?repo=` | a repo's branches, through `gh` |
| `GET /islands/{id}` | the island snapshot |
| `DELETE /islands/{id}` | abandon the island (rows go, the git repository stays on disk). 409 while a task runs there or if it is the last island |
| `DELETE /islands/{id}/residents/{resident_id}` | the resident moves away (409 if a building still counts on them) |
| `GET /islands/{id}/tasks` | the island's tasks, newest first, each with its `steps` (`?status=done,failed&limit=50`) |
| `POST /islands/{id}/tasks` | `{"title": ..., "description": ...}` or `{"issue": 42}` → the queued task. The engine runs it through the island's residents' brains |
| `GET /islands/{id}/tasks/{task_id}` | one task: `steps` (building, resident, brain, outcome, `summary` = what the resident reported) + `events` (the stored events while it ran) |
| `POST /islands/{id}/tasks/{task_id}/cancel` | cancel a queued task or stop the running one (409 if it already finished) |
| `GET /islands/{id}/insights?range=week` | stats for the Insights screen and News' "This week" (`today` / `week` / `month`) |
| `GET /islands/{id}/events?limit=200` | stored events, oldest first, for the News tab (see "Events history") |
| `PUT /islands/{id}/layout` | build mode over HTTP: `{"buildings", "roads"}` → the saved layout (same rules as `set_layout` in protocol.md); or the game's form `{"map", "buildings": [{type, x, y, facing}]}` → the new snapshot (also broadcast), 400 / 409 (steps changing during a task), see protocol.md "Island map" |
| `GET /settings` | effective settings, what `.env` locks, which API keys are set |
| `PUT /settings` | `{"settings": {...}, "secrets": {"openrouter_api_key": "sk-..." \| null}}`; keys go to the keychain and are never echoed |
| `POST /replay/{task_id}?speed=4` | replay a recorded task (demo safety net) |

## Tasks over HTTP

`POST /islands/{id}/tasks` is how work reaches the island without the game: a
curl, a script, or a GitHub issue number. The reply is the queued task; the
engine worker picks it up and each building's resident does their step through
their brain (an ACP agent such as Claude Code when `mock_agents` is off). The
`steps` of `GET /islands/{id}/tasks/{task_id}` show the result of every visit,
with `summary` holding what the resident said: the planner's plan, the coder's
commit note, the test output, the review, the PR text.

```json
{
  "id": "t_d5d447", "title": "Add hello.txt", "status": "done", "building": "post_office", "agent": "jun",
  "pr_url": null, "description": "...", "issue": null, "branch": "mii/t_d5d447", "created_at": "...",
  "steps": [
    {"building": "mailbox", "building_type": "mailbox", "resident": "jun", "brain": "claude-code",
     "outcome": "pass", "summary": "1. Create hello.txt ...", "timed_out": false, "bubbles_raised": 0,
     "started_at": "...", "finished_at": "...", "seconds": 21.4}
  ],
  "events": [{"event_id": 41, "type": "activity", "agent": "carlos", "text": "Write hello.txt", "ts": "..."}]
}
```

`events` is the island's stored event stream between the task's `in_progress`
and `done`/`failed` events (an island runs one task at a time, so that window
is the task's), in the shape planned for `GET /islands/{id}/events`.

## Insights payload

`GET /islands/{id}/insights?range=today|week|month` feeds the game's Insights
screen (`game/scenes/insights/`) and the News tab's "This week" box. Every
field below is filled in (`insights.py`); the game still puts a dash on
anything `null`. The range is the last 1 / 7 / 30 local calendar days ending
today; `*_before` is the same number for the range before that.

```json
{
  "range": "week",
  "island": {
    "tasks_done": 12, "tasks_failed": 1, "prs_opened": 9,
    "tasks_done_before": 8, "prs_opened_before": 6,
    "answer_seconds": 360, "answer_seconds_before": 420,
    "tests_pass_rate": 0.96, "flaky_tests": 2,
    "bubbles_answered": 11, "keys_lent": 4, "prs_merged": 7, "avg_issue_to_pr_seconds": 1840
  },
  "per_day": [{"day": "2026-10-12", "done": 3}, {"day": "2026-10-13", "done": 5}],
  "tip": "The Mailbox is looking busy. Another Planner could sort some letters.",
  "residents": [], "brains": [], "buildings": []
}
```

- `*_before`: the same number for the previous range ("↑ 4 on last week").
- `answer_seconds`: average time from a bubble appearing to your answer, from
  the stored `bubble` / `bubble_resolved` events. `bubbles_answered` counts
  bubbles that didn't time out; `keys_lent` counts permission bubbles answered
  with anything but a rejection.
- `tests_pass_rate` (0-1): Lab visits that passed. `flaky_tests`: tasks whose
  tests failed at least once and then passed.
- `prs_merged`: PRs merged in the range. `insights.py` asks `gh pr view` about
  up to 10 unmerged github.com PRs per request and remembers `Task.merged_at`.
- `per_day`: tasks finished per day in the range, oldest first, today last.
- `tip`: one friendly suggestion from a short list of rules (nothing this
  range, a building that bounces packages, unanswered bubbles, a resident who
  asks a lot, a failing Lab, more deliveries than last time).
- `residents` / `brains` / `buildings` cover the steps in the range.
- "Where packages are waiting" doesn't need anything here: the game counts the
  open tasks in the live snapshot by `building`.

## Events history (for Island News)

`GET /islands/{id}/events?limit=200` lets the game's News tab
(`game/scenes/news/`) show what happened before it connected. It's one query
on the `Event` table (`limit` is capped at 2000):

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
brain, worktrees, Step recording, step timeouts, ACP runner (house keys, house
rules, gifts as MCP servers, notebook), OpenRouter brain, GitHub issue in / PR
out (against the island's branch), Lab test runs, the full insights payload,
events history, build mode (`set_layout`), replay. Real-mode runs against the
live CLIs still need a hands-on check (see README "Real agents").

## Not in scope (on purpose)

LangChain or other direct-API runners beyond OpenRouter, friendship scores, the
ML team router, several tasks at once on one island. (Islands run in parallel:
`run_forever` hands each task to its island's `Lane`, which runs that island's
packages one at a time. Island News is written by the game from stored events;
the backend only serves them, see "Events history".)
