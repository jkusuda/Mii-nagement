class_name IslandLayout
extends RefCounted
## One island's layout: which tiles are land, the pond, the plants, the season,
## and where each building stands. It also works out the things that follow from
## those: the sand paths between the doors and (when the owner hasn't placed any
## by hand) where trees and bushes grow.
##
## The data comes from the backend (snapshot `map` + `buildings`, see
## docs/protocol.md "Island map"); IslandBuilder turns it into 3D nodes.
##
## Coordinates: the island is a GRID_W x GRID_H grid of tiles, TILE world units
## each, centered on the origin. Tile (c, r) covers x from (c - 10) * 2 to
## (c - 9) * 2 and z from (r - 7) * 2 to (r - 6) * 2. Positions in "tile units"
## (plants) are the same thing without the * 2: (10.5, 7.5) is the island's middle.
##
## Godot concept: RefCounted is a plain object (not a node in the scene tree)
## that frees itself when nothing uses it any more. Good for data like this.

const GRID_W := 20
const GRID_H := 14
const TILE := 2.0
const LOT := 3  # a building stands on a LOT x LOT lot
const STEP_ORDER := Protocol.STEP_ORDER
const REQUIRED_STEPS := Protocol.REQUIRED_STEPS
const SEASONS := Protocol.SEASONS
const STEP_NAMES := Protocol.STEP_NAMES

## The island shapes, same as SHAPES in backend/mii/islands.py (keep them in step):
## land rows, a pond, and a lot (x, y, facing) for every step.
const SHAPES := {
	"classic":
	{
		"title": "Classic",
		"land":
		[
			"....................",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			"...................."
		],
		"pond": {"x": 7, "y": 6, "w": 5, "h": 2},
		"lots":
		{
			"mailbox": [3, 2, 0],
			"workshop": [8, 2, 0],
			"lab": [13, 2, 0],
			"courthouse": [15, 8, 3],
			"post_office": [2, 8, 1]
		},
	},
	"round":
	{
		"title": "Round",
		"land":
		[
			"....................",
			"......########......",
			"....############....",
			"...##############...",
			"..################..",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			"..################..",
			"...##############...",
			"....############....",
			"......########......",
			"...................."
		],
		"pond": {"x": 9, "y": 6, "w": 3, "h": 2},
		"lots":
		{
			"mailbox": [5, 3, 0],
			"workshop": [8, 2, 0],
			"lab": [12, 3, 0],
			"courthouse": [12, 8, 2],
			"post_office": [5, 8, 2]
		},
	},
	"long":
	{
		"title": "Main street",
		"land":
		[
			"....................",
			"....................",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			"....................",
			"...................."
		],
		"pond": {"x": 15, "y": 8, "w": 3, "h": 2},
		"lots":
		{
			"mailbox": [2, 3, 0],
			"workshop": [7, 3, 0],
			"lab": [12, 3, 0],
			"courthouse": [10, 8, 2],
			"post_office": [4, 8, 2]
		},
	},
	"l_shape":
	{
		"title": "L-shape",
		"land":
		[
			"....................",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".#########..........",
			".#########..........",
			".#########..........",
			".#########..........",
			".#########..........",
			".#########..........",
			"...................."
		],
		"pond": {"x": 6, "y": 8, "w": 2, "h": 3},
		"lots":
		{
			"mailbox": [2, 2, 0],
			"workshop": [6, 2, 0],
			"lab": [10, 2, 0],
			"courthouse": [14, 2, 3],
			"post_office": [2, 8, 1]
		},
	},
	"twin":
	{
		"title": "Two coves",
		"land":
		[
			"....................",
			".########..########.",
			".########..########.",
			".########..########.",
			".########..########.",
			".##################.",
			".##################.",
			".##################.",
			".##################.",
			".########..########.",
			".########..########.",
			".########..########.",
			".########..########.",
			"...................."
		],
		"pond": {},
		"lots":
		{
			"mailbox": [2, 2, 0],
			"workshop": [5, 2, 0],
			"lab": [12, 2, 0],
			"courthouse": [15, 2, 0],
			"post_office": [12, 8, 2]
		},
	},
}

