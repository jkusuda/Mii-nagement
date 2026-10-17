# Roadmap

Code TODOs use `TODO(mvp)` / `TODO(stretch)`. To list them:

```bash
grep -rn "TODO(mvp)" backend/mii game/net.gd game/scenes
grep -rn "TODO(stretch)" backend/mii game/net.gd game/scenes
```

## ✅ Skeleton (boilerplate)

- [x] Repo docs, protocol, env template
- [x] Backend: models, personality prompts, workflow engine, mock agents, event bus, seed + simulator
- [x] Backend ported to Django + Channels (same protocol, same endpoints)
- [x] Godot: WebSocket client, island, buildings, residents, bubbles, connection indicator
- [x] Runs end to end in mock mode

## 🎯 MVP (the demo)

Backend design: [docs/architecture.md](docs/architecture.md).

- [x] **ACP runner** (`agents/acp.py`): Claude Code, Codex and Gemini CLI on
      your subscriptions. Permission requests → ✋ bubbles with the agent's own
      options, progress → `activity` events, file requests path-checked, missing
      login → 💤 bubble. Tested against a fake ACP agent (`tests/fake_acp_agent.py`).
- [x] **OpenRouter brain** (`agents/openrouter.py`): any OpenRouter model as a
      resident, no CLI needed (`OPENROUTER_API_KEY`).
- [ ] **Real-mode run on stage**: a hands-on task through each live CLI. Claude
      Code: verified end to end (`POST /islands/{id}/tasks` → plan, code + commit,
      tests, review, PR text, all five residents on `claude-code`). Codex and Gemini
      haven't been watched yet.
- [x] **Tasks over HTTP**: `GET /islands/{id}/tasks` and `GET /islands/{id}/tasks/{task_id}`
      (steps with what each resident reported, plus the events while it ran).
- [x] **GitHub issue in → real PR out** (`github.py`, via the `gh` CLI): the
      workshop commits, the post office pushes and opens the PR.
- [x] **Lab runs the real tests** (`TEST_COMMAND`) and passes/fails on the exit code.
- [x] **Insights** (`insights.py`): per resident, per brain, per building stats.
- [x] **Insights screen** (Godot): stat cards, packages per day, where packages
      wait, comfort report. Shows dashes until the backend sends the fields.
- [x] **Insights backend, rest of the payload** ([docs/architecture.md](docs/architecture.md)
      "Insights payload"): ranges, per-day counts, answer time, keys lent, test pass rate,
      flaky tests, PRs merged (via `gh`), previous-range numbers, tip.
- [x] **Replay** (`replay.py`): re-play a recorded good run if anything fails on stage.
- [x] **Game: live island**: residents on the 3D island walking the roads, states
      (working / idle / sleeping), bubble icons, station counts, the Bubbles
      sidebar from real bubbles with option buttons / typed answers.
- [ ] **Game: island extras**: packages carried while walking, brain badges,
      activity thought bubbles, click a resident on the island.
- [x] **Residents tab + resident creator UI** (Godot): cards, filters, creator with
      live 3D Mii preview, brain/model, sliders, gifts, house keys.
- [x] **Resident creator, backend half**: `create_resident` / `update_resident`,
      `keys` / `gifts` columns (new residents are placeholders for now).
- [x] **ACP honours house keys**: `yes` / `ask` / `no` per tool kind in `AcpRunner`.
- [x] **Gifts as MCP servers** (the `GIFTS` table next to `BRAINS`, `GET /gifts`), and giving
      residents a building (`buildings` on `create_resident` / `update_resident`).
- [x] **Game: gifts and "works at"** in the creator: gifts `GET /gifts` says won't run are greyed
      out (with why), and "Works at" chips send `buildings`.
- [x] **Game: `openrouter` brain** in the creator's brain list, with a model picker (GET /models).
- [x] **Game: send a package** (POST /islands/{id}/tasks): title + details, or a GitHub issue number.
- [x] **Game: click a building**: the camera glides to it and a panel opens (Mailbox: open issues +
      send a new one; other buildings: packages at that stage, residents there and their activity).
