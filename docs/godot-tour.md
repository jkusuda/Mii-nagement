# A guided tour of the Godot project

This is for someone who knows Python but has never opened Godot.

## Five ideas that explain most of Godot

| Godot idea | Python-ish analogy | Where you'll see it |
|---|---|---|
| **Node**: one object in the game (a label, a 3D model, a light, a button) | an object instance | everywhere |
| **Scene**: a saved tree of nodes (`.tscn`). Scenes can contain other scenes. | a class you can instantiate | `scenes/*.tscn` |
| **Script** (`.gd`, GDScript): code attached to a node that adds behavior | a subclass of that node's type | `scenes/*.gd` |
| **Signal**: a node announces "this happened" and others connect functions to it | an event or callback | `net.gd`, buttons |
| **Autoload**: a script Godot creates once at startup, reachable by name from anywhere | a module-level singleton | `Net`, `Islands`, `Clock`, `Portraits`, `Audio` |

GDScript reads a lot like Python: indentation blocks, `func` instead of `def`,
`var` to declare variables, and optional types (`var name: String`). Functions
named with a leading `_`, like `_ready` and `_process`, are called by Godot itself.

## Open the project

```bash
make game                                   # or: godot -e --path game  (the editor)
```

The editor has four main areas:
- **Scene dock** (top left): the node tree of the open scene.
- **FileSystem dock** (bottom left): your files. `res://` means "the `game/` folder".
- **Viewport** (center): the 2D or 3D view. Use the **2D / 3D / Script** tabs at
  the top to switch.
- **Inspector** (right): the properties of the selected node.

Press **F5** to run the main scene (`game.tscn`), or **F6** to run only the
scene that's open.

## The files, in the order to read them

### 1. `project.godot`: settings
The window size (1280×720), the start scene (`scenes/game.tscn`), and the four
autoloads (`Net`, `Islands`, `Clock`, `Portraits`, `Audio`). Change these through **Project → Project Settings**.

### 2. `net.gd`: the `Net` autoload
- This is the only code that talks to the backend.
- `_process(delta)` runs every frame. It calls `poll()` on the `WebSocketPeer`,
  reads any messages, and emits one signal per event type (see the `signal`
  lines at the top).
- When the server goes away, it waits 2 seconds and tries again.
- `send_answer(bubble_id, text)` sends your reply to a bubble.

- `island` is the latest island state: the snapshot, updated by every event
  after it (states, moods, bubbles, tasks). After each change Net emits
  `island_changed`, and the screens redraw from `Net.island`. Signals like
  `agent_moved` are for things that need the moment itself (starting a walk).
- `send_resident(data)` creates or edits a resident (the creator).

### 3. `scenes/game.tscn` + `game.gd`: the main screen
```
Game (Control)                      game.gd; theme = ui/theme.tres
└── Margin → Columns
    ├── Left
    │   ├── TopBar                  back button, island picker, connection pill, day pill, logo
    │   ├── IslandFrame
    │   │   ├── Mask (rounded)      clip_children: cuts everything inside to the rounded shape
    │   │   │   └── ViewportContainer → SubViewport → Island3D (the 3D world)
    │   │   ├── Labels              building name pills, added by game.gd
    │   │   ├── Border              the white rounded outline
    │   │   └── 4 corner pills      island view, weather, hint, zoom − 100% +
    │   └── NavRow                  Build / Residents / News / Insights
    └── Sidebar                     Bubbles title, tabs, 3 bubble cards, island mood
```
- **Control vs Node2D vs Node3D:** Controls are UI (they handle clicks and
  layout), Node3D nodes live in the 3D world.
- **Containers** (HBox, VBox, Margin, Center) lay out their children
  automatically. Rearrange things by dragging nodes in the Scene dock.
- **SubViewport** is a "screen inside the screen": it renders the 3D island into
  the frame, so 3D and normal UI can sit side by side.
- `game.gd` creates the building labels, wires the zoom buttons, and keeps the
  connection pill in sync with `Net`.