# Path finding costs (per tile): paths prefer open ground, avoid hugging the
# coast, reuse paths already laid, and go straight rather than zig-zag.
const COST_LAND := 1.0
const COST_COAST := 3.0  # extra, next to the sea
const COST_PATH := 0.6  # a tile that's already path
const COST_TURN := 0.6

# Auto plants.
const MAX_TREES := 13
const MAX_BUSHES := 11

var shape := "classic"
var land: PackedStringArray
var pond := {}  # {x, y, w, h} in tiles, or {} for none
var plants = null  # null = arranged automatically, or [{kind, x, y, rot, scale}]
var season := "summer"
var buildings: Array = []  # [{type, x, y, facing}] in STEP_ORDER
var seed_text := ""  # the island id: the same island always grows the same plants

# Worked out by refresh():
var path_tiles := {}  # Vector2i -> true
var shown_plants: Array = []  # the plants to draw (auto, or the hand-placed ones that fit)

# --- Making layouts ---------------------------------------------------------------


## A layout from a snapshot's island dictionary (falls back to the classic island).
static func from_snapshot(island: Dictionary) -> IslandLayout:
	var data: Dictionary = island.get("map", {}) if island.get("map") is Dictionary else {}
	var layout := from_shape(str(data.get("shape", "classic")) if SHAPES.has(data.get("shape")) else "classic", [])
	layout.seed_text = str(island.get("id", ""))
	if data.get("land") is Array and data["land"].size() == GRID_H:
		layout.land = PackedStringArray(data["land"])
		layout.shape = str(data.get("shape", "custom"))
	layout.pond = data.get("pond") if data.get("pond") is Dictionary else ({} if data.has("land") else layout.pond)
	layout.plants = data.get("plants") if data.get("plants") is Array else null
	layout.season = str(data.get("season", "summer")) if str(data.get("season", "")) in SEASONS else "summer"
	var list: Array = []
	for b in island.get("buildings", []):
		list.append(
			{
				"type": str(b["type"]),
				"x": int(b.get("x", 0)),
				"y": int(b.get("y", 0)),
				"facing": int(b.get("facing", 0))
			}
		)
	if not island.has("buildings"):  # offline: the classic island with everything
		list = from_shape("classic", STEP_ORDER).buildings
	layout.buildings = in_step_order(list)
	return layout


## A fresh island of one of the SHAPES with these steps (building types).
static func from_shape(shape_id: String, steps: Array, seed := "") -> IslandLayout:
	var preset: Dictionary = SHAPES.get(shape_id, SHAPES["classic"])
	var layout := IslandLayout.new()
	layout.shape = shape_id
	layout.land = PackedStringArray(preset["land"])
	layout.pond = preset["pond"].duplicate()
	layout.seed_text = seed
	for t in STEP_ORDER:
		if t in steps:
			var lot: Array = preset["lots"][t]
			layout.buildings.append({"type": t, "x": lot[0], "y": lot[1], "facing": lot[2]})
	return layout


## A deep copy, for build mode's draft.
func copy() -> IslandLayout:
	var other := IslandLayout.new()
	other.shape = shape
	other.land = land.duplicate()
	other.pond = pond.duplicate()
	other.plants = plants.duplicate(true) if plants is Array else null
	other.season = season
	other.buildings = buildings.duplicate(true)
	other.seed_text = seed_text
	return other


## The body for PUT /islands/{id}/layout (the "map" form).
func to_request() -> Dictionary:
	return {
		"map":
		{
			"version": 1,
			"shape": shape,
			"land": Array(land),
			"pond": pond if not pond.is_empty() else null,
			"plants": plants,
			"season": season
		},
		"buildings": buildings.duplicate(true),
	}


