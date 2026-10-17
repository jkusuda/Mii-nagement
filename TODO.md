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
- [ ] **Real-mode run on stage**: a hands-on task through each live CLI. The Claude
      Code adapter handshake (modes + model list) is verified; a full prompt run,
      Codex, and Gemini haven't been watched yet.
- [x] **GitHub issue in → real PR out** (`github.py`, via the `gh` CLI): the
      workshop commits, the post office pushes and opens the PR.
- [x] **Lab runs the real tests** (`TEST_COMMAND`) and passes/fails on the exit code.
- [x] **Insights** (`insights.py`): per resident, per brain, per building stats.
- [x] **Insights screen** (Godot): stat cards, packages per day, where packages
      wait, comfort report. Shows dashes until the backend sends the fields.
- [x] **Insights backend, rest of the payload** ([docs/architecture.md](docs/architecture.md)
      "Insights payload"): ranges, per-day counts, answer time, test pass rate, previous-range numbers, tip.
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
- [ ] **Gifts as MCP servers** (a `GIFTS` table next to `BRAINS`), and giving new
      residents a building ([docs/mii-customization.md](docs/mii-customization.md)).
- [x] **Game: `openrouter` brain** in the creator's brain list, with a model picker (GET /models).
- [x] **Game: send a package** (POST /islands/{id}/tasks): title + details, or a GitHub issue number.
- [x] **Game: click a building**: the camera glides to it and a panel opens (Mailbox: open issues +
      send a new one; other buildings: packages at that stage, residents there and their activity).
- [ ] **Build mode**: place the 5 building types and draw pass/fail roads.

## 🌈 Stretch

- [x] Island News tab (Godot): stories from live events, headline, unread badge, daily digest
- [x] `GET /islands/{id}/events` so News shows history from before the game started
- [x] Settings popup (GET/PUT /settings, /health, /brains/detail) and Replay on shipped News stories
- [x] `keys_lent` in the insights payload for News' "This week"
- [ ] `prs_merged` (watch PRs on GitHub) and `flaky_tests` (needs re-runs in the Lab)
- [x] Archipelago home screen + island switcher (GET /islands, per-island events)
- [x] Island creator: name, repo (via gh), branch, town plan, residents, house rules;
      every island gets its own git repository (`islands.py`)
- [ ] Island looks (seasons), Caretaker
- [ ] ACP runner honours island `branch` and house `rules`
- [ ] "Celebrating" island status (needs recent shipped tasks: the events endpoint)
- [ ] Run several tasks at once (one per free resident)
- [ ] Friendship meter between residents who hand off to each other
- [ ] Mii faces (with mood expressions), hats, hair, portraits ([docs/mii-customization.md](docs/mii-customization.md))
- [ ] Dreams, parties, apartment interiors
- [ ] Blueprints (save and share island layouts as workflow templates)
- [ ] Mock-mode web demo (Godot web export + hosted mock backend)
