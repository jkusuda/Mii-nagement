---
name: godot-animation
description: How motion, moods, speech bubbles and ambient life work in the Godot game (game/), and how to add or change an animation on the island. Use for any request about animating the Miis, residents reacting, emotes, speech lines, island life, or the day/night and season looks.
---

# Animating the island (Godot 4, GDScript)

The game is **display only**: residents' real state, moods and moves come from the backend
over the WebSocket (docs/protocol.md). Animation work never changes workflow or agent logic.
The owner is new to Godot: explain each Godot concept the first time it appears in a file.

## Where motion lives

| File | What it animates |
|---|---|
| `game/scenes/mii/mii.gd` | The Mii body. `ANIMATIONS` names the three clips in `assets/models/mii.glb` (idle, walk, run); `play(what)` blends 0.2 s. Everything else is procedural (Tweens on `mii.position / rotation / scale`). |
| `game/scenes/island/island_resident.gd` | One resident on the island: `walk(points, hurry)` (a chained Tween along path points; they stroll, and only run on a long `hurry` route to work; the clip goes back to idle on arrival; `_footsteps` plays `Audio.footsteps` in 3D while they walk), `settle()` per state (working = a hop loop `_start_working`, sleeping = "z Z"), `say(text, seconds)` (a `SpeechBubble`, `speech_bubble.gd`: white pill with a tail), `emote(kind)` (hop, proud, frustrated, tired, confused, shift, wave), `look_toward(point)`, the mood word under the name tag, `stop_life()`. Working and sleeping residents `enter(points, state)` their building (walk to the door, shrink, hide Mii + tags; `inside`; a sleeper's "z Z" floats over the door); the next `walk()` / `place()` pops them back out. |
| `game/scenes/island/island_residents.gd` | *Where* residents go: snapshot placement, `move` events → `_on_moved` → `IslandPaths.route`, `_line_up` slots at each building, `free_spot()` (nearest clear bit of path, so nobody stands on anyone), `chat` events → `say()`. Owns the `Life` child. |
| `game/scenes/island/island_life.gd` | Ambient Tomodachi-style life between packages. Every idle resident runs their own loop (`_live`): pick an action (`_pick_action`: stroll to a random path point, visit + chat, pond/tree gazing, rest at their building, mutter), linger (`_linger`), repeat until the server needs them or they stop being idle. A visit holds the friend (`_held`; their loop waits). Also sleep shifts, waves, and emotes on `mood` / `handoff` / `task done`. Weighted by personality sliders and `DayNight.night`. |
| `game/scenes/island/life_lines.gd` | The canned lines (by vibe, job, weather, season) and short chat scripts. |
| `game/scenes/island/island_paths.gd` | `route(layout, from, to)` and `nearest(layout, p)`: points along the sand paths. |
| `game/scenes/plants/apple_tree.tscn`, `bush.tscn` + `swaying_plant.gd` (scenes/plants/) | Plants sway. |
| `game/scenes/island/day_night.gd` | `DayNight.night` (0-1) moves the sun and recolours light and water. |
| `game/scenes/island/seasons.gd`, `shaders/season_overlay.gdshader` | Per-season looks: blossoms, autumn colours, snow, lit windows at night. |
| `game/scenes/residents/resident_data.gd` | `vibe(resident)`: the mood word shown (backend moods + sleepy / excited / lonely / bored / relaxed read off the snapshot), `VIBES` colours. |

## The one rule: the server always wins

`island_life.gd` only borrows residents whose server state is `idle` or `sleeping` and marks
them `life_busy`. Any `agent_state` or `move` event for that resident makes
`island_residents.gd` call `walker.stop_life()`, which kills the ambient tweens, clears
`life_busy`, and emits `arrived` so the coroutine in `island_life.gd` unwinds. `_line_up`
skips `life_busy` walkers that are still idle, so it never drags a stroller home mid-walk.
Keep that contract when adding actions: check `_still(walker)` after every `await`.

## Recipes

**A new emote.** Add a branch in `IslandResident.emote()`. Use `mii.position:y` for hops
(unless `_work_tween` is running: then pulse `mii.scale`), `mii.scale:y` for slumps,
`mii.rotation:z` for sways, and tween `self._facing` for head turns (`_process` lerps the Mii
toward `_facing`; never tween `mii.rotation:y` directly). Always kill `_emote_tween` first and
restore `Vector3.ONE * MII_SCALE`.

**React to a new event.** Add the signal in `game/net.gd` (`_handle_message` → `signal`),
connect it in `island_life.gd` `_ready`, call `_emote(id, kind)`. Unknown event types must be
ignored, not errors (protocol rule 1).

**A new ambient action.** Add a weight in `_pick_action`, a `match` arm in `_live`, and a
coroutine like `_pond`: `_go(walker, point)` walks along the paths to the nearest free spot and returns false when
the server interrupted. Don't walk home at the end: the loop lingers and picks the next
action (the `rest` action is the walk home). Lines go in `life_lines.gd`.

**Speech.** `walker.say(text, seconds)` on the speaker; `look_toward` the listener. The bubble
(names in orange) wraps at 460 px; keep lines short. Fonts: `assets/font/DreamLife` has no emoji glyphs, so use
words, not emoji.

**Faces, hair, hats.** See docs/mii-customization.md (`Resident.appearance` is stored and
echoed by the backend; the game owns its meaning).

## Checking your work

```bash
make check                          # lint + backend tests + Godot script check (fails on script errors)
make fmt                            # gdformat + ruff format
make dev                            # backend + game on mock brains: found an island, send packages
# Godot not on PATH or in /Applications? Add GODOT=/path/to/Godot to any make command.
```

Keep `*.gd.uid` files next to new scripts (Godot 4.4+ makes them on import:
`--headless --path game --import`). Keep `.tscn` files simple; build nodes in code when clearer.