static func in_step_order(list: Array) -> Array:
	var out: Array = []
	for t in STEP_ORDER:
		for b in list:
			if b["type"] == t:
				out.append(b)
	return out


# --- Tiles and positions ------------------------------------------------------------


func is_land(t: Vector2i) -> bool:
	return t.x >= 0 and t.y >= 0 and t.x < GRID_W and t.y < GRID_H and land[t.y][t.x] == "#"


func set_land(t: Vector2i, on: bool) -> void:
	if t.x <= 0 or t.y <= 0 or t.x >= GRID_W - 1 or t.y >= GRID_H - 1:
		return  # the grid's edge stays sea, room for the beach
	var row := land[t.y]
	land[t.y] = row.substr(0, t.x) + ("#" if on else ".") + row.substr(t.x + 1)


## The world position of a point in tile units (see the top of this file).
static func tile_to_world(p: Vector2) -> Vector3:
	return Vector3((p.x - GRID_W / 2.0) * TILE, 0.0, (p.y - GRID_H / 2.0) * TILE)


## The middle of a tile, in world space.
static func tile_center(t: Vector2i) -> Vector3:
	return tile_to_world(Vector2(t) + Vector2(0.5, 0.5))


static func world_to_tile_units(p: Vector3) -> Vector2:
	return Vector2(p.x / TILE + GRID_W / 2.0, p.z / TILE + GRID_H / 2.0)


static func world_to_tile(p: Vector3) -> Vector2i:
	var t := world_to_tile_units(p)
	return Vector2i(floori(t.x), floori(t.y))


## Where a building stands (the middle of its lot) and which way it turns.
static func lot_center(b: Dictionary) -> Vector3:
	return tile_to_world(Vector2(b["x"], b["y"]) + Vector2(LOT, LOT) / 2.0)


static func facing_angle(b: Dictionary) -> float:
	return int(b["facing"]) * PI / 2.0  # 0 south, 1 east, 2 north, 3 west (models face south)


## The tile just outside the door, where the building's path starts.
static func door_tile(b: Dictionary) -> Vector2i:
	var x: int = b["x"]
	var y: int = b["y"]
	return [Vector2i(x + 1, y + LOT), Vector2i(x + LOT, y + 1), Vector2i(x + 1, y - 1), Vector2i(x - 1, y + 1)][int(
		b["facing"]
	)]


static func lot_tiles(b: Dictionary) -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	for c in range(b["x"], b["x"] + LOT):
		for r in range(b["y"], b["y"] + LOT):
			out.append(Vector2i(c, r))
	return out


func pond_tiles() -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	if pond.is_empty():
		return out
	for c in range(pond["x"], pond["x"] + pond["w"]):
		for r in range(pond["y"], pond["y"] + pond["h"]):
			out.append(Vector2i(c, r))
	return out


func building(type: String) -> Dictionary:
	for b in buildings:
		if b["type"] == type:
			return b
	return {}


func has_building(type: String) -> bool:
	return not building(type).is_empty()


## Tiles nothing can walk through: building lots and the pond.
func blocked_tiles() -> Dictionary:
	var blocked := {}
	for t in pond_tiles():
		blocked[t] = true
	for b in buildings:
		for t in lot_tiles(b):
			blocked[t] = true
	return blocked


# --- Rules (the same as validate_layout in backend/mii/islands.py) -------------------


