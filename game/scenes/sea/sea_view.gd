class_name SeaView
extends SubViewportContainer
## A 3D sea with one or more islands in it, using the island view's own water
## shader, sun, shadows, and sky light (it borrows them from island_3d.tscn).
## The Archipelago screen shows every island in one; the island creator shows
## a preview of the new island.
##
## show_islands() lays the islands out in a grid and points the camera so they
## all fit. Each island shows only the building types it's given.
## screen_point() says where a 3D point is on screen, for name plates.
## Clicking an island emits island_clicked.

signal island_clicked(island_id: String)

const IslandScene := preload("res://scenes/island_3d.tscn")
const SPACING := Vector2(50, 38)           # distance between island centers (x, z)
const ISLAND_HALF := Vector2(18.6, 12.4)   # the island's size, as in water.gdshader
const MAX_ISLANDS := 8                      # the water shader knows up to 8 shores
## The nodes in island_3d.tscn that make up one island (everything else is
## the shared stage: sun, sky, water). Add new island decorations here.
const ISLAND_PARTS := ["BaseIsland", "Pond", "Trees", "Bushes", "Buildings"]

var _viewport := SubViewport.new()
var _stage: Node3D                # island_3d.tscn, minus its own island, residents and camera
var _camera := Camera3D.new()
var _islands := {}                # id -> Node3D (base island + buildings)
var _centers := {}                # id or "slot:N" -> Vector3
var _layout_key := ""


func _init() -> void:
	stretch = true
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	_viewport.own_world_3d = true  # its own world, apart from the island view
	_viewport.msaa_3d = Viewport.MSAA_2X
	_viewport.handle_input_locally = false
	add_child(_viewport)

	_stage = IslandScene.instantiate()
	for part in ["Residents", "CameraRig"] + ISLAND_PARTS:
		_stage.get_node(part).free()  # we add our own islands and camera
	var sun: DirectionalLight3D = _stage.get_node("Sun")
	sun.directional_shadow_max_distance = 300.0  # the sea view is further away than the island view
	_viewport.add_child(_stage)

	_camera.fov = 30.0
	_camera.far = 800.0
	_camera.rotation_degrees = Vector3(-45, 0, 0)
	_stage.add_child(_camera)


func _ready() -> void:
	_camera.make_current()
	# Deferred: the SubViewport takes this control's new size a moment later.
	resized.connect(func(): _frame_camera.call_deferred())


## Show these islands: each entry is {"id": String, "types": [building types]}.
## With `new_slot`, one more empty grid cell is kept (for a "New island" sign).
func show_islands(entries: Array, new_slot := false) -> void:
	var key := JSON.stringify([entries, new_slot])
	if key == _layout_key:
		return
	_layout_key = key
	for node in _islands.values():
		node.queue_free()
	_islands.clear()
	_centers.clear()

	var cells := mini(entries.size(), MAX_ISLANDS) + (1 if new_slot else 0)
	var columns := 1 if cells <= 1 else (2 if cells <= 4 else 3)
	var rows := ceili(cells / float(columns))
	for i in cells:
		var col := i % columns
		var row := i / columns
		var center := Vector3((col - (columns - 1) / 2.0) * SPACING.x, 0, (row - (rows - 1) / 2.0) * SPACING.y)
		if i < entries.size():
			var id := str(entries[i]["id"])
			_islands[id] = _make_island(entries[i].get("types", []), center)
			_centers[id] = center
		else:
			_centers["slot:new"] = center

	_set_water()
	_frame_camera()


## Where a point (relative to an island's center, or to the new-island slot with
## "slot:new") is on screen, in this control's coordinates.
func screen_point(id: String, offset := Vector3.ZERO) -> Vector2:
	if not _centers.has(id):
		return Vector2(-1000, -1000)
	return _camera.unproject_position(_centers[id] + offset) * _scale()


func has_island(id: String) -> bool:
	return _islands.has(id)


