# Architecture

How Mii-nagement fits together, which file does what, and where to look for a change.
Read this first; then [protocol.md](protocol.md) for the WebSocket messages and
[http-api.md](http-api.md) for the HTTP endpoints.

## The two halves

```
 backend/ (Python 3.12, Django + Channels)               game/ (Godot 4, GDScript)
 ┌───────────────────────────────────────┐   WebSocket   ┌──────────────────────────────┐
 │ engine.py  runs packages through the  │ ── events ──▶ │ net.gd (Net autoload)        │
 │            island's buildings          │               │   keeps Net.island, emits    │
 │ agents/    one runner per brain        │ ◀─ commands ─ │   one signal per event       │
 │ events.py  store + broadcast events    │               │ scenes/  draw Net.island,    │
 │ views.py   HTTP endpoints              │ ◀──  HTTP ─── │   send what you click        │
 │ SQLite (models.py), git (sandbox.py)   │               │                              │
 └───────────────────────────────────────┘               └──────────────────────────────┘
```

- **The backend decides everything**: the workflow, the agents, permissions, moods, the
  weather. The game only shows events and sends your input (protocol.md, rule 4).
- **One WebSocket per island** (`/ws?island=<id>`): a `snapshot` on connect, then events.
  The game sends commands on the same socket (`answer_bubble`, `create_resident`, ...).
- **HTTP** for request/response work: founding islands, settings, insights, history,
  build mode's save. Every HTTP call in the game goes through `Net.request_json`.
- **Names both halves share** (building types, states, moods, bubble kinds...) live in
  `backend/mii/vocab.py` and `game/scenes/protocol.gd`. Change protocol.md, then both.

An island is a project: a local git repository (created, cloned, or adopted when the
island is founded). Buildings are workflow steps, roads are handoffs (`always` / `pass` /
`fail`), and a task is a package that travels the roads. Each resident has a **brain**:
Claude Code, Codex or Gemini CLI over the Agent Client Protocol (ACP), any OpenRouter
model, or the free mock brain (the default).

## Where to look

Start from the change you want, open the files in the order given, then run the check.
`make check` runs every check (lint, backend tests, Godot script check).

| To change... | Open, in order | Check |
|---|---|---|
| a server → game event | `docs/protocol.md` → `events.py` (builder) → where it's emitted (`engine.py`, `consumers.py`, `views.py`) → `game/net.gd` (`_handle_message` + a signal) → the scene that listens | `make check` |
| a game → server command | `docs/protocol.md` → `consumers.py` `_handle` → the module that does the work → `net.gd` (a `send_*` function) → the scene that sends it | `make check` |
| an HTTP endpoint | `docs/http-api.md` → `urls.py` → `views.py` (parse + status code only) → the module that does the work → `tests/test_views.py` → the caller, through `Net.request_json` | `make check` |
| a table or column | `models.py` (+ its `*_to_dict` if the protocol shows it) → `uv run python manage.py makemigrations mii` → protocol.md if a field is sent | `make check` |
| what a building does | `engine.py` (its `BuildingHandler`: `instructions()` and `run()`) | `uv run pytest tests/test_engine.py` |
| the package's route / one step | `engine.py` `WorkflowEngine._run_task` and `_run_step`; the order of events is in protocol.md "How a task flows" | `uv run pytest tests/test_engine.py` |
| a new building type | `vocab.py` (`STEP_ORDER`, `STEP_NAMES`) → `island_map.py` (a lot in every `SHAPES` entry) → `engine.py` (a handler) → `game/scenes/protocol.gd` → `game/scenes/buildings/` (a scene) + `building_data.gd` + `island_builder.gd` (`BUILDING_SCENES`) → `island_layout.gd` (`SHAPES`) | `make check` |
| an ACP brain | one entry in `BRAINS` (`agents/acp.py`) and its name in `vocab.ACP_BRAINS`; the game's list is `ResidentData.BRAINS` | `uv run pytest tests/test_acp.py tests/test_vocab.py` |
| a gift (MCP server) | `GIFTS` in `agents/acp.py`; the game's list is `ResidentData.GIFTS` | `uv run pytest tests/test_acp.py` |
| a setting or an API key | `prefs.py` (`SETTINGS` / `SECRETS`) → `game/scenes/ui/settings_popup.gd` → `.env.example` | `uv run pytest tests/test_prefs.py` |
| the prompt residents get | `personality.py` (job + sliders) and `agents/__init__.py` (`build_prompt`, `parse_verdict`) | `uv run pytest tests/test_personality.py tests/test_acp.py` |
| a judge decision (mood, help, flaky...) | `judge.py` (`RuleJudge`, then `JevJudge`) | `uv run pytest tests/test_judge.py` |
| island map rules (land, lots, doors) | `island_map.py` `validate_map` **and** `game/scenes/island/island_layout.gd` `problem()`: keep them in step | `uv run pytest tests/test_island_map.py` + `make godot-check` |
| founding an island, repos | `islands.py` (`create_island`) → `repos.py` (git) → `github.py` (`gh`) | `uv run pytest tests/test_islands.py` |
| a screen or popup | `game/scenes/game.gd` (`show_screen`, the nav) → the screen's folder below; build UI in code with `UI.label` and the theme in `ui/theme.tres` | `make godot-check`, then play it |
| how residents move, emote, talk | the `godot-animation` skill (`.claude/skills/godot-animation/SKILL.md`) | play it |
| a recording of the game | the `demo-video` skill | |

