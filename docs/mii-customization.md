# Mii customization plan

Status: **plan**. Only the body colour is implemented today. This doc covers the
Mii model we have, how the creator's fields reach the ACP agents, and how to add
faces, hair, hats, and portraits later.

## The model we have

`game/assets/models/mii.glb` comes from `blank_mii.glb` (Blender export).

| Thing | What's in it |
|---|---|
| Mesh | one skinned mesh, `char1`, about 52k vertices. Head, body, and limbs are one piece. |
| Size | 1.7 units tall, feet at y = 0, facing +Z (toward the camera in our scenes) |
| Skeleton | 24 bones: `spine` … `spine.005` (the head), `headfront` (front of the face), `head_end` (top of the head), arms, hands, legs, feet |
| Animations | `Armature\|clip0\|baselayer` (idle), `Walking`, `Running` |
| Material | the original was a flat gray 4096² texture used as color *and* emission (16 MB, and it would make the Mii glow). The game copy drops the texture and uses a plain white material, which `mii.gd` tints. |
| Not in it | no face, no hair, no blend shapes (shape keys), no separate head mesh |

`scenes/mii/mii.tscn` wraps it: `Mii.color` tints the body, `Mii.play("idle" | "walk" | "run")`
switches animation. Use the same scene for residents walking on the island.

To re-import a new version: export from Blender as `.glb` with **no image
textures** (or run the same strip step again), drop it on `assets/models/mii.glb`.

## How the creator's fields reach the agent (ACP)

The creator was reworked from the mockup to match what the backend and ACP can
actually do. The look stayed the same.

| Creator field | Stored on `Resident` | Used by |
|---|---|---|
| Name, job, catchphrase, 4 sliders | already exist | `personality.build_system_prompt()`, the first ACP prompt |
| Colour | `color` (exists) | the game only |
| Brain | `brain` (exists): `mock`, `claude-code`, `codex`, `gemini` | which `BRAINS` command `AcpRunner` starts. The creator greys out brains `GET /brains` says aren't installed. |
| Model | `model` (exists, `null` = default) | ACP session config, where the agent supports picking a model |
| **House keys** | new `keys` JSON: `{"read": "yes", "edit": "ask", "execute": "ask", "fetch": "no"}` | `AcpRunner`'s `session/request_permission` handler. Look up the tool call's `kind`: **yes** answers with the agent's "allow once" option by itself, **ask** shows a ✋ bubble (today's behaviour), **no** answers "reject". |
| **Gifts** | new `gifts` JSON list, e.g. `["browser", "docs"]` | `mcpServers` in ACP `session/new`. Add a `GIFTS` table next to `BRAINS` (gift id → MCP server command), so the game only ever sends ids. |

What changed from the mockup, and why:
- **Jobs** are planner / coder / reviewer, the three in `personality.JOBS`. The lab
  runs tests itself, so there's no "tester" job.
- **Face row → Catchphrase.** The face picker comes back with the steps below.
- **Gifts** are MCP servers. ACP agents bring their own read/edit/terminal tools, so
  "test kit / magnifier / terminal" became extras we can actually hand over.
- **House keys** follow ACP's tool kinds (`read`, `edit`, `execute`, `fetch`, …)
  and have three answers: Yes / Ask me / No. "Install packages" is part of Run
  commands. "Merge to main" was removed: the post office opens a PR, and a
  person merges it.
- Mapping ACP tool kinds to keys lives in `ResidentData.HOUSE_KEYS` (game, display only)
  and must be repeated in `AcpRunner` (backend, enforcement). The backend is the one
  that decides. The game only shows the setting.

### Creating and editing residents (done)

The creator sends `create_resident` / `update_resident` (docs/protocol.md) through
`Net.send_resident()`. `backend/mii/residents.py` checks the fields, saves the
row (`keys` and `gifts` are JSON columns), picks the id and home building, and
broadcasts a `resident` event. Offline, the game adds the resident locally so
the screens still work.

