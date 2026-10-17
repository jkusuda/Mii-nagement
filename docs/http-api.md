# HTTP API

Every endpoint of the backend (`backend/mii/urls.py` → `backend/mii/views.py`).
The WebSocket messages are in [protocol.md](protocol.md); how the pieces fit is in
[architecture.md](architecture.md). All bodies are JSON; an error is
`{"error": "<readable reason>"}` with a 4xx/5xx status.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws?island=<id>[&token=<jwt>]` | snapshot + live events; the game sends `answer_bubble`, `create_resident`, `update_resident`, `delete_resident`, `create_task`, `cancel_task`, `set_layout`, `update_island`. With login on: close code 4401 for a bad token, 4403 for someone else's private island (protocol.md) |
| `GET /health` | is the server up, which mode, which judge (`rules` / `jev` / `gemini`), which database (`sqlite` / `postgres` / `timescale`) |
| `GET /meta` | the vocabulary the game shows pickers for: jobs, traits, brains, gifts, keys, building types, job buildings, road conditions, town plans, island shapes, house rules, bubble kinds, moods, states, task statuses, weather kinds (all from `backend/mii/vocab.py`) |
| `GET /brains` | `{brain: true/false}`: which brains can run here (the creator greys out the rest) |
| `GET /brains/detail` | per brain: `ok`, `installed`, `logged_in` (best effort), `api_key`, `hint` |
| `GET /brains/{brain}/models?refresh=1` | the models a brain offers here: `{"models": [{"id", "name", "description"}], "current": its default}`. `claude-code`, `codex`, `gemini`: the backend starts the CLI in a throwaway git worktree, opens a session (no prompt, nothing spent), reads its `model` config option and closes it; can take a few seconds, cached 10 minutes (`refresh=1` asks again); 503 `{"error"}` when the CLI is missing, not logged in or too slow. `openrouter`: the catalogue (as `GET /models`). `mock` or unknown: 404 |
| `GET /gifts` | per gift (MCP server): `ok`, `label`, `hint` (the creator greys out the rest) |
| `GET /models` | the OpenRouter catalogue `{"models": [{id, name, context_length}]}` (needs `OPENROUTER_API_KEY`; 503 without): the resident creator's model picker and Settings' default-model dropdown |
| `GET /islands` | every island the caller may see (id, name, repo, created_at, owner, members, ...: the Archipelago screen), plus `default_folder` for new repositories. With login on: the public islands, plus yours and those you are a member of |
| `POST /islands` | found an island: `{"name", "repo"?, "branch"?, "shape"?, "steps"? \| "plan"?: "classic" \| "quick_fixes", "season"?, "rules"?, "folder"?, "existing_repo"?}` → its snapshot (400 `{"error"}` with a readable reason). The repository comes one of three ways: no `repo` → a new repo with a README in `<folder>/<id>`; `repo` → `gh repo clone owner/name <folder>/<id>` (needs `gh` installed and logged in; the folder must be empty or missing; checks out `branch`, default the repo's default branch; an empty GitHub repo gets a first README commit); `existing_repo` → that git folder as is (needs a commit; `repo` is read from its GitHub `origin` when not given, `branch` defaults to its checked-out branch). Then it lays out the shape's lots and the roads for the steps. Cloning runs in a worker thread and can take a while. With login on: 401 without a token, and the island's `owner` is you. On the public demo (`PUBLIC_DEMO=1`): `folder` and `existing_repo` are refused (400) |
| `GET /github/repos?q=` | the user's repos, through their logged-in `gh` (island creator) |
| `GET /github/branches?repo=` | a repo's branches, through `gh` |
| `GET /islands/{id}` | the island snapshot (plus `mock_agents`), the same object the `snapshot` event carries |
| `PATCH /islands/{id}` | `{"name"?, "repo"?, "branch"?, "rules"?, "members"?}`: rename, link a repo (also sets the repository's `origin`), change the branch or house rules, or (the owner only, login on) the member list → the island header, and an `island` event to every client |
| `GET /islands/{id}/issues?limit=50` | the linked repo's open GitHub issues through `gh` (400 without a repo), to start a task from one |
| `DELETE /islands/{id}` | abandon the island (rows go, the git repository stays on disk). 409 while a task runs there or if it is the last island. With login on or on the public demo, only the owner may (401 without a token, 403 for members and for public islands) |
| `DELETE /islands/{id}/residents/{resident_id}` | the resident moves away (409 if a building still counts on them) |
| `GET /islands/{id}/tasks` | the island's tasks, newest first, each with its `steps` (`?status=done,failed&limit=50`) |
| `POST /islands/{id}/tasks` | `{"title": ..., "description": ...}` or `{"issue": 42}`, optionally with `"attachments": [{"name": "spec.md", "content": "<base64>"}]` → the queued task. The engine runs it through the island's residents' brains. See "Attachments" below |
| `GET /islands/{id}/tasks/{task_id}` | one task: `steps` (building, resident, brain, outcome, `summary` = what the resident reported) + `events` (the stored events while it ran) |
| `POST /islands/{id}/tasks/{task_id}/cancel` | cancel a queued task or stop the running one (409 if it already finished) |
| `GET /islands/{id}/insights?range=week` | stats for the Insights screen and News' "This week" (`today` / `week` / `month`) |
| `GET /islands/{id}/events?limit=200` | stored events, oldest first, for the News tab (see "Events history") |
| `PUT /islands/{id}/layout` | build mode over HTTP: `{"buildings", "roads"}` → the saved layout (same rules as `set_layout` in protocol.md); or the game's form `{"map", "buildings": [{type, x, y, facing}]}` → the new snapshot (also sent to every client as a stored `snapshot` event), 400 / 409 (steps changing during a task), see protocol.md "Island map" |
| `GET /settings` | effective settings (incl. `judge` = `auto` / `off` / `jev` / `gemini`, `judge_model`, `spectator_url`, `godot_path`), what `.env` locks, which API keys are set (incl. `typesafe_api_key`, `spectator_secret`) |
| `PUT /settings` | `{"settings": {...}, "secrets": {"openrouter_api_key": "sk-..." \| null}}`; keys go to the keychain and are never echoed. 403 on the public demo; 401 without a token when login is on |
| `POST /replay/{task_id}?speed=4` | replay a recorded task (demo safety net); private islands as below |
| `GET /auth/config` | `{"enabled": bool}`: whether the game shows "Log in" (on when `AUTH0_DOMAIN`, `AUTH0_AUDIENCE` and `AUTH0_CLIENT_ID` are all set) |
| `POST /auth/device` | start a device login (proxied to Auth0): `{user_code, verification_uri_complete, device_code, interval, expires_in}`. 404 when login is off, 502 when Auth0 can't be reached |
| `POST /auth/token` | `{"device_code"}`: poll every `interval` seconds → `{"pending": true}` until approved, then `{access_token, expires_in, name, email}`; 400 `{"error"}` for an expired or denied code (start again) |
| `GET /auth/me` | the claims of the caller's token (401 without a valid one) |

**Login (optional).** With login on, send `Authorization: Bearer <access_token>` on every
call (the WebSocket takes `?token=`). Islands without an `owner` stay public. Every
`/islands/{id}/...` endpoint answers 401 (no or bad token) or 403 (not the owner or a
member) for a private island; a 401 anywhere means the token expired (24 hours): log in again.

## Attachments

A task can carry files (the Mailbox form's "Attach files…"): up to 10, at most
2 MB each and 8 MB together, sent base64-encoded. Names are cut to a plain file
name (`../../x/notes.md` → `notes.md`, odd characters → `_`, repeats → `notes-2.md`);
a bad list, bad base64 or too much data is a 400 (a body over 12 MB is a 413).
They're stored with the task (`Attachment` table). When the task starts, real mode
writes them to `.mii-attachments/` in its worktree, with a `.gitignore` of `*` so
they're never committed, and every resident's prompt lists them. Mock mode keeps
them and lists them too; the mock planner "reads" each one in the thinking dropdown.

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
and its last `task` event (`done`, `failed` or `cancelled`; an island runs one task at a
time, so that window is the task's), in the shape of `GET /islands/{id}/events`
(`replay.task_window` finds it; replay uses the same window).

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
