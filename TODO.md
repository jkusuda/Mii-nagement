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
- [ ] **Game: live island**: residents walking with packages on the 3D island
      (the walk function is the owner's Godot exercise), brain badges, activity thought bubbles, bubbles with option
      buttons, the Bubbles sidebar filled from real bubbles.
- [ ] **Resident creator**: name, job, personality sliders, brain, color, catchphrase.
- [ ] **Build mode**: place the 5 building types and draw pass/fail roads.

## 🌈 Stretch

- [ ] Island News (daily recap of what happened on the island)
- [ ] Archipelago: multiple islands / repos
- [ ] Run several tasks at once (one per free resident)
- [ ] Friendship meter between residents who hand off to each other
- [ ] Dreams, parties, apartment interiors
- [ ] Blueprints (save and share island layouts as workflow templates)
- [ ] Mock-mode web demo (Godot web export + hosted mock backend)
