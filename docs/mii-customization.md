# Mii customization

Status: **faces, hair and headshots done** (Look page in the creator, portraits on
the resident cards). Still a plan: mood expressions, portraits in bubbles, body shape. This doc covers the
Mii model, how a look is drawn, how the creator's fields reach the ACP agents,
and what's next.

## The model we have

`game/assets/models/mii.glb` is the rigged Meshy "blank slate" Mii, copied in by
`tools/build_mii_parts.py` (which drops its 16 MB grey texture).

| Thing | What's in it |
|---|---|
| Mesh | one skinned mesh, `char1`, about 52k vertices. Head, body, and limbs are one piece. |
| Size | 1.7 units tall, feet at y = 0, facing +Z (toward the camera in our scenes) |
| Skeleton | 23 Mixamo bones, imported as `mixamorig_Hips`, `mixamorig_Head`, `mixamorig_LeftHand`, … (Godot turns the `:` into `_`), plus `headfront` (front of the face) |
| Animations | `Idle_11` (our idle), `Idle_3` (a 10 s idle), `Walking`, `Running`, and `Armature\|clip0\|baselayer` (one frame, the bind pose) |
| Material | none we use: `mii.gd` draws it with `shaders/mii.gdshader` |
| Not in it | no face, no hair, no blend shapes (shape keys) |

`scenes/mii/mii.tscn` wraps it: `Mii.color` is the shirt, `Mii.appearance` is the
look, `Mii.play("idle" | "walk" | "run")` switches animation. The same scene is
used in the creator's preview and for residents walking on the island.

## Faces and hair (done)

### The parts

The pictures are rips from the Wii U Mii Maker: four sprite sheets (60 eyes,
24 brows, 18 noses, 36 mouths), `Hair.obj` with 68 hairstyles, and `special.obj`
with a cap and a beanie. `tools/build_mii_parts.py` turns them into game files:

```bash
python tools/build_mii_parts.py "<folder with the rips>"   # needs Pillow, NumPy, SciPy
```

| Output | What it is |
|---|---|
| `assets/mii/eyes.png`, `brows.png`, `noses.png`, `mouths.png` | one atlas per kind: 128 px cells, 8 per row, in the sheet's order. Black ink; white kept only where it's *inside* a shape (eye whites, teeth); transparent elsewhere. |
| `assets/mii/hair/hair_NN.obj`, `hat_cap.obj`, `hat_beanie.obj` | one mesh per style, turned to face +Z. `Hair.obj`'s faces point into the head, so they're flipped. |
| `assets/mii/hair_icons.png` | a thumbnail of every hairstyle on a head (same atlas layout), for the creator |
| `assets/models/mii.glb` | the rigged Mii without its texture |

`MiiParts` (`scenes/mii/mii_parts.gd`) lists them all: style counts, the skin and
hair colour palettes, `icon(kind, i)` for thumbnails, `hair_mesh(i)`, `random()`.

### How a look is drawn

- **Face:** painted by `shaders/mii.gdshader`, not stuck on as sprites. The first
  time a Mii is made, `mii.gd` works out for every vertex where it sits inside
  the head (from the skin weights and the rest pose) and saves it in the mesh's
  `CUSTOM0` channel. The shader uses that to stamp eyes, brows, nose and mouth
  from the atlases onto the front of the head, so the face follows the head
  through every animation. Eyes and brows are drawn once and mirrored. The Mii
  blinks every few seconds (the shader squashes the eyes). Placement lives in
  the shader's `eye_spacing`, `eye_height`, `eye_size`, … uniforms.
- **Body:** the same shader paints the shirt (`Mii.color`), trousers below the
  hips, shoes below the ankles, and skin on the head and hands.
- **Hair:** a `MeshInstance3D` on a `BoneAttachment3D` that follows the head bone.
  The hair meshes were made for a ball-shaped head (`HAIR_HEAD_CENTER`,
  `HAIR_HEAD_RADIUS` in `mii.gd`); we stretch that ball onto this Mii's head and
  move it into the bone's space with the skin's bind pose. Hats keep their knit
  or stripe texture, tinted with the resident's colour.

The baked mesh is cached, so every Mii on the island shares one copy.

### Where the look is stored

`appearance` on the resident (protocol.md). The backend never looks inside it:
it checks it's a small flat object, stores the JSON and sends it back.

```json
"appearance": {"skin": "#F2C6A0", "hair": 12, "hair_color": "#3B2418",
               "eyes": 3, "brows": 1, "nose": 0, "mouth": 2}
```

`hair` is `-1` for bald, and styles from 68 on are the hats. Residents made
before faces existed have `{}`: `MiiParts.for_resident()` gives them a random
look seeded by their id, so they look the same every time (and the creator
starts from that look when you edit them).

### The creator's Look page

Tabs at the top switch between **About** (everything that was there before) and
**Look**: skin and hair colour swatches, then a tab per part (Hair, Eyes, Brows,
Nose, Mouth) with a grid of thumbnails. On the Look page the preview camera
moves in on the face. "Surprise me" rolls a new look too.

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
- **Face row → Catchphrase.** The face came back as the creator's Look page (above).
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

The backend half is done: `AcpRunner` honours `keys` (auto-answers permission
requests) and `gifts` (handed to `session/new` as MCP servers from the `GIFTS`
table; `GET /gifts` says which run on this machine), and `buildings` on
`create_resident` / `update_resident` makes a resident the worker at those
buildings (protocol.md). Still to do in the game: a "works at" picker and
greying out unavailable gifts.

## What's next

### Moods on the face

The protocol already sends `mood`. Map it to an eye/mouth override in `mii.gd`
(`confused` → a wobbly mouth, `tired` → half-closed eyes, `proud` → a big grin):
pick the style numbers from the atlases and set the shader's `eyes` / `mouth`
for as long as the mood lasts, then put the resident's own back.

### Portraits in bubbles

Resident cards show a headshot from the `Portraits` autoload (`game/portraits.gd`):
a hidden SubViewport with one Mii and a camera on its head. `Portraits.of(resident)`
returns a texture at once and fills it a couple of frames later; each look and
colour is photographed once and cached. `bubble_card` still shows an initial:
give its avatar the same `clip_children` + TextureRect treatment as `resident_card.tscn`.

### Body shape

`height` and `build` in `appearance` scale the `Mii` node: `scale = Vector3(w, h, w)`.
Keep it to ±15% so the walk animation still looks right.

### Part placement

Mii Maker also lets you move and resize each part. The shader already has a
uniform for each (`eye_height`, `eye_size`, `mouth_height`, …); adding them to
`appearance` and a few sliders on the Look page is all it takes.

### Performance on the island

52k vertices × 10 residents is fine on a laptop. For the web demo, decimate a copy
in Blender to about 8k for the island and keep the detailed one for the creator.
Godot's import already makes LODs (`meshes/generate_lods`), which helps when
zoomed out.