### 4. `scenes/island/island_3d.tscn`: the 3D world
```
Island3D (Node3D)
├── WorldEnvironment    sky light, Filmic color, soft shadows (SSAO), glow
├── Sun                 DirectionalLight3D with soft shadows
├── CameraRig           camera_rig.gd: zoom + drag
│   └── Camera3D        tilted 45°, 30° field of view
├── Water               a big plane using shaders/water.gdshader
├── Island              island_builder.gd: builds the island from data (see 8b)
├── Residents           island_residents.gd: the walking Miis
└── DayNight            day_night.gd: moves the sun and recolours light and water
```
Click any node and change it in the Inspector: rotate the Sun, tilt the camera.
Click **Preview** at the top of the 3D view to look through the camera. The
island itself is built when the game runs (from the backend's layout), so to
change *where things are*, use **Build** in the game. To change *how a building
looks or sits on its lot*, open its scene in `scenes/buildings/`.

### 5. `scenes/island/camera_rig.gd`: zoom and drag
- The camera is a child of the rig. Zoom moves the camera closer; drag slides
  the whole rig across the ground.
- `_unhandled_input()` gets only the clicks the UI didn't use, so buttons never
  pan the island.
- `_process()` eases toward the target zoom and position each frame.

### 6. `scenes/ui/`: reusable UI pieces
- `building_label`: each frame it asks the camera where its building is on
  screen (`unproject_position`) and moves there.
- `bubble_card`: one sidebar card. Its `@export` variables (name, colors, text)
  show in the Inspector; `@tool` makes the editor preview update live.
- `answer_button.gd`: paints the wavy orange button background in code.

### 7. `scenes/residents/` and `scenes/mii/`: Residents tab and creator
- The **Residents** nav button swaps the island frame for `residents_screen`
  (`game.gd` `show_screen()` just toggles `visible`). "+ New resident", "Invite a
  resident", and each card's **Edit** open `resident_creator`; the sidebar hides
  so the creator gets the full width. The back button steps back one screen.
- `resident_data.gd` holds the lists (jobs, brains, sliders, house keys, gifts)
  and the mock residents shown until the backend sends a snapshot. The vocabulary
  shared with the backend (building types, bubble kinds...) is in `scenes/protocol.gd`.
- `mii.tscn` is the 3D Mii: the model plus `mii.gd`, which tints it and plays
  idle / walk / run. `mii_preview.tscn` puts one Mii on its own little stage
  (a SubViewport with `own_world_3d`, so it isn't standing on the island).
- Plan for faces, hats, and portraits: [mii-customization.md](mii-customization.md).

### 8. `scenes/island/`: residents on the 3D island
- `island_residents.gd` (the `Residents` node in island_3d.tscn) makes one
  `island_resident` per resident and listens to Net. On `move` it asks
  `IslandPaths.route()` for the points along the sand path to the new building
  (`IslandLayout.walk_tiles` finds the shortest way with the fewest turns).
- `island_resident.gd` walks through those points with a **Tween** (a node that
  animates a property over time) and shows the state: hop = working, "z Z" =
  asleep, an icon in a bubble = needs you.
- `island_paths.gd` turns the path tiles into points to walk through (it only
  keeps the corners).

### 8b. `scenes/island/`: the island built from data
Every island has a **layout** (from the backend): a 20 × 14 grid of land tiles,
a pond, plants, a season, and each building's lot and facing.
- `island_layout.gd` is that data plus the rules: is this tile land, does this
  building fit, and the **paths**, worked out by a small path-finding search that
  joins the buildings' doors (turning costs extra, so paths come out as neat
  L-shapes). When the owner hasn't placed plants, it scatters trees and bushes
  itself, with a fixed random seed so the same island always looks the same.
- `island_terrain.gd` makes the ground: a little "field" image (distance to the
  coast, where the paths are) scaled up smoothly, which rounds the square tiles
  into a soft coastline, then an **ArrayMesh** (a mesh built in code) whose
  height follows the field: grass, a rounded edge, the beach, the sea floor.
- `island_builder.gd` puts it all in the scene: the ground (colored by
  `shaders/terrain.gdshader`), the pond, the five building scenes, plants, and
  lamp posts. `scenes/island/seasons.gd` then gives it the season's look.
- `scenes/build/build_mode.gd` is the **Build** button: it edits a copy of the
  layout, rebuilds the island after every change, and sends it to the backend on
  Save. It finds the tile under the mouse by **ray casting**: a ray from the
  camera through the mouse, down to the ground.

### 9. `scenes/insights/`: the Insights tab
- `insights_screen.gd` asks the backend for `/islands/{id}/insights` with an
  **HTTPRequest** node each time the tab opens (and when a task changes). The
  stat cards, bars, and rows are built in code from the response.
- "Where packages are waiting" and the comfort report's moods come from
  `Net.island`, so they're live without asking the backend.

### 10. `scenes/news/`: the News tab
- `news_stories.gd` turns backend events into stories ("Carlos got puzzled at
  the Lab."). Edit the wording there.
- `news_screen.gd` listens to every event (`Net.event_received`) even while
  hidden, so the News button's badge can count stories you haven't seen. It
  saves when you last looked in `user://news.cfg` (a per-user folder Godot
  gives every game).

### 11. `islands.gd` and `scenes/home/`: the Archipelago
- The game opens on the Archipelago (`home.tscn`): one card per island with a
  picture of its 3D model, and "Waiting on you" with every island's bubbles.
- `islands.gd` (the `Islands` autoload) asks the backend for every island and
  keeps their snapshots fresh. Its `thumbnail()` renders an island into a hidden
  **SubViewport** with an orthographic camera, once, and hands out the texture.
- Clicking an island calls `Net.visit_island(id)`: Net reconnects to that
  island's events, and every screen redraws from the new snapshot. The island
  name in the top bar opens `island_dropdown.gd`, a **PopupPanel** (a small
  window that closes when you click outside it).

### 12. `scenes/home/sea_view.gd` and the island creator
- `sea_view.gd` is a **SubViewportContainer** that borrows island_3d.tscn's sky,
  sun, and water, then places any number of islands in it. The water shader
  takes up to 8 island centers (`island_centers`) so every island gets its own
  shallows and foam. The camera backs off until every island fits.
- `scenes/home/island_creator.gd` is the two-step "Found a new island" screen.
  It asks the backend for your repos and branches (`gh`), and "Make your own"
  borrows the resident creator; those residents are sent once the new island
  exists (`game.gd` `_move_residents_in`).

### 13. `ui/theme.tres` and `shaders/water.gdshader`
- **Theme:** every color, font, and pill/button style. Each look is a named
  *type variation* (`PillWhite`, `NavButton`, …) that nodes pick with
  `theme_type_variation`. Double-click the file to open the theme editor.
- **Water shader:** a small GPU program that colors every water pixel by its
  distance to the island (shallows, foam rings, wave streaks). Its settings are
  under Water → Surface Material Override → Shader Parameters.

## What you should see when you press Play (F5)

- The island in a rounded frame, with the top bar, bottom nav, and Bubbles sidebar.
- Scroll, pinch, or the − / + buttons zoom; click and drag pans. The name pills
  follow their buildings.
- The connection pill turns green when the backend is running.
- Once you found an island and send it a package (`make dev` runs it on mock brains),
  residents walk the sand path between buildings, hop while working, show
  "z Z" when asleep, and get an icon over their head when they need you. The
  sidebar fills with their bubbles, and the building pills count who's there.
- The top bar shows the island's day and the time; the weather pill follows the
  backend's weather; the News button counts stories you haven't read.

## Handy editor tricks

- While the game runs, the Scene dock gets a **Remote** tab showing the live
  node tree, including nodes created by code.
- `print()` output and errors show in the **Output** panel at the bottom.
- Cmd-click a class or method name in the script editor to open its documentation.