## One island: the base model, pond and plants, plus only the buildings it has.
func _make_island(types: Array, center: Vector3) -> Node3D:
	var island := Node3D.new()
	island.position = center
	var source := IslandScene.instantiate()
	for part in ISLAND_PARTS:
		var node: Node3D = source.get_node(part)
		source.remove_child(node)
		node.owner = null  # it belonged to the scene we took it from
		island.add_child(node)
	source.free()
	var wanted := types.map(func(t): return str(t).to_pascal_case())
	for building in island.get_node("Buildings").get_children():
		building.visible = str(building.name) in wanted
	_stage.add_child(island)
	return island


## Tell the water shader where the shores are, so each island gets its shallows and foam.
func _set_water() -> void:
	var water: MeshInstance3D = _stage.get_node("Water")
	if not water.has_meta("base_material"):  # remember the scene's material once; we tweak copies
		water.set_meta("base_material", water.get_active_material(0))
		water.scale = Vector3(3, 1, 3)  # a wider sea for wider archipelagos
	var material: ShaderMaterial = water.get_meta("base_material").duplicate()
	var centers := PackedVector2Array()
	for id in _islands:
		centers.append(Vector2(_centers[id].x, _centers[id].z))
	# -1 tells the shader "no islands" (0 would mean one island in the middle).
	material.set_shader_parameter("island_count", centers.size() if centers.size() > 0 else -1)
	material.set_shader_parameter("island_centers", centers)
	water.material_override = material


## Point the camera (tilted 45° like the island view) so every cell fits.
func _frame_camera() -> void:
	if _centers.is_empty() or size.x <= 0 or size.y <= 0:
		return
	var low := Vector2(INF, INF)
	var high := Vector2(-INF, -INF)
	for c in _centers.values():
		low = Vector2(minf(low.x, c.x), minf(low.y, c.z))
		high = Vector2(maxf(high.x, c.x), maxf(high.y, c.z))
	var width := high.x - low.x + ISLAND_HALF.x * 2.0 + 4.0
	var depth := high.y - low.y + ISLAND_HALF.y * 2.0 + 4.0
	var half_tan := tan(deg_to_rad(_camera.fov / 2.0))
	var aspect := size.x / size.y
	# Seen at 45°, depth shrinks to ~0.7; buildings add a little height.
	var fit_height := (depth * 0.71 + 6.0) / (2.0 * half_tan)
	var fit_width := width / (2.0 * half_tan * aspect)
	var distance := maxf(fit_height, fit_width)
	var middle := Vector3((low.x + high.x) / 2.0, 0, (low.y + high.y) / 2.0 + 1.5)
	# That's an estimate: perspective makes the nearer row bigger. Back off until
	# every island's corners (and roofs) are on screen, with some room to spare.
	var view := Vector2(_viewport.size) if _viewport.size.x > 0 else size
	var pad := view * Vector2(0.03, 0.06)
	for attempt in 30:
		_camera.position = middle + Vector3(0, distance * 0.7071, distance * 0.7071)
		if _all_on_screen(view, pad):
			break
		distance *= 1.06


func _all_on_screen(view: Vector2, pad: Vector2) -> bool:
	for c in _centers.values():
		for corner in [Vector3(-1, 0, -1), Vector3(1, 0, -1), Vector3(-1, 0, 1), Vector3(1, 0, 1)]:
			for height in [0.0, 5.0]:
				var p := _camera.unproject_position(c + Vector3(corner.x * ISLAND_HALF.x, height, corner.z * ISLAND_HALF.y))
				if p.x < pad.x or p.y < pad.y or p.x > view.x - pad.x or p.y > view.y - pad.y:
					return false
	return true


## The SubViewport can be smaller than this control (stretch_shrink); convert.
func _scale() -> Vector2:
	return size / Vector2(_viewport.size) if _viewport.size.x > 0 else Vector2.ONE


func _gui_input(event: InputEvent) -> void:
	if not (event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT):
		return
	# Shoot a ray from the camera through the mouse and see where it meets the sea (y = 0).
	var at: Vector2 = event.position / _scale()
	var from := _camera.project_ray_origin(at)
	var direction := _camera.project_ray_normal(at)
	if absf(direction.y) < 0.001:
		return
	var hit := from + direction * (-from.y / direction.y)
	for id in _islands:
		var local: Vector3 = hit - _centers[id]
		if absf(local.x) <= ISLAND_HALF.x and absf(local.z) <= ISLAND_HALF.y:
			island_clicked.emit(id)
			return