## What's wrong with this layout, in words for the player; "" if it's fine.
func problem() -> String:
	var tiles := {}
	for r in GRID_H:
		for c in GRID_W:
			if is_land(Vector2i(c, r)):
				if c == 0 or r == 0 or c == GRID_W - 1 or r == GRID_H - 1:
					return "Keep the edge of the map as sea."
				tiles[Vector2i(c, r)] = true
	if tiles.is_empty():
		return "The island needs some land."
	if _reachable(tiles.keys()[0], tiles).size() != tiles.size():
		return "The land must be one piece."
	for t in pond_tiles():
		if not tiles.has(t):
			return "The pond must be on land."
	for t in REQUIRED_STEPS:
		if not has_building(t):
			return "The island needs a %s." % STEP_NAMES[t]
	var blocked := {}
	for t in pond_tiles():
		blocked[t] = true
	for b in buildings:
		for c in range(b["x"] - 1, b["x"] + LOT + 1):
			for r in range(b["y"] - 1, b["y"] + LOT + 1):
				if not tiles.has(Vector2i(c, r)):
					return "The %s needs land all round it." % STEP_NAMES[b["type"]]
		for t in lot_tiles(b):
			if blocked.has(t):
				return "The %s overlaps something." % STEP_NAMES[b["type"]]
			blocked[t] = true
	var free := {}
	for t in tiles:
		if not blocked.has(t):
			free[t] = true
	for b in buildings:
		if not free.has(door_tile(b)):
			return "The %s's door is blocked." % STEP_NAMES[b["type"]]
	if buildings.is_empty():
		return ""
	var reach := _reachable(door_tile(buildings[0]), free)
	for b in buildings:
		if not reach.has(door_tile(b)):
			return "No path can reach the %s." % STEP_NAMES[b["type"]]
	return ""


## Would the island still be fine with building b here (moved, turned, or new)?
func can_place(b: Dictionary) -> bool:
	# Quick checks first (most spots fail these), then all the rules.
	for c in range(b["x"] - 1, b["x"] + LOT + 1):
		for r in range(b["y"] - 1, b["y"] + LOT + 1):
			if not is_land(Vector2i(c, r)):
				return false
	var other := copy()
	other.buildings = other.buildings.filter(func(o): return o["type"] != b["type"])
	other.buildings.append(b)
	other.buildings = in_step_order(other.buildings)
	return other.problem() == ""


## A good free lot for a new building: the nearest valid one to the island's middle.
func find_spot(type: String) -> Dictionary:
	var middle := Vector2.ZERO
	var count := 0
	for r in GRID_H:
		for c in GRID_W:
			if is_land(Vector2i(c, r)):
				middle += Vector2(c, r) + Vector2(0.5, 0.5)
				count += 1
	middle /= maxi(count, 1)
	var spots := []
	for y in range(1, GRID_H - LOT):
		for x in range(1, GRID_W - LOT):
			spots.append([Vector2(x + LOT / 2.0, y + LOT / 2.0).distance_squared_to(middle), x, y])
	spots.sort_custom(func(a, b): return a[0] < b[0])
	for spot in spots:
		for facing in [0, 2, 1, 3]:
			var b := {"type": type, "x": spot[1], "y": spot[2], "facing": facing}
			if can_place(b):
				return b
	return {}


## Move (or resize) the pond, if the island stays fine. {} = no pond.
func move_pond(new_pond: Dictionary) -> bool:
	var before := pond
	pond = new_pond
	if problem() == "":
		return true
	pond = before
	return false


## Room for this plant (public, for build mode)?
func plant_fits(plant: Dictionary, others: Array) -> bool:
	return _plant_fits(plant, others)


static func _reachable(start: Vector2i, tiles: Dictionary) -> Dictionary:
	var seen := {start: true}
	var queue: Array[Vector2i] = [start]
	while not queue.is_empty():
		var t: Vector2i = queue.pop_front()
		for n in [t + Vector2i.RIGHT, t + Vector2i.LEFT, t + Vector2i.DOWN, t + Vector2i.UP]:
			if tiles.has(n) and not seen.has(n):
				seen[n] = true
				queue.append(n)
	return seen


# --- Paths and plants ---------------------------------------------------------------


## Work out the paths and the plants. Call after changing anything.
func refresh() -> void:
	_lay_paths()
	shown_plants = _auto_plants() if plants == null else _fitting(plants)


