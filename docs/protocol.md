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
    "map": {
      "version": 1, "shape": "classic",
      "land": ["....................", ".##################.", "...", "...................."],
      "pond": {"x": 7, "y": 6, "w": 5, "h": 2},
      "plants": null,
      "season": "summer"
    },
    "server_time": "2026-10-16T09:41:03+00:00",
    "weather": "sunny",
    "mock_agents": true,
    "buildings": [
      {"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 3, "y": 2, "facing": 0, "resident": "jun"}
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
        "state": "idle", "building": "mailbox", "mood": "happy", "notebook": null,
        "activity": "Reading auth.py",
        "appearance": {"skin": "#F2C6A0", "hair": 12, "hair_color": "#3B2418", "eyes": 3, "brows": 1, "nose": 0, "mouth": 2}
      }
    ],
    "bubbles": [
      {"id": "b_3f2a", "agent": "carlos", "kind": "confused", "text": "Which test file?", "asked_at": "2026-10-16T09:39:12+00:00"}
    ],
    "tasks": [
      {"id": "t_12", "title": "Fix login typo", "status": "in_progress", "building": "lab", "agent": "carlos", "pr_url": null, "issue": 42}
    ],
    "friendships": [
      {"a": "carlos", "b": "jun", "handoffs": 7, "score": 0.679}
    ]
  }
}
```

- `repo`: the island's GitHub repo (`owner/name`) or `null`. `path`: the island's
  local git repository (every island is a repository; see `GET /islands` and
  `POST /islands` in architecture.md). `branch`: what residents branch off from,
  and what the Post Office's PRs target. An island founded with a `repo` is a
  `gh repo clone` of it; one founded with `existing_repo` (a local git folder)
  gets `repo` from that folder's GitHub `origin` (`null` if it has none, so no
  PRs) and `branch` from its checked-out branch unless one is given.
  `plan`: the town plan its steps match (`classic`, `quick_fixes`) or `custom`. `rules`: house rules from
  the island creator (`true` = ask me before doing this). `shell` and
  `install_packages` turn a resident's `execute` key from `yes` into `ask` (for
  every command, or for commands that look like a package install). `merge_pr`
  makes the Courthouse ask you (a `ready` bubble, "Approve & merge" / "Send
  back") before the Post Office merges the PR it opens; off, it merges without
  asking.
- `created_at`: when the island was founded (ISO-8601 UTC). The game counts
  days from it ("Day 1" is the founding day, in the player's local time).
  `server_time`: the server's clock when the snapshot was made, for the same sum.
- `weather`: `sunny | cloudy | rainy | night`, the island's mood read off its
  recent work (see the `weather` event). `mock_agents`: `true` while every
  resident runs on the free mock brain (worth a small badge).
- `map`: how the island looks: land, pond, plants, season (see "Island map" below).
- `buildings[].x / y`: the top-left tile of the building's 3×3-tile lot.
  `facing`: where its door looks: `0` south (toward the camera), `1` east,
  `2` north, `3` west.
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
  `database`, `terminal`, `godot`, `video`) the resident brings to every ACP session (the `GIFTS`
  table in `backend/mii/agents/acp.py`). `GET /gifts` says which ones work on
  this machine. The `openrouter` brain has no MCP client, so it ignores them.
- `residents[].appearance`: how the resident's Mii looks. Only the game reads it
  (`MiiParts` in `game/scenes/mii/mii_parts.gd`): `skin` and `hair_color` are
  `#RRGGBB`; `hair` (`-1` = bald, the last few are hats), `eyes`, `brows`,
  `nose`, `mouth` are style numbers. `{}` until the creator sets it; the game
  then picks a look from the resident's id. The server only checks it's a flat
  object of at most 24 short keys with numbers, booleans, or short strings,
  and otherwise stores and echoes it untouched (docs/mii-customization.md).
- `residents[].notebook`: how full the agent's context window is, 0.0-1.0, or
  `null` until the agent reports it (see the `notebook` event).
- `residents[].activity`: the last `activity` line the resident reported (`""`
  if none yet), so a client that connects mid-task shows it right away.