Rules that save time:

- Read a file before changing it, and grep for its callers: names are reused across halves
  (a building type is `mailbox` in Python, GDScript, the `.tscn` file names and protocol.md).
- Never put workflow or agent logic in the game, and never loosen `sandbox.py`'s checks.
- The engine runs on the event loop: use the async ORM (`aget`, `asave`, `async for`) and
  `asyncio.to_thread` for git, `gh` and keychain calls.
- Mock mode (`MOCK_AGENTS=1`, the default) must keep working: it is how the game and the
  tests run without agents.

## Backend map (`backend/mii/`)

Grouped by layer; a module only imports from its own layer or the ones below it.

| Layer | Module | What it does | Tests |
|---|---|---|---|
| entry | `asgi.py` | ASGI app: HTTP (`urls.py`), `/ws` (`consumers.py`), lifespan (starts the engine via `runtime.py`) | |
| entry | `urls.py`, `views.py` | HTTP routes; views parse the request and pick the status code | `test_views.py` |
| entry | `consumers.py` | the `/ws` consumer: checks `?token=` (close 4401 / 4403), sends the snapshot, dispatches commands in `_handle` | `test_residents.py`, `test_tasks.py`, `test_auth.py` |
| entry | `auth.py` | optional Auth0 login: `verify` (JWKS, RS256), `can_see`, `island_guard`, the device-flow proxies behind `/auth/*` | `test_auth.py` |
| entry | `management/commands/` | `manage.py seed [--reset\|--loop]` and `manage.py watch` | |
| runtime | `runtime.py` | the one bus, bubbles, judge, spectator hook and engine per process; `reload()` applies saved settings | `test_judge.py` |
| engine | `engine.py` | `IslandGraph`, the five `BuildingHandler`s, `WorkflowEngine` (one `Lane` per island) | `test_engine.py` |
| engine | `judge.py` | small typed decisions: `RuleJudge` (offline), or an `AskingJudge`: `JevJudge` (TypeSafe Jev) or `GeminiJudge` (Gemini structured output) | `test_judge.py` |
| engine | `events.py` | event builders (one per protocol event), `EventBus` (store + hooks + broadcast), `Bubbles` (ask and wait) | `test_engine.py` |
| engine | `hooks.py` | `SpectatorHook`: copies a few events to the outside spectator feed (outbound only, off by default) | `test_hooks.py` |
| engine | `metrics.py` | Tiger Data: one row per finished step / task in a TimescaleDB hypertable, `per_day` for Insights; a no-op on SQLite | `test_metrics.py` |
| agents | `agents/__init__.py` | `AgentJob`, `StepResult`, `build_prompt`, `parse_verdict`, `permission_for`, `make_runner` | `test_acp.py` |
| agents | `agents/acp.py` | `AcpRunner` (Claude Code, Codex, Gemini over ACP; one kept agent per resident per task), `BRAINS`, `GIFTS` | `test_acp.py` (with `fake_acp_agent.py`) |
| agents | `agents/openrouter.py` | `OpenRouterRunner`: a small tool loop over OpenRouter's chat API | `test_openrouter.py` |
| agents | `agents/mock.py` | `MockRunner`: fake work, random bubbles and outcomes | |
| domain | `islands.py` | found / change / abandon islands, build mode's `set_map` | `test_islands.py`, `test_island_map.py` |
| domain | `island_map.py` | map shapes, `validate_map`, `build_roads` (pure functions) | `test_island_map.py` |
| domain | `residents.py` | create / edit / delete residents (the resident creator) | `test_residents.py` |
| domain | `layout.py` | the workflow graph by hand (`set_layout`), `assign_buildings` | `test_layout.py` |
| domain | `tasks.py` | a request (`title` or GitHub `issue`) → a queued task | `test_tasks.py` |
| domain | `talk.py` | a `talk` message: one read-only chat turn with a resident in a building's role (throwaway worktree in real mode), answered to the sender only | `test_talk.py` |
| domain | `insights.py`, `weather.py`, `replay.py`, `personality.py` | stats, island weather, replaying a recorded task, the system prompt | `test_insights.py`, `test_weather.py`, `test_replay.py`, `test_personality.py` |
| infra | `sandbox.py` | worktrees per task, path checks, `run_shell` (**the safety boundary**) | `test_engine.py` |
| infra | `repos.py`, `github.py` | git repos on disk; the `gh` CLI (issues, PRs, clone) | `test_islands.py`, `test_github.py` |
| infra | `models.py` | the tables and `*_to_dict` (rows → protocol shapes) | |
| infra | `prefs.py`, `config.py`, `settings.py` | user settings + keychain secrets; `.env` overrides; Django settings | `test_prefs.py` |
| base | `vocab.py` | every shared name (steps, states, moods, brains...), data only | `test_vocab.py` |