## The paths join the doors in workflow order and close the loop back to the
## mailbox. Each new path prefers tiles that are already path, so they merge.
func _lay_paths() -> void:
	path_tiles.clear()
	var doors: Array[Vector2i] = []
	for b in buildings:
		doors.append(door_tile(b))
	var blocked := blocked_tiles()
	for i in doors.size():
		path_tiles[doors[i]] = true
	if doors.size() < 2:
		return
	var pairs := []
	for i in doors.size() - 1:
		pairs.append([doors[i], doors[i + 1]])
	if doors.size() > 2:
		pairs.append([doors[-1], doors[0]])
	for pair in pairs:
		for t in _route(pair[0], pair[1], blocked):
			path_tiles[t] = true


## The cheapest way from tile a to tile b (a Dijkstra search; turning costs a
## little extra, so paths come out as clean L-shapes instead of staircases).
func _route(a: Vector2i, b: Vector2i, blocked: Dictionary) -> Array[Vector2i]:
	var dirs := [Vector2i.RIGHT, Vector2i.DOWN, Vector2i.LEFT, Vector2i.UP]
	var best := {}  # [tile, dir] -> cost
	var came_from := {}  # [tile, dir] -> [tile, dir]
	var open := [[0.0, a, -1]]
	best[[a, -1]] = 0.0
	var end_state = null
	while not open.is_empty():
		# The cheapest open state (the grid is small, a sorted pick is plenty fast).
		var pick := 0
		for i in open.size():
			if open[i][0] < open[pick][0]:
				pick = i
		var here: Array = open.pop_at(pick)
		var cost: float = here[0]
		var tile: Vector2i = here[1]
		var dir: int = here[2]
		if cost > best.get([tile, dir], INF):
			continue
		if tile == b:
			end_state = [tile, dir]
			break
		for d in 4:
			var next: Vector2i = tile + dirs[d]
			if not is_land(next) or (blocked.has(next) and next != b):
				continue
			var step := COST_PATH if path_tiles.has(next) else COST_LAND + (COST_COAST if _by_sea(next) else 0.0)
			if dir != -1 and d != dir:
				step += COST_TURN
			var total := cost + step
			if total < best.get([next, d], INF):
				best[[next, d]] = total
				came_from[[next, d]] = [tile, dir]
				open.append([total, next, d])
	var out: Array[Vector2i] = []
	if end_state == null:
		return out
	var state = end_state
	while state != null:
		out.push_front(state[0])
		state = came_from.get(state)
	return out


func _by_sea(t: Vector2i) -> bool:
	for dx in [-1, 0, 1]:
		for dy in [-1, 0, 1]:
			if not is_land(t + Vector2i(dx, dy)):
				return true
	return false


## Scatter trees and bushes where they look nice: on grass, away from the coast,
## paths, buildings, and the pond. Trees lean toward the shore, bushes inland.
## Always the same for the same island (the random numbers start from seed_text).
func _auto_plants() -> Array:
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(seed_text + "plants")
	var out: Array = []
	var trees := 0
	var bushes := 0
	for attempt in 1400:
		if trees >= MAX_TREES and bushes >= MAX_BUSHES:
			break
		var p := Vector2(rng.randf_range(1.0, GRID_W - 1.0), rng.randf_range(1.0, GRID_H - 1.0))
		var sea := _distance_to_sea(p)
		# Mostly along the shore; the middle of the island stays fairly open.
		if sea > 2.5 and rng.randf() > 0.3:
			continue
		var tree := rng.randf() < (0.7 if sea < 2.2 else 0.3)
		if (tree and trees >= MAX_TREES) or (not tree and bushes >= MAX_BUSHES):
			tree = not tree
		var plant := {
			"kind": "tree" if tree else "bush",
			"x": p.x,
			"y": p.y,
			"rot": rng.randf_range(0.0, 360.0),
			"scale": rng.randf_range(0.85, 1.15)
		}
		if not _plant_fits(plant, out):
			continue
		out.append(plant)
		if tree:
			trees += 1
		else:
			bushes += 1
	return out