- `bubbles`: bubbles still waiting for an answer. `asked_at` (ISO-8601 UTC) is
  when each one appeared, for "2m ago" after a reconnect.
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
{"type": "bubble", "agent": "carlos", "kind": "permission", "id": "b_9c1d", "text": "Install packages", "options": ["Just this once", "Always", "Not now"]}
```
A permission bubble's `text` says in plain words what the key is for (the agent's
own reason, or a guess from the command), and its `options` are always named by
what they do: `Just this once`, `Always`, `Not now`, `Never` (whichever the agent offers).

`task` (optional): the id of the task package the resident is working on, so
the client can show its title and pull request. `detail` (optional): extra
context shown as-is, such as the exact command or file a permission bubble asks
about:
```json
{"type": "bubble", "agent": "pip", "kind": "permission", "id": "b_7e10", "text": "Add zod to check the settings form", "options": ["Just this once", "Always", "Not now"], "task": "t_12", "detail": "npm install zod@3.23.8"}
```
Snapshot `bubbles[]` have the same fields (`task` and `detail` are `null` when unset).

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
With the `master_key` setting on, every `bubble` is followed right away by its
`bubble_resolved`, answered by the backend: the first option that isn't a refusal,
or "carry on" for a question.

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
`mood`: `happy | focused | confused | frustrated | tired | proud`. The list may grow;
show an unknown mood neutrally. (With a TypeSafe key saved, the backend's judge reads the
mood off what the resident just did; otherwise simple rules pick it. The game adds a few
"vibes" of its own on top, such as sleepy or bored, from the snapshot alone.)

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

### `thinking`
The resident's train of thought while carrying a package, streamed from their
agent in small batches (about once a second). Append `text` to what you have for
that `task`; it is a piece of a longer stream, not a full message.
```json
{"type": "thinking", "agent": "carlos", "task": "t_12", "kind": "thought", "text": "The rate limiter should live in middleware, not the view. "}
```
`kind`: `thought` (the agent's own reasoning, when it shares it), `reply` (what
it is writing back), or `tool` (a tool it is calling, one line). Stored like
every event, so `GET /islands/{id}/tasks/{task_id}` returns them for a package
already under way.

A `tool` event may carry three more fields, for the dropdown's summary and its
"technical details":
```json
{"type": "thinking", "agent": "carlos", "task": "t_12", "kind": "tool", "text": "Edit src/limits.py",
 "tool": "edit", "call": "toolu_01", "detail": "--- src/limits.py\n+++ src/limits.py\n@@ ...\n+RATE = 10"}
```
- `tool`: what sort of tool: `read`, `edit`, `delete`, `move`, `search`,
  `execute`, `fetch`, `think` or `other` (missing = `other`).
- `call`: the tool call's id. A later `tool` event with the same `call` updates
  that line (new `text` and `detail`) instead of adding one.
- `detail`: the raw part, at most ~2000 characters: a diff, the command, or the
  tool's input as JSON. Missing when there's nothing to add.

### `task`
A task package's status changed. Lets the client show who is carrying a package.
`pr_url` is set once the post office opens a pull request.
```json
{"type": "task", "id": "t_12", "title": "Fix login typo", "status": "in_progress", "building": "lab", "agent": "carlos", "pr_url": null, "issue": 42}
```
`issue` is the GitHub issue number the package came from, or `null`.

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
{"type": "layout", "buildings": [{"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 4, "y": 15, "facing": 0, "resident": "jun"}],
 "roads": [{"from": "mailbox", "to": "workshop", "condition": "always"}]}
```

### `island`
The island's header changed (renamed, a repo linked, branch or house rules
edited, by any client). Same fields as the top of the snapshot: replace them.
```json
{"type": "island", "island": {"id": "sunny", "name": "Sunnier Isle", "repo": "me/sunny", "path": "/home/you/Documents/Mii-nagement/sunny", "branch": "main", "plan": "classic", "rules": {"merge_pr": true, "install_packages": true, "shell": false}, "created_at": "2026-10-04T09:12:00+00:00"}}
```

### `chat`
One resident said something to another: a request for help, the helper's reply, or a
note left with a `SAY TO <Name>:` line. Show a speech bubble over `from`; there is
nothing to answer. Residents see what was said to them on their next step of that task.
```json
{"type": "chat", "from": "carlos", "to": "jun", "task": "t_12", "text": "I'm stuck at the Lab on 'Fix login typo'. Can you take a look?"}
```

### `weather`
The island's weather changed. `sunny` when the last few steps mostly passed (or
nothing has happened yet), `cloudy` when about half failed, `rainy` when most
failed, `night` while every resident is asleep. Sent after a step or a
sleep/wake changes it, never more often than it changes.
```json
{"type": "weather", "weather": "rainy"}
```

