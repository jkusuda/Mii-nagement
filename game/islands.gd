extends Node
## Islands: every island on the backend (the archipelago), for the home screen
## and the island dropdown. An autoload, like Net.
##
## - `islands` holds one snapshot per island, in the order GET /islands lists
##   them. The island Net is connected to stays live (Net.island); the others
##   are fetched with GET /islands/{id} every few seconds.
## - thumbnail(id) renders that island's 3D model (only the buildings it has)
##   into a texture, for island cards.
##
## Godot concept: an *autoload* is created once at startup and reachable by name
## from any script (`Islands.islands`). See project.godot.

signal changed

const IslandScene := preload("res://scenes/island/island_3d.tscn")
const POLL_SECONDS := 5.0
const THUMB_SIZE := Vector2i(480, 300)

var islands: Array[Dictionary] = []

var _order: Array = []  # island ids from GET /islands
var _by_id := {}  # id -> snapshot
var _listing := false  # GET /islands is on its way
var _fetching := {}  # id -> true while that island's snapshot is on its way
var _thumbs := {}  # id -> SubViewport
var _dirty := false


func _ready() -> void:
	Net.connected.connect(refresh)
	Net.island_changed.connect(func(): _dirty = true)  # merged in _process, at most once a frame

	# Godot concept: a Timer node fires `timeout` every `wait_time` seconds.
	var timer := Timer.new()
	timer.wait_time = POLL_SECONDS
	timer.timeout.connect(refresh)
	add_child(timer)
	timer.start()


func _process(_delta: float) -> void:
	if _dirty:
		_dirty = false
		_rebuild()


## Ask the backend for the island list (and then each island's snapshot).
func refresh() -> void:
	if not _listing:
		_listing = true
		Net.request_json("/islands", _on_list)


func find(id: String) -> Dictionary:
	return _by_id.get(id, {})


## Every open bubble on every island, as [island, bubble] pairs.
func all_bubbles() -> Array:
	var pairs := []
	for island in islands:
		for bubble in island.get("bubbles", []):
			pairs.append([island, bubble])
	return pairs


func resident_count() -> int:
	var total := 0
	for island in islands:
		total += island.get("residents", []).size()
	return total


## What an island is up to, for its pill: {"text", "color", "kind"}.
static func status(island: Dictionary) -> Dictionary:
	var residents: Array = island.get("residents", [])
	var waiting: int = island.get("bubbles", []).size()
	var working := residents.filter(func(r): return r.get("state") in ["working", "walking"]).size()
	var sleeping := residents.filter(func(r): return r.get("state") == "sleeping").size()
	if waiting:
		return {"text": "Waiting on you · %d" % waiting, "color": Color("D6457A"), "kind": "waiting"}
	if working:
		return {"text": "Busy · %d working" % working, "color": Color("3E7FCB"), "kind": "busy"}
	if residents.is_empty():
		return {"text": "Nobody home yet", "color": Color("8A8FA8"), "kind": "empty"}
	if sleeping == residents.size():
		return {"text": "Napping · all quiet", "color": Color("8A8FA8"), "kind": "napping"}
	return {"text": "All quiet", "color": Color("2E8B57"), "kind": "quiet"}


## A picture of the island's 3D model (its shape, buildings, plants, season).
## The texture stays the same object; it re-renders when the island changes.
func thumbnail(id: String) -> Texture2D:
	if not _thumbs.has(id):
		_thumbs[id] = _make_thumbnail()
	var viewport: SubViewport = _thumbs[id]
	var island := find(id)
	var layout := IslandLayout.from_snapshot(island)
	var key := JSON.stringify([layout.to_request(), id])
	if not viewport.has_meta("built") or viewport.get_meta("built") != key:  # only re-render when the island changes
		viewport.set_meta("built", key)
		var builder: IslandBuilder = viewport.get_child(0).get_node("Island")
		builder.build(layout)
		viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	return viewport.get_texture()


func _on_list(ok: bool, data: Dictionary) -> void:
	_listing = false
	if not ok:
		# Offline: we only know the island we're connected to.
		_order = [Net.island["id"]] if not Net.island.is_empty() else []
		_rebuild()
		return
	_order = data.get("islands", []).map(func(i): return str(i["id"]))
	for id in _order:
		if id == Net.island.get("id"):
			continue  # live already
		if not _fetching.get(id, false):
			_fetching[id] = true
			Net.request_json("/islands/%s" % id.uri_encode(), _on_island.bind(id))
	_rebuild()


func _on_island(ok: bool, data: Dictionary, id: String) -> void:
	_fetching.erase(id)
	if ok:
		_by_id[id] = data
		_rebuild()


func _rebuild() -> void:
	if not Net.island.is_empty():
		_by_id[str(Net.island["id"])] = Net.island
		if str(Net.island["id"]) not in _order:
			_order.append(str(Net.island["id"]))
	islands.clear()
	for id in _order:
		if _by_id.has(id):
			islands.append(_by_id[id])
	changed.emit()


func _make_thumbnail() -> SubViewport:
	var viewport := SubViewport.new()
	viewport.size = THUMB_SIZE
	viewport.transparent_bg = true
	viewport.own_world_3d = true  # its own little world, apart from the main island
	viewport.msaa_3d = Viewport.MSAA_4X

	var world: Node3D = IslandScene.instantiate()
	for part in ["Residents", "Water", "CameraRig"]:  # just the island and its buildings
		world.get_node(part).free()
	world.get_node("Island").detail = IslandBuilder.Detail.FAR
	var sky: WorldEnvironment = world.get_node("WorldEnvironment")
	var env: Environment = sky.environment.duplicate()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0, 0, 0, 0)  # see-through around the island
	env.glow_enabled = false
	env.ssao_enabled = false
	sky.environment = env

	# Looking down at 45°, like the main view. Orthographic: no perspective, so
	# the whole island fits neatly. `size` is how many units tall the view is.
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = 27.0
	camera.position = Vector3(0, 30, 31.5)
	camera.rotation_degrees = Vector3(-45, 0, 0)
	world.add_child(camera)

	viewport.add_child(world)
	add_child(viewport)
	camera.make_current()
	return viewport