## Hand-placed plants that still fit (a building may have moved onto one).
func _fitting(list: Array) -> Array:
	var out: Array = []
	for plant in list:
		if _plant_fits(plant, out, false):
			out.append(plant)
	return out


## Room for this plant? `spacing` also keeps it apart from the plants so far.
func _plant_fits(plant: Dictionary, others: Array, spacing := true) -> bool:
	var p := Vector2(plant["x"], plant["y"])
	var tree: bool = plant["kind"] == "tree"
	if not is_land(Vector2i(floori(p.x), floori(p.y))) or _distance_to_sea(p) < (0.75 if tree else 0.6):
		return false
	if _distance_to_tiles(p, path_tiles) < (0.7 if tree else 0.55):
		return false
	for b in buildings:
		var near := 0.75 if tree else 0.45
		if _distance_to_rect(p, Rect2(b["x"], b["y"], LOT, LOT)) < near:
			return false
	if not pond.is_empty() and _distance_to_rect(p, Rect2(pond["x"], pond["y"], pond["w"], pond["h"])) < 0.7:
		return false
	if spacing:
		for other in others:
			var gap := 1.6 if tree or other["kind"] == "tree" else 1.1
			if p.distance_to(Vector2(other["x"], other["y"])) < gap:
				return false
	return true


func _distance_to_sea(p: Vector2) -> float:
	var best := 9.0
	var t := Vector2i(floori(p.x), floori(p.y))
	for dx in range(-3, 4):
		for dy in range(-3, 4):
			var n := t + Vector2i(dx, dy)
			if not is_land(n):
				best = minf(best, _distance_to_rect(p, Rect2(n, Vector2.ONE)))
	return best


func _distance_to_tiles(p: Vector2, tiles: Dictionary) -> float:
	var best := 9.0
	var t := Vector2i(floori(p.x), floori(p.y))
	for dx in range(-2, 3):
		for dy in range(-2, 3):
			var n := t + Vector2i(dx, dy)
			if tiles.has(n):
				best = minf(best, _distance_to_rect(p, Rect2(n, Vector2.ONE)))
	return best


static func _distance_to_rect(p: Vector2, rect: Rect2) -> float:
	var dx := maxf(maxf(rect.position.x - p.x, 0.0), p.x - rect.end.x)
	var dy := maxf(maxf(rect.position.y - p.y, 0.0), p.y - rect.end.y)
	return Vector2(dx, dy).length()


# --- Walking ----------------------------------------------------------------------


## The path tile nearest to a world position.
func nearest_path_tile(p: Vector3) -> Vector2i:
	var target := world_to_tile_units(p)
	var best := Vector2i(-1, -1)
	var best_d := INF
	for t in path_tiles:
		var d := (Vector2(t) + Vector2(0.5, 0.5)).distance_squared_to(target)
		if d < best_d:
			best_d = d
			best = t
	return best


## Path tiles from a to b, walking only on path (a breadth-first search).
func walk_tiles(a: Vector2i, b: Vector2i) -> Array[Vector2i]:
	var came_from := {a: a}
	var queue: Array[Vector2i] = [a]
	while not queue.is_empty():
		var t: Vector2i = queue.pop_front()
		if t == b:
			break
		for n in [t + Vector2i.RIGHT, t + Vector2i.LEFT, t + Vector2i.DOWN, t + Vector2i.UP]:
			if path_tiles.has(n) and not came_from.has(n):
				came_from[n] = t
				queue.append(n)
	var out: Array[Vector2i] = []
	if not came_from.has(b):
		return [a, b]
	var t := b
	while t != a:
		out.push_front(t)
		t = came_from[t]
	out.push_front(a)
	return out


## A key that changes whenever the ground (land, pond, or paths) changes, so the
## terrain is only rebuilt when it must be.
func ground_key() -> String:
	var tiles := path_tiles.keys()
	tiles.sort()
	return "%s|%s|%s" % ["".join(land), JSON.stringify(pond), str(tiles)]