- [x] **Island after founding**: `PATCH /islands/{id}` / `update_island` (rename, link a repo, branch,
      rules) + the `island` event; `GET /islands/{id}/issues` to start a task from a GitHub issue;
      `GET /meta` so the game stops hard-coding backend lists; `weather` in the snapshot and as an
      event; `activity`, `appearance` and bubble `asked_at` in the snapshot; `request_id` on errors.
- [ ] **Game: use them**: island settings (rename / link repo), issue picker in the Mailbox, read
      `/meta` instead of `ResidentData` constants, show the backend `weather`, send `appearance`.
- [ ] **Build mode**: place the 5 building types and draw pass/fail roads.
      Backend done: `set_layout` / `PUT /islands/{id}/layout` + the `layout` event (protocol.md).
      The game still needs the editing UI.

## 🌈 Stretch

- [x] Island News tab (Godot): stories from live events, headline, unread badge, daily digest
- [x] `GET /islands/{id}/events` so News shows history from before the game started
      ([docs/architecture.md](docs/architecture.md), "Events history"); `prs_merged`
      and `keys_lent` in the insights payload for News' "This week"
- [x] Settings popup (GET/PUT /settings, /health, /brains/detail) and Replay on shipped News stories
- [x] Archipelago home screen + island switcher (GET /islands, per-island events)
- [x] Island creator: name, repo (via gh), branch, town plan, residents, house rules;
      every island gets its own git repository (`islands.py`)
- [ ] Island looks (seasons), Caretaker
- [x] ACP runner honours island `branch` (worktrees + PRs) and house `rules` (`shell`, `install_packages`)
- [ ] "Celebrating" island status (the events endpoint exists now; game side to do)
- [x] Islands work in parallel (one package at a time per island)
- [x] Approve & merge: the Courthouse asks (`merge_pr` rule), the Post Office merges via `gh`
- [x] ACP residents ask questions (`QUESTION:` line -> ❓ bubble -> answer in the same session)
- [ ] Several packages at once on one island
- [x] The judge (`judge.py`): moods, missing verdicts, flaky Lab re-runs, Insights tip, who helps.
      Rules by default; TypeSafe Jev with a `typesafe_api_key` (mock mode too)
- [x] Residents help each other: `HELP:` → a free neighbour walks over and does the step; `SAY TO <Name>:`
      notes; `REMEMBER:` diary; `chat` events. Game: speech lines, mood words on the island
- [x] Island life (game only): idle residents stroll, visit friends and chat, gaze at the pond, mutter, wave,
      hop on `proud`, slump on `frustrated`; vibes (sleepy, bored, lonely, excited, relaxed) read off the snapshot
- [ ] A free backend "social ticker" so every client and News see the same ambient chats
- [ ] Two accounts on one island (see architecture.md "Two people, one island"); LAN co-pilot works today
- [x] Gifts for animation and video work: `godot` (godot-mcp-runtime, needs `GODOT_PATH`), `video` (ffmpeg-mcp);
      project skills `.claude/skills/godot-animation`, `demo-video`
- [x] Friendship meter between residents who hand off to each other (`Friendship` table,
      `friendships` in the snapshot, `friendship` event; resident cards show the best friend)
- [x] Cancel a task (`cancel_task` / `POST .../cancel`), create one over the socket
      (`create_task`), residents and islands can be deleted. Game: "Take back" on package
      cards, "Move away" in the creator, ✕ on island switcher rows (all ask first)
- [ ] Mii faces (with mood expressions), hats, hair, portraits ([docs/mii-customization.md](docs/mii-customization.md))
- [ ] Dreams, parties, apartment interiors
- [ ] Blueprints (save and share island layouts as workflow templates)
- [ ] Mock-mode web demo (Godot web export + hosted mock backend)
