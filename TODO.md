# Roadmap

Code TODOs use `TODO(mvp)` / `TODO(stretch)`. To list them:

```bash
grep -rn "TODO(mvp)" backend/mii game/net.gd game/scenes
grep -rn "TODO(stretch)" backend/mii game/net.gd game/scenes
```

## ✅ Skeleton (boilerplate)

- [x] Repo docs, protocol, env template
- [x] Backend: models, personality prompts, workflow engine, mock agents, event bus, seed + simulator
- [x] Godot: WebSocket client, island, buildings, residents, bubbles, connection indicator
- [x] Runs end to end in mock mode

## 🎯 MVP (the demo)

Backend design: [docs/architecture.md](docs/architecture.md).

- [ ] **ACP runner** (`agents/acp.py`): drive Claude Code first, then Codex and
      Gemini CLI. Permission requests → ✋ bubbles with options, progress →
      `activity` events, file requests path-checked, usage limits → 💤 bubbles.
      Test it against a tiny fake ACP agent (`tests/test_acp.py`).
- [ ] **GitHub issue in → real PR out** (`github.py`, via the `gh` CLI): the
      workshop commits, the post office pushes and opens the PR.
- [ ] **Lab runs the real tests** and passes/fails on the exit code.
- [ ] **Insights** (`insights.py`): per resident, per brain, per building stats,
      shown on the Insights screen.
- [ ] **Replay** (`replay.py`): re-play a recorded good run if anything fails on stage.
- [x] **Game: live island**: residents on the 3D island walking the roads, states
      (working / idle / sleeping), bubble icons, station counts, the Bubbles
      sidebar from real bubbles with option buttons / typed answers.
- [ ] **Game: island extras**: packages carried while walking, brain badges,
      activity thought bubbles, click a resident on the island.
- [x] **Residents tab + resident creator UI** (Godot): cards, filters, creator with
      live 3D Mii preview, brain/model, sliders, gifts, house keys.
- [x] **Resident creator, backend half**: `create_resident` / `update_resident`,
      `keys` / `gifts` columns (new residents are placeholders for now).
- [ ] **ACP honours house keys and gifts**, and new residents can be given a building
      ([docs/mii-customization.md](docs/mii-customization.md)).
- [ ] **Build mode**: place the 5 building types and draw pass/fail roads.

## 🌈 Stretch

- [ ] Island News (daily recap of what happened on the island)
- [ ] Archipelago: multiple islands / repos
- [ ] Run several tasks at once (one per free resident)
- [ ] Friendship meter between residents who hand off to each other
- [ ] Mii faces (with mood expressions), hats, hair, portraits ([docs/mii-customization.md](docs/mii-customization.md))
- [ ] Dreams, parties, apartment interiors
- [ ] Blueprints (save and share island layouts as workflow templates)
- [ ] Mock-mode web demo (Godot web export + hosted mock backend)
