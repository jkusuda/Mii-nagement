# Mii-nagement WebSocket Protocol

This file is the **single source of truth** for messages between the backend
(`backend/`) and the Godot client (`game/`). If you change a message, change it
here first, then update both halves.

- Endpoint: `ws://localhost:8000/ws?island=<id>` (leave out `island` for the
  server's first island). A client only gets events for the island it watches.
- Every message is one JSON object with a `"type"` field.
- The server sends a `snapshot` as soon as a client connects, then a live stream
  of events.
- Every server event is also stored in SQLite (the `Event` table), so a session can
  be replayed later.

## Rules for both sides

1. **Ignore what you don't understand.** Unknown `type`s and unknown fields must
   be skipped, not treated as errors. This lets one half get ahead of the other.
2. **The server may add `"ts"`** (ISO-8601 UTC time) to any event. Clients can
   ignore it.
3. **IDs are strings.** Agents, buildings, bubbles, and tasks all use string IDs.
4. **The game never decides anything.** It shows events and sends your input.
   All workflow and agent logic lives in the backend.

---

## Server → client

### `snapshot`
The full island state, sent once when a client connects (and again on reconnect).
The client should rebuild its view from scratch when it gets one.

```json
{
  "type": "snapshot",
  "island": {
    "id": "sunny",
    "name": "Sunny Isle",
    "repo": null,
    "path": "/home/you/Documents/Mii-nagement/sunny",
    "branch": "main",
    "plan": "classic",
    "rules": {"merge_pr": true, "install_packages": true, "shell": false},
    "created_at": "2026-10-04T09:12:00+00:00",
    "buildings": [
      {"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 4, "y": 15, "resident": "jun"}
    ],
    "roads": [
      {"from": "lab", "to": "courthouse", "condition": "pass"}
    ],
    "residents": [
      {
        "id": "jun", "name": "Jun", "job": "planner",
        "brain": "gemini", "model": null, "color": "#F4A261",
        "catchphrase": "Let's map it out!",
        "bluntness": 0.3, "caution": 0.7, "chattiness": 0.6, "creativity": 0.5,
        "keys": {"read": "yes", "edit": "ask", "execute": "ask", "fetch": "no"},
        "gifts": [],
        "state": "idle", "building": "mailbox", "mood": "happy", "notebook": null
      }
    ],
    "bubbles": [
      {"id": "b_3f2a", "agent": "carlos", "kind": "confused", "text": "Which test file?"}
    ],
    "tasks": [
      {"id": "t_12", "title": "Fix login typo", "status": "in_progress", "building": "lab", "agent": "carlos", "pr_url": null}
    ],
    "friendships": [
      {"a": "carlos", "b": "jun", "handoffs": 7, "score": 0.679}
    ]
  }
}
```

- `repo`: the island's GitHub repo (`owner/name`) or `null`. `path`: the island's
  local git repository (every island is a repository; see `GET /islands` and
  `POST /islands` in architecture.md). `branch`: what residents branch off from.
  `plan`: the town plan the island was founded with. `rules`: house rules from
  the island creator (`true` = ask me before doing this). `shell` and
  `install_packages` turn a resident's `execute` key from `yes` into `ask` (for
  every command, or for commands that look like a package install); `merge_pr`
  is stored only: the post office opens PRs and never merges them.