### `talk_thinking` / `talk_reply` / `talk_error`
Answers to a `talk` message (see Client → server). Sent **only to the client that
sent `talk`** and never stored (they don't show up in News or replays). `id` is the
talk id the client chose.
```json
{"type": "talk_thinking", "id": "k_7c1e", "agent": "carlos", "text": "Reading src/auth.py"}
{"type": "talk_reply", "id": "k_7c1e", "agent": "carlos", "text": "The rate limiter lives in middleware.py. Want me to explain it?"}
{"type": "talk_error", "id": "k_7c1e", "agent": "carlos", "message": "carlos is asleep (no claude-code login)."}
```
`talk_thinking` is optional, zero or more times (one line: a tool the agent is using).
Exactly one `talk_reply` or `talk_error` ends the turn.

### `error`
The server couldn't handle something the client sent. If the message carried a
`request_id`, the error carries it back, so the client knows which command failed.
```json
{"type": "error", "message": "unknown bubble id: b_nope", "request_id": "r_17"}
```

---

## Client → server

Any message may carry a `request_id` (a string of the client's choosing). The
server never uses it except to echo it on an `error` reply. Success shows up as
the broadcast event the command produces.

The game sends `answer_bubble`, `create_resident`, `update_resident`,
`delete_resident`, `cancel_task` and `talk` today; `create_task`, `set_layout` and
`update_island` work too but the game uses their HTTP twins (POST tasks, PUT
layout with a map) or has no UI for them yet. It doesn't send `request_id`.

### `answer_bubble`
You answered a bubble. The server sends the answer to the waiting resident, then
sends `bubble_resolved` to every client.
```json
{"type": "answer_bubble", "id": "b_3f2a", "response": "Use tests/test_login.py"}
```
If the bubble ID is unknown or already resolved, the server replies with `error`.

For a bubble with `options`, `response` is one of them. A `ready` bubble's
response may add a note for the resident after a colon, e.g.
`"Send back: also test IPv6"` (the package goes back to the Workshop with it).

Where bubbles come from: ✋ `permission` when an agent wants a house key set to
`ask`; ❓ `confused` when an agent ends its turn with a `QUESTION:` line (ACP
brains) or calls `ask_user` (OpenRouter); ✅ `ready` from the Courthouse once
the reviewer passes work that will become a PR; 💤 `tired` when a brain isn't
logged in. Mock residents raise any kind at random.

### `create_resident`
Move a new resident onto the island (the resident creator's "Move in"). The
server picks the `id` and home building (the first building of their job:
planner → `mailbox` (then `post_office`), coder → `workshop` (then `lab`), reviewer →
`courthouse`; `JOB_BUILDINGS` in backend/mii/vocab.py), then sends a `resident` event to
every client.
```json
{
  "type": "create_resident",
  "resident": {
    "name": "Mo", "job": "coder", "color": "#9B87F5", "catchphrase": "Let me poke at it!",
    "brain": "claude-code", "model": null,
    "bluntness": 0.2, "caution": 0.8, "chattiness": 0.6, "creativity": 0.4,
    "keys": {"read": "yes", "edit": "ask", "execute": "ask", "fetch": "no"},
    "gifts": ["docs"],
    "appearance": {"skin": "#F2C6A0", "hair": 12, "hair_color": "#3B2418", "eyes": 3, "brows": 1, "nose": 0, "mouth": 2}
  }
}
```
`name` is required (1-20 characters). Every other field is optional and gets a
default. `brain` is one of `mock`, `claude-code`, `codex`, `gemini`, `openrouter`.
Unknown `job`, `brain`, key values, or gifts, and an `appearance` that isn't a
small flat object, are rejected with `error`.

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
GitHub repo, and optional `attachments` (see `POST /islands/{id}/tasks` in
http-api.md). The server replies with a `task` event (status `queued`) to every
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
    {"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 4, "y": 15, "facing": 0, "resident": "jun"},
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

### `update_island`
Rename the island, link (or change) its GitHub repo, or edit its branch or house
rules after founding. Only the fields sent change. Linking a repo also points
the island repository's `origin` at it, so the post office can push.
```json
{"type": "update_island", "island": {"name": "Sunnier Isle", "repo": "me/sunny", "rules": {"shell": true}}}
```
The server replies with `error` (bad name or repo, nothing to change), or an
`island` event to every client. Same thing over HTTP: `PATCH /islands/{id}`.

### `talk`
Chat with a resident at a building (the building panel → a resident → Talk). The
resident's brain answers in character, in that building's role, **read-only**: it
may read the island's repository but never changes files, raises no bubbles, and
the conversation never touches a package. In real mode it runs in a throwaway
worktree; in mock mode the reply is canned.
```json
{"type": "talk", "id": "k_7c1e", "resident": "carlos", "building": "workshop",
 "message": "Where does the rate limiter live?",
 "history": [{"from": "you", "text": "Hi!"}, {"from": "resident", "text": "Hey! Busy day at the Workshop."}]}
```
`id` is the client's (short, letters/digits/`_`/`-`), echoed on every answer.
`history` is the conversation so far (the server keeps none; it uses the last 10
turns). `building` is optional (default: the resident's home building). Answers:
`talk_thinking` (optional), then `talk_reply` or `talk_error`, to the sender only.

---

## Island map

An island is a grid of 20 × 14 tiles. The game draws one tile as 2 × 2 world
units, centered on the origin: tile `(c, r)` is centered at
`x = (c - 9.5) * 2`, `z = (r - 6.5) * 2`.

- `land`: 14 strings of 20 characters, `#` = land, `.` = sea. The grid's edge is
  always sea (room for the beach), and the land is one piece.
- `pond`: a rectangle of tiles on land, or `null`.
- `plants`: `null` = the game arranges trees and bushes itself (and re-arranges
  them when buildings move). A list = placed by hand in build mode:
  `{"kind": "tree" | "bush", "x", "y" (tile units, may be fractional), "rot" (degrees), "scale"}`.
- `season`: `spring`, `summer`, `autumn`, or `winter`, picked by the owner.
- `shape`: the preset it started from (informational).

Buildings stand on a 3 × 3-tile lot. Its door opens onto the tile just outside
the middle of the side it faces. Rules the server checks (`validate_map` in
`backend/mii/islands.py`): every lot plus the ring of tiles around it is land;
lots don't overlap each other or the pond; every door opens onto free land; and
all doors can reach each other over free land. The **paths** are not stored: the
game draws them between the doors over free land, so everyone sees the same ones.

### Workflow steps

Each building type is one workflow step. An island has `mailbox`, `workshop`,
and `post_office`, plus any of `lab` and `courthouse`. Roads always follow the
order mailbox → workshop → lab → courthouse → post_office over the steps the
island has: a check step (`lab`, `courthouse`) goes on with `pass` and back to
the `workshop` with `fail`; the others go on with `always`.

### Build mode: `PUT /islands/{id}/layout` with a `map`

This is what the game's build mode sends: the island's map and where each
workflow step stands. (Without `map`, the same URL takes the whole workflow
graph by hand, `{"buildings", "roads"}`, like `set_layout` above.)

```json
{"map": {...}, "buildings": [{"type": "workshop", "x": 8, "y": 2, "facing": 0}]}
```

The server validates, saves, and answers with the new snapshot, which it also
sends as a `snapshot` event to every client watching the island. Buildings keep
their ids. Adding a step builds it (with a resident of the right job, if any) and
rebuilds the roads; removing one sends its residents to another building of
their job. Errors: `400` (`{"error": "..."}`, a rule above is broken), `404`,
and `409` when the steps would change while a task is in progress.

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
5b. A free neighbour may take a visit instead of the building's owner, at most twice per
   task: when the package keeps coming back to a work building after a failed check (the
   owner retries once first; the judge decides from the third visit, or from the second when
   it came back with the same failure as last time), or right away when a failing reply had
   a `HELP:` line. Then: `chat` (owner → helper), `handoff` to the helper,
   their `move` / `agent_state`, `activity` and `bubble`s, `chat` back (helper → owner),
   `mood`. The helper carries the package on, so the next handoff comes from them.
6. The step finishes with an outcome (`pass` or `fail`), and the engine picks the
   next road:
   - a road whose `condition` matches the outcome wins;
   - otherwise an `always` road;
   - no matching road means the task is finished (`task: done`, or `failed` if the
     last outcome was `fail`).
   - red tests at the Lab (`TEST_COMMAND` failed) go straight back to the Workshop
     the package last came through, whatever the roads say.
   - a package that would visit one building a fourth time, or a 21st step, stops:
     one `activity` ("Gave up at the Workshop: <last failure>") and `task: failed`.

When a resident has nothing to do they go `idle`, and eventually `sleeping`.

## Seed island IDs

The Godot client finds buildings and residents by these IDs, so they must match
`backend/mii/seed.py`.

The seed island uses the `classic` shape (`SHAPES` in `backend/mii/islands.py`).

| Building id   | type          | resident | lot (x, y) | facing |
|---------------|---------------|----------|------------|--------|
| `mailbox`     | `mailbox`     | `jun`    | (3, 2)     | 0      |
| `workshop`    | `workshop`    | `carlos` | (8, 2)     | 0      |
| `lab`         | `lab`         | `carlos` | (13, 2)    | 0      |
| `courthouse`  | `courthouse`  | `sydney` | (15, 8)    | 3      |
| `post_office` | `post_office` | `jun`    | (2, 8)     | 1      |

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

In the 3D client, the island is built from the snapshot's `map` and
`buildings` (`game/scenes/island/island_builder.gd`); each building **type** becomes
a node under `Island/Buildings/` (`mailbox` → `Mailbox`, `post_office` →
`PostOffice`). Building ids are unique across all islands, so other islands use
ids like `maple_mailbox`; the client always goes by `type`.
