# Backend architecture

Mii-nagement is a Tomodachi Life-style manager for coding agents. The backend
runs the workflow; the Godot game only shows events and sends your input
([protocol.md](protocol.md)).

## The idea in one paragraph

An island is a repo. Buildings are workflow steps, roads are handoffs with
`always` / `pass` / `fail` conditions, and a task is a package that travels the
roads. Each building has a resident, and each resident has a **brain**: a real
coding agent (Claude Code, Codex, or Gemini CLI) that we drive over the
**Agent Client Protocol (ACP)**. Users bring the agents they already pay for
(subscription login) or an API key. When an agent needs you, a bubble pops up
over the resident's head.

## Brains

| `Resident.brain` | Agent | How we start it (ACP) | Pays with |
|---|---|---|---|
| `mock` | MockRunner | n/a | free (the default) |
| `claude-code` | Claude Code | `npx -y @zed-industries/claude-code-acp` | Claude plan login, or `ANTHROPIC_API_KEY` |
| `codex` | Codex | `npx -y @zed-industries/codex-acp` | ChatGPT plan login, or `OPENAI_API_KEY` |
| `gemini` | Gemini CLI | `gemini --acp` | Google login, or `GEMINI_API_KEY` |

All three real brains share one code path (`agents/acp.py`); only the start
command differs. Adding another ACP agent is one entry in the `BRAINS` table.
`MOCK_AGENTS=1` forces every resident onto the mock brain.

## Files

```
backend/mii/
  config.py        settings from .env (mock switch, repo path, timeouts)
  models.py        tables + database setup + snapshot builder
  personality.py   job + sliders -> the prompt preamble
  events.py        event bus (save + broadcast) + bubbles
  engine.py        island graph + building handlers + task loop
  residents.py     create / edit residents (from the game's resident creator)
  sandbox.py       one git worktree per task + is_inside() path check
  agents/
    __init__.py    AgentJob, StepResult, BrainRunner, make_runner()
    mock.py        MockRunner
    acp.py         AcpRunner + BRAINS table + available_brains()
  github.py        issue in / PR out, via the `gh` CLI
  insights.py      stats from the Step table
  replay.py        replay a recorded task into the game
  server.py        HTTP + WebSocket endpoints
  seed.py          seed island + mock simulator
  watch.py         prints the event stream (debugging)
```

## Tables

| Table | What it holds |
|---|---|
| `Island` | id, name, GitHub repo |
| `Resident` | name, job, 4 personality sliders, `brain`, optional `model`, catchphrase, color, house `keys`, `gifts`, live state / building / mood |
| `Building` | type, name, lot position, resident |
| `Road` | from, to, condition |
| `Task` | title, status, where the package is, who carries it, `issue_number`, `branch`, `pr_url` |
| `Step` | one building visit: task, building, resident, brain, outcome, timed out?, bubbles raised, start/finish time |
| `Event` | every event sent to the game (history + replay) |

## One step

1. The engine hands the package over (`handoff`), walks the resident to the
   building, and sets them `working`.
2. It builds an `AgentJob`: the resident, the building's instructions, the
   package history, the task's worktree, plus `ask()` (show a bubble, wait for
   the answer) and `report()` (send an `activity` event).
3. The resident's brain runs the job, with a step timeout.
   **AcpRunner** (to build): start the agent in the worktree, send personality +
   instructions + history, stream progress as `activity`, turn permission
   requests into ✋ bubbles with options, allow file reads/writes only inside
   the worktree, read `VERDICT: PASS/FAIL` at the end, close the process.
4. The outcome picks the next road. A `Step` row is saved for insights.

## Safety

- Agents only run inside a git worktree of `SANDBOX_REPO_PATH` (`sandbox.py`).
- Each agent's own safety mode is switched on (Claude Code asks before acting,
  Codex writes only inside its folder, Gemini uses its sandbox).
- Permission requests become bubbles; file requests are path-checked.
- Every step has a timeout, and a package can visit at most 20 buildings.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws` | snapshot + live events; the game sends `answer_bubble`, `create_resident`, `update_resident` |
| `GET /health` | is the server up, which mode |
| `GET /brains` | which agent CLIs are installed |
| `GET /islands/{id}` | the island snapshot |
| `POST /islands/{id}/tasks` | `{"title": ...}` or `{"issue": 42}` |
| `GET /islands/{id}/insights` | stats for the Insights screen |
| `GET /islands/{id}/events` | stored events for the News tab (planned, see below) |
| `POST /replay/{task_id}` | replay a recorded task (demo safety net) |

## Insights payload

`GET /islands/{id}/insights?range=today|week|month` feeds the game's Insights
screen (`game/scenes/insights/`). The game shows whatever is there and puts a
dash on anything missing, so fields can be added one at a time. **Today only
the first three `island` fields exist**; the rest is the shape the screen
expects.

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

Working: engine, roads, bubbles, event bus, personality, mock brain, worktrees,
Step recording, step timeouts, `/brains`, the island part of insights.

Boilerplate (`TODO(mvp)`): `AcpRunner.run`, `github.py`, the rest of
`insights.py`, `replay.py`. List them with:

```bash
grep -rn "TODO(mvp)" backend/mii
```

## Not in scope (on purpose)

LangChain / direct API runners, friendship scores, the ML team router, multiple
islands, running several tasks at once. (Island News is written by the game from
stored events; the backend only serves them, see "Events history".)