`seed.py` (the seed island + simulator) and `watch.py` (prints the event stream) back the
two management commands.

## Game map (`game/`)

**Autoloads** (created at startup, reachable by name everywhere; `project.godot`):

| Autoload | File | What it does |
|---|---|---|
| `Net` | `net.gd` | the WebSocket, `Net.island` (the live snapshot), one signal per event, `send_*` commands, `request_json` for HTTP |
| `Islands` | `islands.gd` | every island (`GET /islands` + their snapshots) and their 3D thumbnails, for the home screen |
| `Clock` | `clock.gd` | time of day (+ the dev slider), "Day N", a fallback weather roll |
| `Portraits` | `portraits.gd` | Mii headshots, rendered once per look (`UI.avatar(resident, px)` makes the round one every list uses) |

**Scenes**: `scenes/game.tscn` (`game.gd`, class `Game`) is the main scene: top bar, the
3D island inside a SubViewport (`scenes/island/island_3d.tscn`), the bottom nav, the
Bubbles sidebar, and every screen (`show_screen()` shows one).

| Folder | What lives there |
|---|---|
| `scenes/` | `game.gd` / `game.tscn` (main screen), `protocol.gd` (`Protocol`: the shared vocabulary and bubble looks) |
| `scenes/island/` | the 3D island: `island_3d.tscn`, `island_layout.gd` (data + rules + paths), `island_terrain.gd`, `island_builder.gd`, `seasons.gd`, `day_night.gd`, `camera_rig.gd`; residents: `island_residents.gd` (follows Net), `island_resident.gd` (one walker), `speech_bubble.gd` (its speech bubble), `island_paths.gd` (working and delivering walk the paths), `island_life.gd` + `life_lines.gd` (ambient life: idle residents roam open ground, `IslandLayout.can_roam` / `roam_route` keep them off the water, buildings, pond and plants) |
| `scenes/buildings/` | one scene per building type, `building.gd`, `building_data.gd` (names, icons, colours), `building_picker.gd` (what's under the mouse) |
| `scenes/plants/` | `apple_tree.tscn`, `bush.tscn`, `swaying_plant.gd` |
| `scenes/build/` | `build_mode.gd`: the Build tool (land, lots, plants, steps, pond, season) |
| `scenes/home/` | the Archipelago (`home.gd`), `sea_view.gd`, `island_plate.gd`, `island_dropdown.gd`, `island_creator.gd` |
| `scenes/residents/` | Residents tab, resident cards, the resident creator, `resident_data.gd` (jobs, brains, gifts, vibes) |
| `scenes/mii/` | the 3D Mii (`mii.gd`), its parts (`mii_parts.gd`), the creator's preview stage |
| `scenes/news/`, `scenes/insights/` | the News tab (`news_stories.gd` writes the stories) and the Insights tab |
| `scenes/ui/` | sidebar, bubble cards, the answer popup, building panel, the talk popup (`talk_popup.gd` + `talk_room.gd`; the bubble types its text in via `visible_characters`, a click skips), settings, toasts, `ui.gd` (`UI.label`) |
| `ui/theme.tres`, `shaders/`, `assets/` | every colour and style, the GPU shaders, models / icons / fonts / Mii parts |

**`Net` signals and who listens** (every screen also redraws on `island_changed`):

| Signal | Listeners |
|---|---|
| `connected`, `disconnected` | `game.gd`, `home.gd`, `islands.gd`, `bubbles_sidebar.gd` |
| `snapshot_received` | `game.gd`, `residents_screen.gd`, `news_screen.gd`, `island_residents.gd`, `island_life.gd` |
| `agent_moved` | `island_residents.gd`, `island_life.gd` |
| `agent_state_changed`, `mood_changed`, `handoff_happened`, `task_updated` | `island_life.gd` (and `insights_screen.gd` for tasks) |
| `chat_said` | `island_residents.gd` |
| `bubble_resolved` | `answer_popup.gd` |
| `resident_updated`, `resident_removed`, `server_error` | `game.gd` |
| `thinking_received` | `building_panel.gd` |
| `talk_thinking`, `talk_replied`, `talk_failed` | `talk_popup.gd` |
| `event_received` | `news_screen.gd` |
| `island_changed` | `game.gd`, `islands.gd`, `bubbles_sidebar.gd`, `building_panel.gd`, `insights_screen.gd`, `island_residents.gd` |

**HTTP calls from the game** (all through `Net.request_json`):

| Endpoint | Caller |
|---|---|
| `GET /islands`, `GET /islands/{id}` | `islands.gd` |
| `POST /islands`, `GET /github/repos`, `GET /github/branches` | `home/island_creator.gd` |
| `DELETE /islands/{id}` | `home/island_dropdown.gd` |
| `POST /islands/{id}/tasks` | `ui/package_form.gd` |
| `GET /islands/{id}/tasks/{task}` | `ui/building_panel.gd` (a package's thinking history) |
| `GET /islands/{id}/events`, `GET .../insights?range=week`, `POST /replay/{task}` | `news/news_screen.gd` |
| `GET /islands/{id}/insights` | `insights/insights_screen.gd` |
| `PUT /islands/{id}/layout` | `build/build_mode.gd` |
| `GET/PUT /settings`, `GET /health`, `GET /brains/detail`, `GET /models` | `ui/settings_popup.gd` |
| `GET /brains/detail`, `GET /gifts`, `GET /brains/{brain}/models` | `residents/resident_creator.gd` |

## One task, end to end

1. **In.** `POST /islands/{id}/tasks` (`views.tasks_view` → `tasks.submit_task`) or the
   `create_task` command → `WorkflowEngine.submit` stores a `Task` (and its `Attachment`s)
   and emits `task: queued`.
2. **Queue.** `run_forever` hands it to the island's `Lane`; `run_task` cuts a worktree
   (`sandbox.Sandbox.create_worktree`, real mode only) and walks the `IslandGraph` from the mailbox.
   Tasks live in SQLite, so a restart loses nothing: `recover` re-queues them, and a
   half-done one resumes at the building it was in, its history rebuilt from its `Step` rows.
   A Lane runs up to `parallel_packages` (setting, default 3) packages at once, each in its
   own worktree. Every step books its resident (`_claim`): when the building's owner is busy
   with another package, a free neighbour stands in (the same job first, and residents
   without a building of their own before owners; anyone at a work building, only the same
   job at the Lab and the Courthouse), with a "busy with another package" `chat` and
   `Step.helped` set; with nobody free the package waits its turn.
3. **Each building** (`_run_task` → `_run_step`): handoff, walk, work. The building's
   `BuildingHandler` builds the instructions and hands an `AgentJob` to the runner for the
   resident's brain (`agents/`). Bubbles go through `events.Bubbles.ask` and wait for
   `answer_bubble`. Every change is an event: `events.<builder>` → `EventBus.emit` (stored
   in `Event`, broadcast to the island's sockets).
4. **Game.** `IslandConsumer` forwards each event; `net.gd` updates `Net.island` and emits
   a signal; `island_residents.gd` starts the walk (`island_paths.gd`), `island_resident.gd`
   animates it, the sidebar shows bubbles, News turns events into stories.
5. **Out.** The outcome picks the next road; with no road left the task is `done` or
   `failed`, the Post Office has opened (and maybe merged) the PR, and the worktree is removed.

### One step in detail

1. The engine hands the package over (`handoff`), walks the resident to the
   building, and sets them `working`.
2. It builds an `AgentJob`: the resident, the building's instructions, the
   package history, the task's worktree, `read_only` for plan/review/PR steps,
   plus `ask()` (show a bubble, wait for the answer) and `report()` (send an
   `activity` event). The history in the prompt is trimmed (`notes_for_prompt`):
   a one-line recap of every step, then in full only the plan, the latest
   failure report and the step just before. Step rows and events keep it all.
3. The building handler runs. Most hand the job to the resident's brain:
   - **AcpRunner**: each Mii is one agent per task. On a resident's first visit:
     start the agent in the worktree, `initialize`, `session/new`
     (a missing login becomes a 💤 bubble), switch on the agent's edit or
     read-only mode, pick the resident's model if offered, send personality +
     instructions + history as one prompt. The process and session are then
     kept (`AcpRunner.live`, by task + resident): their next visit (a retry, a
     help visit, a Workshop pre-check retry) prompts the same session with a
     short "same session as before" header and no diary, so the agent skips
     start-up and re-reading the repo. A changed worktree, model or gifts, or a
     dead process, starts a fresh one. While it runs: tool calls become
     `activity` events; permission requests are decided by the resident's house
     keys (`yes` = allow, `no` = reject, `ask` = a ✋ bubble whose buttons are
     the agent's own options; no answer = reject); file reads/writes are served only inside the worktree.
     Claude Code's subagents (its Agent tool, ACP kind `think`) count as `read`; the
     fresh-session prompt carries the brain's `tip` (`BRAINS`), which asks Claude Code to
     run independent parts as parallel subagents. Their tool calls ask like any other, so
     several ✋ bubbles from one resident can be open at once (`MiiClient.pending`).
     ACP has no "ask the user" request, so the prompt tells the agent it may
     end a turn with `QUESTION: ...`: the runner shows a ❓ bubble and prompts
     the same session again with your answer (up to 3 per step). The final
     turn's `VERDICT: PASS/FAIL` line decides. The process is closed right away
     when a step times out, is cancelled or the agent errors, and otherwise when
     the task ends (`run_task` calls `agents.end_task`, which runs the runner's
     `end_task`). One runner serves the whole task, even if settings change.
   - **OpenRouterRunner**: the same contract over OpenRouter's chat API with
     built-in tools.
   Some handlers do more: the **Workshop** commits the worktree after the
   coder and, with `TEST_COMMAND` set, runs the tests itself before the
   handoff: a failure goes back to the coder in the same step with the output
   on top (`PRETEST_RETRIES`, 2, at most; the last attempt is left to the Lab),
   the **Lab** runs `TEST_COMMAND` itself and uses the exit code, the
   **Courthouse** prepends `git diff --stat` and, once the reviewer passes it on
   an island with a GitHub repo, asks you with a ✅ `ready` bubble ("Approve &
   merge" / "Send back"; skipped when the `merge_pr` house rule is off, which
   approves it). Send back fails the step with your note. The **Post Office**
   turns the reply into a PR title/body, opens the PR, and merges it with
   `gh pr merge` if you approved (squash, else merge, else rebase). Unanswered:
   the PR stays open.
4. The outcome picks the next road, except that red tests at the Lab go straight
   back to the Workshop the package last came through, with the test output on top
   of the coder's instructions. A task stops (`failed`, with a "Gave up at the
   <building>: <last failure>" `activity`) before a fourth visit to one building
   (`MAX_VISITS`) or a 21st step (`MAX_STEPS`). A `Step` row is saved for insights (`Step.helped` marks a help visit). When
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
`anthropic/claude-sonnet-4.5`. The resident creator's picker lists them from
`GET /brains/{brain}/models` (`agents/acp.py` `list_brain_models`: a short session in a
scratch worktree, never in the island's repo); a short name is matched by `pick_model`.

## The judge, help visits, chat and the diary

`judge.py` answers the small questions the engine used to settle with hard-coded rules.
`RuleJudge` *is* those rules (the default, offline, deterministic). Two model judges share
`AskingJudge`, which builds each question and parses the answer; they only differ in `_ask`
(state + questions in, `{id: {"choice": key}}` / `{id: {"noul": 0..1}}` out, or `None`):

- `JevJudge` asks TypeSafe's Jev, a fast typed-decision model (one HTTPS call per decision,
  ~100-500 ms). Needs `typesafe_api_key` (env `TYPESAFE_API_KEY`).
- `GeminiJudge` asks Gemini (`judge_model`, default `gemini-2.5-flash`) with structured
  output: a JSON schema with one property per question (an enum over the choices, or a
  number), temperature 0, a 6-second timeout. Needs `gemini_api_key` (the same key the
  Gemini CLI brain uses, so it is not private).

The `judge` setting picks one: `off` = rules; `jev` / `gemini` = that judge when its key is
set; `auto` (default) = Jev, else Gemini, else rules. A model judge falls back to the rules
on any error and warns once. Mock mode included: the calls cost a fraction of a cent and
are the demo. `GET /health` says which judge is active. The judge never decides permissions.

| decision | when | with a model judge | with the rules |
|---|---|---|---|
| mood | after every step | reads the resident, building, outcome and reply | happy / frustrated, tired on timeout |
| verdict | the reply had no `VERDICT` line | "did the agent finish?" < 0.4 → fail | pass (as before) |
| flaky | the Lab's `TEST_COMMAND` failed | "flaky or environmental?" ≥ 0.7 → run once more (`Step.flaky`) | never re-run |
| tip | `GET .../insights` | picks among the tips whose rule applies | the first rule that applies |
| needs help | a package is back at a work building (never the Lab or Courthouse) and not stuck | "would a different teammate taking this turn unblock it?" ≥ 0.6 | from the third visit (the owner retries once first) |
| helper | help is needed | picks among the free neighbours (or nobody) | same job as the building, else the best friend |

**Help visits.** Two ways a neighbour takes a turn. When a package keeps coming back to a
work building (Mailbox, Workshop, Post Office) after a failed check, the owner gets one
retry of their own, then the judge may hand the next visit to a free neighbour (idle or
asleep). A package that is **stuck** (sent back with the same failure, compared without
case, numbers or extra spaces, as the one this building had to fix last time, including
the Workshop's own pre-check) gets the neighbour without asking the judge, so a red test
that survived the coder's in-step retries is helped on the second visit: same building, same worktree, the check's report on top of the instructions, their
outcome picks the road and the Workshop commits their work as usual. The Lab and the
Courthouse are never taken over: their tests and review are the owner's call. The other
way is explicit: a resident writes `HELP: <what I'm stuck on>` in a failing reply and a
neighbour redoes that building right away (at the Lab or Courthouse only a neighbour with
the same job, so a coder never reviews their own work). Either way both get a `chat` event (the
request, then the first words of the helper's report), the handoff grows the friendship
meter, and the helper carries the package on. At most `MAX_HELPS` (2) per task; a visit
counts toward the 20-step cap. **Notes:** `SAY TO <Name>: <message>` becomes a `chat` event and shows up
under "Messages from your neighbours" in that resident's next prompt on the task.
**Diary:** `REMEMBER: <line>` lines go to `Resident.notes` (oldest lines dropped past
~1500 characters) and open every future prompt of that resident. Every brain gets the same
three lines explained at the end of its prompt (`agents.build_prompt`); `parse_verdict`
strips them from the summary.

**Spectator feed.** `hooks.SpectatorHook` is registered on the `EventBus` and, when the
`spectator_url` setting is set, POSTs the `task`, `handoff`, `bubble`, `bubble_resolved`,
`chat`, `weather` and `mood` events (stored shape, plus `island`) to that URL with
`Authorization: Bearer <spectator_secret>`. Outbound only: the hook just enqueues (200
events at most, extra ones are dropped) and one background task posts them, 3 s timeout,
one warning while the feed is down. A dead feed never slows the engine. The page itself
lives in `spectator/`; this is not a game message, so protocol.md doesn't list it.

## Safety

- Agents only run inside a git worktree of the island's own repository (`sandbox.py`).
- Each agent's own safety mode is switched on (Claude Code `acceptEdits`,
  Codex `workspace-write` / `read-only`). On read-only steps (plan, review, PR
  text) edit permissions are refused and file writes are rejected by our side
  of the ACP connection. Claude Code's `plan` mode isn't used: refusing its
  "Approve plan" ends the turn as cancelled and loses the plan.
- Attached files are written only inside the task's worktree (`.mii-attachments/`,
  plain names, path-checked, git-ignored), never anywhere else on disk.
- Permission requests become bubbles; file requests are path-checked. Shell
  commands are gated by a bubble, not path-restricted.
- Every step has a timeout, and a package can visit at most 20 buildings (3 visits to one building).
- The OpenRouter, TypeSafe and spectator keys are never passed to agent processes
  (`prefs.PRIVATE_ENV`). API keys live in the OS keychain (or an owner-only file where
  there is none), never in the database.
- Login (optional, `auth.py`): islands without an `owner` are **public**: anyone who can
  reach the server sees them and can send packages there. Only owned islands are
  private (owner + `members`). Only the owner may abandon an island while login is on.
- The public demo (`PUBLIC_DEMO=1`) locks `PUT /settings`, refuses `folder` /
  `existing_repo` on `POST /islands`, and refuses abandoning public islands.

## Tables

| Table | What it holds |
|---|---|
| `Island` | id, name, GitHub repo, `repo_path` (its local git repository), branch, town plan, house rules, `map` (land tiles, pond, plants, season), `owner` (Auth0 user id, null = public) and `members` |
| `Resident` | name, job, 4 personality sliders, `brain`, optional `model`, catchphrase, color, house `keys`, `gifts`, live state / building / mood / `notebook` / `activity`, `appearance`, `notes` (the diary: `REMEMBER:` lines from their own replies) |
| `Building` | type, name, lot (`x`, `y`, `facing`), resident |
| `Road` | from, to, condition |
| `Task` | title, status, where the package is, who carries it, `issue_number`, `branch`, `pr_url`, `merged_at`, `created_at` / `finished_at` |
| `Attachment` | a file sent with a task: name (made safe) and bytes; written to `.mii-attachments/` in the task's worktree when it starts |
| `Step` | one building visit: task, building, resident, brain, outcome, timed out?, bubbles raised, `flaky` (tests passed on the re-run), `helped` (a help visit: who asked), start/finish time |
| `Event` | every event sent to the game (history + replay) |
| `Friendship` | the friendship meter: one row per pair of residents (`a` < `b`), `handoffs`, `score` |
| `Setting` | user settings saved from the game (never secrets: those live in the OS keychain) |
| `metrics` | TimescaleDB only, no Django model (a hypertable has no primary key): one row per finished step (`kind=step`) and task (`kind=task`); the continuous aggregate `metrics_daily` buckets it by UTC day and feeds Insights' "packages per day". Made by migration `0015_metrics`, which does nothing on SQLite or plain Postgres |

The database is SQLite (`DATABASE_PATH`) unless `MII_DATABASE_URL` is a `postgres://` DSN
(Tiger Cloud or a local TimescaleDB). `GET /health` says `sqlite`, `postgres` or `timescale`.
Days in Insights are the server's local days and Timescale's buckets are UTC: hosted
setups run with `TZ=UTC` so both agree.

After changing a model: `uv run python manage.py makemigrations mii`, then
`migrate` (the server also migrates at startup).

## Conventions and checks

- `make check` before you call a change done: ruff (lint + format check), the backend
  tests, gdlint + gdformat check, and the Godot script check. `make fmt` formats both halves.
  CI (`.github/workflows/ci.yml`) runs the same on every push and pull request.
- Python: ruff at 120 columns (`backend/pyproject.toml`). GDScript: gdformat at 120,
  gdlint (`.gdlintrc`), static types on new code, `class_name` on scripts other scripts use.
- Comments say what and why in a line or two; Godot concepts get a short explanation the
  first time a file uses them. No history ("we used to..."): that is what git is for.
- Open work is `TODO(mvp)` / `TODO(stretch)` in the code and listed in [TODO.md](../TODO.md).
- Migrations are Django's and checked in; the server migrates at startup.

## Two people, one island

Today a second Godot client is a full co-pilot: start the backend with
`uvicorn ... --host 0.0.0.0`, run the game elsewhere on the LAN with
`MII_SERVER=<host-ip>:8000`, and both players get the same broadcasts, answer the same
bubbles and send packages to the same island. Everything still runs on the host: the
agents, the host's CLI logins and keychain keys, `gh`, the repositories.

With login on (`AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_CLIENT_ID`), islands get an
**owner**: whoever founded it while logged in (the game's device login, proxied through
`/auth/*`). The owner adds **members** by user id (`update_island` with `members`); owner
and members see the island and play it as co-pilots, everyone else gets 403 (HTTP) or
close code 4403 (socket). Islands without an owner, like the seed island, stay public.
Agents still run as the host: per-user brains and house keys stay out of scope.

## Not in scope (on purpose)

LangChain or other direct-API runners beyond OpenRouter, the ML team router,
multi-account islands (see above). (Islands run in parallel, and each island's `Lane`
runs up to `parallel_packages` packages at once, see "One task, end to end". Island
News is written by the game from stored events; the backend only serves them, see
"Events history".)