New residents are **placeholders**: they're saved and shown, but the workflow
still runs the residents assigned to buildings. Still to do:
- `AcpRunner` honours `keys` (auto-answer permission requests) and `gifts` (`mcpServers`).
- A way to give a building to a new resident (build mode, or the creator).
- `appearance` (below), once customization starts.

## Customization, step by step

The backend never looks inside `appearance`: it stores the JSON and sends it
back. All of it is cosmetic, so it's fine for the game to own the meaning.

```json
"appearance": {
  "color": "#9B87F5",
  "eyes": 2, "mouth": 0, "brows": 1, "blush": true,
  "hair": "bob", "hair_color": "#4A3426",
  "hat": "hard_hat",
  "height": 0.5, "build": 0.5
}
```

### 1. Faces (the biggest win)

Attach face parts to the `headfront` bone with a **BoneAttachment3D**, a node that
follows one bone of a skeleton, so the face moves with the idle/walk animations.

- Under it, one small `Sprite3D` per part: left eye, right eye, brows, mouth, blush.
  Small separate parts sit flat on a round head much better than one big face
  picture would.
- Parts come from one PNG atlas per kind (`assets/mii/eyes.png` with 8 styles in a
  row, `mouths.png` with 6, …). `Sprite3D.hframes` + `frame` picks the style.
- **Moods for free:** the protocol already sends `mood`. Map it to an eye/mouth
  override (`confused` → wobbly mouth, `tired` → half-closed eyes, `proud` → big smile).
- **Blinking:** a Timer swaps the eye frame for 0.1 s every few seconds.

Alternatives we skipped: a `Decal` projected onto the head (doesn't work with the
Compatibility renderer, so it would break a future web build), and painting the
face into the body texture (we'd have to know the head's UV layout, and every
face change would need a new texture).

The first thing to do is place a test sprite on `headfront` in the editor and
nudge it until it sits on the face. Those offsets become constants in `mii.gd`.

### 2. Hair, hats, accessories

- Each is a tiny `.glb` modelled around the head's origin, in `assets/mii/props/`.
- Attach with a second BoneAttachment3D on `spine.005` (the head bone). Hats can use
  `head_end` (the top of the head).
- Tint hair with `hair_color`, the same way `mii.gd` tints the body.
- Fun first set: one hat per building (mail cap, hard hat, lab goggles, judge's
  wig, postie cap). A resident could wear the hat of the building they're in.

### 3. Body shape

`height` and `build` sliders scale the `Mii` node: `scale = Vector3(w, h, w)`. Keep
it to ±15% so the walk animation still looks right.

### 4. Portraits for cards and bubbles

`resident_card` and `bubble_card` show an initial today. Add a `PortraitStudio`
autoload: one hidden SubViewport with a Mii and a camera close on the head. To
make a portrait, apply the appearance, wait one frame, then copy
`viewport.get_texture().get_image()` into an `ImageTexture`. Cache it by appearance,
so each look renders once.

### 5. Creator UI

Keep the current four panels. Put the "Face" row back in the Identity panel (four
thumbnail buttons in the `ChoiceButton` style, as in the mockup) and add a small
tab strip like the Bubbles sidebar's (`TabsPanel`): **Face · Hair · Hat · Body**.
When a face tab is open, tween the preview camera closer to the head.

### 6. Performance on the island

52k vertices × 10 residents is fine on a laptop. For the web demo, decimate a copy
in Blender to about 8k for the island and keep the detailed one for the creator.
Godot's import already makes LODs (`meshes/generate_lods`), which helps when
zoomed out.

## Suggested order

1. ~~Protocol + backend for `create_resident`~~ (done).
2. `keys` + `gifts` in `AcpRunner` (makes the creator matter).
3. Faces with mood expressions.
4. Portraits in the cards and sidebar.
5. Hats, hair, body shape.