- `created_at`: when the island was founded (ISO-8601 UTC). The game counts
  days from it ("Day 1" is the founding day, in the player's local time).
- `buildings[].x / y`: the building's lot on a grid. The 3D client currently
  places buildings itself (`island_3d.tscn`); x/y are for build mode later.
- `buildings[].resident`: the resident who works there.
- `residents[].brain`: which coding agent runs the resident: `mock`,
  `claude-code`, `codex`, `gemini`, or `openrouter`. `model` is an optional model
  override (`null` = the brain's default; for `openrouter` it is an OpenRouter
  model id such as `anthropic/claude-sonnet-4.5`). Use it for a small brain badge.
- `roads[].condition`: `always`, `pass`, or `fail` (see the workflow section below).
- `residents[].bluntness / caution / chattiness / creativity`: personality
  sliders, 0.0-1.0 (see `backend/mii/personality.py`).
- `residents[].keys`: house keys, what the resident may do without asking.
  Keys `read`, `edit`, `execute`, `fetch` (ACP tool kinds), each `yes`, `ask`,
  or `no`. The ACP runner honours them: `yes` answers the agent's own
  "allow once" option, `ask` shows a ✋ bubble, `no` rejects.
- `residents[].gifts`: ids of extra MCP servers (`browser`, `github`, `docs`,
  `database`, `terminal`) the resident brings to every ACP session (the `GIFTS`
  table in `backend/mii/agents/acp.py`). `GET /gifts` says which ones work on
  this machine. The `openrouter` brain has no MCP client, so it ignores them.
- `residents[].notebook`: how full the agent's context window is, 0.0-1.0, or
  `null` until the agent reports it (see the `notebook` event).
- `bubbles`: bubbles still waiting for an answer.
- `tasks[].status`: `queued`, `in_progress`, `done`, `failed`, or `cancelled`.
- `friendships`: the friendship meter, one entry per pair of residents who have
  handed packages to each other (`a` < `b` by id). `handoffs` counts them,
  `score` is 0.0-1.0 and grows with every handoff (5 → 0.56, 10 → 0.80).

### `agent_state`
A resident's activity changed.
```json
{"type": "agent_state", "agent": "carlos", "state": "working", "building": "workshop"}
```
`state`: `idle | walking | working | sleeping`.
While `walking`, `building` is where they're **headed**.

### `move`
A resident is walking from one building to another. The client works out the
path along the island's roads and animates it.
```json
{"type": "move", "agent": "carlos", "from": "workshop", "to": "lab"}
```
The server doesn't wait for the animation. If the next event for that resident
(for example `agent_state: working`) arrives before the walk finishes, the client
can let the walk finish first or snap the resident to the destination.

### `bubble`
A resident needs you. Shown as an emoji above their head; click it to answer.
```json
{"type": "bubble", "agent": "carlos", "kind": "confused", "id": "b_3f2a", "text": "Which test file should I update?"}
```
`kind`: `confused | struggling | permission | ready | tired`.

`options` (optional): when present, show these as buttons and send the chosen
one back as the `response`. Agents' permission requests use it:
```json
{"type": "bubble", "agent": "carlos", "kind": "permission", "id": "b_9c1d", "text": "May I run `npm install`?", "options": ["Allow once", "Always allow", "Reject"]}
```

| kind         | meaning                                  | suggested emoji |
|--------------|------------------------------------------|-----------------|
| `confused`   | needs clarification                      | ❓              |
| `struggling` | stuck after retries                      | 💦              |
| `permission` | wants approval for a risky action        | ✋              |
| `ready`      | finished something you should look at    | ✅              |
| `tired`      | hit a budget or limit, needs rest        | 💤              |

### `bubble_resolved`
A bubble was answered (by any client) or timed out. Remove it.
```json
{"type": "bubble_resolved", "id": "b_3f2a", "answered": true, "response": "Use tests/test_login.py"}
```
`answered` is `false` (and `response` `null`) when the bubble timed out. The
insights endpoint counts answers and answer times from these.

### `handoff`
A task package passed from one resident to another.
```json
{"type": "handoff", "from": "carlos", "to": "sydney", "task": "t_12", "outcome": "success"}
```
`outcome`: `success | fail`. This is the result of the step the giver just
finished. For example, a failed review hands the package back to the coder with
`fail`.

### `mood`
A resident's mood changed.
```json
{"type": "mood", "agent": "sydney", "mood": "proud"}
```
`mood`: `happy | focused | confused | frustrated | tired | proud`.

| mood         | suggested emoji |
|--------------|-----------------|
| `happy`      | 😊 |
| `focused`    | 🧐 |
| `confused`   | 😵 |
| `frustrated` | 😤 |
| `tired`      | 😪 |
| `proud`      | 😎 |

### `activity`
What a resident is doing right now, streamed from their agent. Good for thought
bubbles or a status line under their name.
```json
{"type": "activity", "agent": "carlos", "text": "Reading auth.py"}
```

### `task`
A task package's status changed. Lets the client show who is carrying a package.
`pr_url` is set once the post office opens a pull request.
```json
{"type": "task", "id": "t_12", "title": "Fix login typo", "status": "in_progress", "building": "lab", "agent": "carlos", "pr_url": null}
```

### `resident`
A resident was created or edited (by any client). Add it, or replace the
resident with the same `id`. Same shape as `snapshot.island.residents[]`.
```json
{"type": "resident", "resident": {"id": "mo_3f2a", "name": "Mo", "job": "coder", "...": "..."}}
```

### `resident_removed`
A resident moved away (any client asked). Remove them from the island.
```json
{"type": "resident_removed", "id": "mo_3f2a"}
```

### `friendship`
Two residents handed a package between them; their friendship grew. Same
shape as `snapshot.island.friendships[]`: replace the entry for that pair.
```json
{"type": "friendship", "a": "carlos", "b": "jun", "handoffs": 8, "score": 0.728}
```

### `notebook`
How full a resident's context window is (their notebook), from the agent's
usage updates. `value` is 0.0-1.0.
```json
{"type": "notebook", "agent": "carlos", "value": 0.42}
```

### `layout`
The island's buildings or roads changed: build mode (`set_layout`), or a
resident was given a building. Replace `buildings` and `roads` wholesale; the
shapes are the snapshot's.
```json
{"type": "layout", "buildings": [{"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 4, "y": 15, "resident": "jun"}],
 "roads": [{"from": "mailbox", "to": "workshop", "condition": "always"}]}
```

### `error`
The server couldn't handle something the client sent.
```json
{"type": "error", "message": "unknown bubble id: b_nope"}
```

---

## Client → server

### `answer_bubble`
You answered a bubble. The server sends the answer to the waiting resident, then
sends `bubble_resolved` to every client.
```json
{"type": "answer_bubble", "id": "b_3f2a", "response": "Use tests/test_login.py"}
```
If the bubble ID is unknown or already resolved, the server replies with `error`.

### `create_resident`
Move a new resident onto the island (the resident creator's "Move in"). The
server picks the `id` and home building (planner → `mailbox`, coder →
`workshop`, reviewer → `courthouse`), then sends a `resident` event to every
client.
```json
{
  "type": "create_resident",
  "resident": {
    "name": "Mo", "job": "coder", "color": "#9B87F5", "catchphrase": "Let me poke at it!",
    "brain": "claude-code", "model": null,
    "bluntness": 0.2, "caution": 0.8, "chattiness": 0.6, "creativity": 0.4,
    "keys": {"read": "yes", "edit": "ask", "execute": "ask", "fetch": "no"},
    "gifts": ["docs"]
  }
}
```
`name` is required (1-20 characters). Every other field is optional and gets a
default. `brain` is one of `mock`, `claude-code`, `codex`, `gemini`, `openrouter`.
Unknown `job`, `brain`, key values, or gifts are rejected with `error`.

A new resident takes over the buildings of their job that nobody works at yet.
To put them to work outright, add `"buildings": ["workshop", "lab"]` (building
ids): they become the worker at those buildings, taking over from whoever was
there, and the server follows the `resident` event with a `layout` event. The
engine always runs the resident each building points at.

### `update_resident`
Edit a resident (the creator's "Save"). Same fields as `create_resident` plus
`id`; only the fields you send change. The server replies with a `resident`
event to every client, or `error` if the id is unknown.
```json
{"type": "update_resident", "resident": {"id": "mo_3f2a", "caution": 0.9, "gifts": [], "buildings": ["lab"]}}
```

### `delete_resident`
The resident moves away. The server replies with `resident_removed` to every
client, or `error` if the id is unknown, if they are carrying a package, or if a
building still counts on them (send `update_resident` with `buildings` for
someone else first).
```json
{"type": "delete_resident", "id": "mo_3f2a"}
```

### `create_task`
Drop a package in the mailbox (same as `POST /islands/{id}/tasks`). Either a
`title` (with an optional `description`) or an `issue` number of the island's
GitHub repo. The server replies with a `task` event (status `queued`) to every
client, or `error`.
```json
{"type": "create_task", "task": {"title": "Fix login typo", "description": "The button says 'Log im'."}}
```

### `cancel_task`
Take a package back. A queued task is cancelled on the spot; a running one is
stopped mid-step (its agent process is ended and the resident puts the package
down). Every client gets a `task` event with status `cancelled`; a finished or
unknown task answers `error`.
```json
{"type": "cancel_task", "id": "t_12"}
```

### `set_layout`
Build mode: replace the island's buildings and roads. Send the whole layout.
```json
{
  "type": "set_layout",
  "buildings": [
    {"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 4, "y": 15, "resident": "jun"},
    {"type": "lab", "name": "Lab", "x": 20, "y": 15}
  ],
  "roads": [{"from": "mailbox", "to": "workshop", "condition": "always"}]
}
```
- A building with an `id` that already exists is updated in place (its history
  and worker survive); one without an `id` is created (the server picks the id,
  which is why the server's `layout` event is the one to redraw from);
  buildings left out are removed, and residents living there move to the mailbox.
- Rules: exactly one `mailbox`; `type` is one of `mailbox`, `workshop`, `lab`,
  `courthouse`, `post_office`; roads join buildings in the list with
  `condition` `always` / `pass` / `fail`; `resident` must live on the island.
  A building a package is sitting in right now can't be removed.
- The server replies with `error`, or a `layout` event to every client. The
  change applies to the next package (a running one keeps its graph).
  Same thing over HTTP: `PUT /islands/{id}/layout` with `{"buildings", "roads"}`.

---

## How a task flows (typical event order)

The island's buildings and roads form a graph. A task "package" starts at the
`mailbox` and follows roads until it reaches a building with no outgoing road.

For each building the package visits:

1. `handoff`: only if a different resident than the last step takes the package.
2. `move` + `agent_state: walking`: only if that resident isn't already there.
3. `agent_state: working`
4. While working: any number of `activity` events, and optional `bubble`s (each
   waits for `answer_bubble` or a timeout, then `bubble_resolved`). A step that
   runs past the step timeout is stopped and counts as `fail`.
5. `mood`
6. The step finishes with an outcome (`pass` or `fail`), and the engine picks the
   next road:
   - a road whose `condition` matches the outcome wins;
   - otherwise an `always` road;
   - no matching road means the task is finished (`task: done`, or `failed` if the
     last outcome was `fail`).

When a resident has nothing to do they go `idle`, and eventually `sleeping`.

## Seed island IDs

The Godot client finds buildings and residents by these IDs, so they must match
`backend/mii/seed.py`.

| Building id   | type          | resident | lot (x, y) |
|---------------|---------------|----------|------------|
| `mailbox`     | `mailbox`     | `jun`    | (4, 15)    |
| `workshop`    | `workshop`    | `carlos` | (12, 7)    |
| `lab`         | `lab`         | `carlos` | (20, 15)   |
| `courthouse`  | `courthouse`  | `sydney` | (28, 7)    |
| `post_office` | `post_office` | `jun`    | (35, 15)   |

Roads:

| from         | to            | condition |
|--------------|---------------|-----------|
| `mailbox`    | `workshop`    | `always`  |
| `workshop`   | `lab`         | `always`  |
| `lab`        | `courthouse`  | `pass`    |
| `lab`        | `workshop`    | `fail`    |
| `courthouse` | `post_office` | `pass`    |
| `courthouse` | `workshop`    | `fail`    |

| Resident id | name    | job      | color     | brain         |
|-------------|---------|----------|-----------|---------------|
| `jun`       | Jun     | planner  | `#F4A261` | `gemini`      |
| `carlos`    | Carlos  | coder    | `#2A9D8F` | `claude-code` |
| `sydney`    | Sydney  | reviewer | `#9B5DE5` | `codex`       |

With `MOCK_AGENTS=1` (the default), every resident runs on the mock brain anyway.

In the 3D client, each building **type** matches a node under `Buildings/` in
`game/scenes/island_3d.tscn` (`mailbox` → `Mailbox`, `post_office` → `PostOffice`).
Building ids are unique across all islands, so other islands use ids like
`maple_mailbox`; the client always goes by `type`. Buildings an island doesn't
have are hidden.
