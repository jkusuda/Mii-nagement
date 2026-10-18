class_name BuildMode
extends Node3D
## Build mode: rearrange the island. Drag buildings around (the paths follow),
## add or remove land, plant trees and bushes, pick which workflow steps the
## island has, size the pond, and choose the season.
##
## Everything happens on a *draft* copy of the island's layout, rebuilt live so
## you see each change. Save sends it to the backend (PUT /islands/{id}/layout,
## see docs/protocol.md "Island map"), which checks it again and tells every
## client. Cancel puts the island back the way it was.
##
## This node lives in the 3D world (island_3d.tscn, added by game.gd), so it
## gets the mouse clicks that land on the island; its toolbar lives in the 2D
## island frame. The Build button in the bottom nav opens it.
##
## Godot concepts:
## - Ray casting: to find which tile is under the mouse, we shoot a ray from
##   the camera through the mouse position (project_ray_origin / _normal) and see
##   where it meets the ground (y = 0).
## - _unhandled_input() gets clicks no UI element used. This node is added after
##   the camera rig, so it sees them first, and set_input_as_handled() keeps the
##   rig from also panning on a left click (or zooming while you turn a building
##   with the mouse wheel).
## - The green/red "ghosts" are meshes with a see-through material: a box over a
##   lot, or a see-through copy of the plant you're about to place.
## - The Land tool's yellow squares are one MultiMeshInstance3D: a single mesh
##   drawn many times (once per selected tile), which is much cheaper than one
##   node per tile.

signal opened
signal closed

enum Tool { MOVE, LAND, PLANTS, BUILDINGS, POND, SEASON }
## [name, icon] for each tool, in Tool order.
const TOOLS := [
	["Move", preload("res://assets/icons/mouse-pointer-2.svg")],
	["Land", preload("res://assets/icons/map.svg")],
	["Plants", preload("res://assets/icons/sprout.svg")],
	["Buildings", preload("res://assets/icons/map-pin.svg")],
	["Pond", preload("res://assets/icons/globe.svg")],
	["Season", preload("res://assets/icons/sun.svg")],
]
const TOOL_HINTS := [
	"Drag a building or the pond to move it. While carrying a building, press R or scroll to turn it.",
	"Drag over the map to select tiles (hold Shift to select more), then Add or Remove land.",
	"Click to plant where the marker is green. Auto-arrange lets the island decide.",
	"Pick the workflow steps this island has. Mailbox, Workshop, and Post Office are always needed.",
	"Pick a pond size (or none). Move it with the Move tool.",
	"Pick the island's season.",
]
const POND_SIZES := {"None": Vector2i.ZERO, "Small": Vector2i(2, 2), "Medium": Vector2i(3, 2), "Large": Vector2i(5, 2)}
const SEASON_NAMES := {"spring": "Spring", "summer": "Summer", "autumn": "Autumn", "winter": "Winter"}
const INFO_ICON := preload("res://assets/icons/circle-help.svg")
const PLUS_ICON := preload("res://assets/icons/plus.svg")
const MINUS_ICON := preload("res://assets/icons/minus.svg")
const CLEAR_ICON := preload("res://assets/icons/x.svg")
const INK := Color("292E50")  # the theme's dark text color
const PROBLEM := Color("D9453B")  # red, for "that won't work"
const OPTIONS_BG := Color("FFF4D6")  # the soft yellow strip behind a tool's options

var island: IslandBuilder
var rig: CameraRig
var draft: IslandLayout
var is_open := false

var _original: IslandLayout
var _tool := Tool.MOVE
var _panel: PanelContainer
var _tool_buttons: Array[Button] = []
var _options_title: Label
var _options: HBoxContainer
var _status_icon: TextureRect
var _status: Label
var _save: Button
var _ghost: MeshInstance3D
var _ok_material := StandardMaterial3D.new()
var _bad_material := StandardMaterial3D.new()
var _saving := false

# Move tool
var _dragging := ""  # building type being dragged, "pond", or ""
var _grab := Vector2i.ZERO  # which tile of the lot the mouse holds
var _drag_spec := {}  # where the dragged thing would go now
var _last_moved := ""  # the building R turns
var _mouse := Vector2.ZERO  # the last mouse position over the island
# Land tool
var _selected := {}  # Vector2i -> true: the tiles Add / Remove land will change
var _select_from := Vector2i(-1, -1)  # where the selection drag started (-1 = not dragging)
var _select_before := {}  # the selection when the drag started (Shift adds to it)
var _selection_marks: MultiMeshInstance3D
var _select_material := StandardMaterial3D.new()
var _land_buttons: Array[Button] = []  # Add, Remove, Clear: off while nothing is selected
# Plants tool
var _plant_kind := "tree"  # tree | bush | remove
var _plant_preview: Node3D  # a see-through tree or bush under the mouse
var _plant_preview_kind := ""
var _plant_ring: MeshInstance3D  # the green/red disc it would stand on
var _preview_ok := StandardMaterial3D.new()
var _preview_bad := StandardMaterial3D.new()


## Called once by game.gd: the island to edit, the camera, and the 2D frame
## the toolbar goes in.
func setup(island_builder: IslandBuilder, camera_rig: CameraRig, frame: Control) -> void:
	island = island_builder
	rig = camera_rig

	for m in [_ok_material, _bad_material]:
		m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		m.no_depth_test = true  # always visible, even "inside" a building
	_ok_material.albedo_color = Color(0.3, 0.95, 0.5, 0.35)
	_bad_material.albedo_color = Color(1.0, 0.3, 0.3, 0.4)
	_ghost = MeshInstance3D.new()
	_ghost.mesh = BoxMesh.new()
	_ghost.visible = false
	add_child(_ghost)

	# The plant marker: a see-through plant (lit, so it looks like a plant) on a disc.
	for m in [_preview_ok, _preview_bad]:
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	_preview_ok.albedo_color = Color(0.55, 1.0, 0.6, 0.5)
	_preview_bad.albedo_color = Color(1.0, 0.45, 0.4, 0.5)
	_plant_ring = MeshInstance3D.new()
	var disc := CylinderMesh.new()
	disc.height = 0.06
	disc.radial_segments = 32
	_plant_ring.mesh = disc
	_plant_ring.visible = false
	add_child(_plant_ring)

	# The Land tool's selection: one flat yellow square per selected tile.
	_select_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_select_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	_select_material.no_depth_test = true
	_select_material.albedo_color = Color(1.0, 0.82, 0.1, 0.75)
	var square := BoxMesh.new()
	square.size = Vector3(IslandLayout.TILE * 0.86, 0.1, IslandLayout.TILE * 0.86)
	square.material = _select_material
	_selection_marks = MultiMeshInstance3D.new()
	_selection_marks.multimesh = MultiMesh.new()
	_selection_marks.multimesh.transform_format = MultiMesh.TRANSFORM_3D
	_selection_marks.multimesh.mesh = square
	add_child(_selection_marks)

	_build_toolbar(frame)


# --- Opening and closing ----------------------------------------------------------


func open() -> void:
	if is_open or island.layout == null:
		return
	is_open = true
	_original = island.layout.copy()
	draft = island.layout.copy()
	rig.left_drag_pans = false
	_panel.visible = true
	_select_tool(Tool.MOVE)
	opened.emit()


func cancel() -> void:
	if not is_open:
		return
	island.build(_original)
	_close()


func _close() -> void:
	is_open = false
	_dragging = ""
	_clear_selection()
	_hide_markers()
	_panel.visible = false
	rig.left_drag_pans = true
	closed.emit()


func _on_save_pressed() -> void:
	if draft.problem() != "" or _saving:
		return
	var id := str(Net.island.get("id", ""))
	if id == "" or not Net.is_online:
		_close()  # offline: keep the change on screen only
		return
	_saving = true
	_refresh_status()
	Net.request_json("/islands/%s/layout" % id.uri_encode(), _on_saved, HTTPClient.METHOD_PUT, draft.to_request())


func _on_saved(ok: bool, data: Dictionary) -> void:
	_saving = false
	if not is_open:
		return  # cancelled meanwhile
	if ok:
		_close()  # the server also sends everyone the new snapshot
		return
	_refresh_status("Couldn't save: %s" % data.get("error", "the backend didn't answer."))


# --- The toolbar --------------------------------------------------------------------
#
#   [Move] [Land] [Plants] [Buildings] [Pond] [Season]     <- tools (with icons)
#   ┌ Plants:  [Tree] [Bush] [Remove]  [Auto-arrange] ┐    <- this tool's options
#   (i) Click to plant where the marker is green.   Cancel  [Save]
#
# Godot concept: UI can be built in code, like here, instead of in a .tscn:
# make the nodes, set their properties, add them as children.


func _build_toolbar(frame: Control) -> void:
	_panel = PanelContainer.new()
	_panel.theme_type_variation = &"PillWhite"
	_panel.visible = false
	frame.add_child(_panel)
	# Pin it to the bottom middle of the island frame (the back row of
	# buildings, at the top, stays in view), growing upward.
	_panel.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	_panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_panel.offset_bottom = -14

	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 8)
	_panel.add_child(rows)

	# 1. Tools: bigger buttons with an icon each; the chosen one is yellow.
	var tools := HBoxContainer.new()
	tools.alignment = BoxContainer.ALIGNMENT_CENTER
	tools.add_theme_constant_override("separation", 6)
	rows.add_child(tools)
	for i in TOOLS.size():
		var button := _button(TOOLS[i][0], func(): _select_tool(i))
		button.icon = TOOLS[i][1]
		button.custom_minimum_size = Vector2(0, 40)
		button.add_theme_color_override("icon_normal_color", INK)
		button.add_theme_color_override("icon_hover_color", INK)
		button.add_theme_color_override("icon_pressed_color", INK)
		button.add_theme_constant_override("icon_max_width", 18)
		tools.add_child(button)
		_tool_buttons.append(button)

	# 2. The chosen tool's options, on a soft yellow strip with the tool's name.
	var strip := PanelContainer.new()
	var strip_style := StyleBoxFlat.new()
	strip_style.bg_color = OPTIONS_BG
	strip_style.set_corner_radius_all(12)
	strip_style.content_margin_left = 12
	strip_style.content_margin_right = 12
	strip_style.content_margin_top = 6
	strip_style.content_margin_bottom = 6
	strip.add_theme_stylebox_override("panel", strip_style)
	rows.add_child(strip)
	var strip_row := HBoxContainer.new()
	strip_row.add_theme_constant_override("separation", 10)
	strip.add_child(strip_row)
	_options_title = Label.new()
	_options_title.theme_type_variation = &"LabelStrong"
	strip_row.add_child(_options_title)
	_options = HBoxContainer.new()
	_options.add_theme_constant_override("separation", 6)
	strip_row.add_child(_options)

	# 3. A hint (or what's wrong, in red), then Cancel and Save.
	var bottom := HBoxContainer.new()
	bottom.add_theme_constant_override("separation", 8)
	rows.add_child(bottom)
	_status_icon = TextureRect.new()
	_status_icon.texture = INFO_ICON
	_status_icon.custom_minimum_size = Vector2(16, 16)
	_status_icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_status_icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_status_icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	bottom.add_child(_status_icon)
	_status = Label.new()
	_status.theme_type_variation = &"LabelMuted"
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status.custom_minimum_size = Vector2(360, 0)
	_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bottom.add_child(_status)
	var cancel_button := _button("Cancel", cancel)
	cancel_button.theme_type_variation = &"OutlineButton"
	cancel_button.custom_minimum_size = Vector2(86, 38)
	bottom.add_child(cancel_button)
	_save = Button.new()
	_save.set_script(AnswerButton)  # the wavy orange button, like "Answer"
	_save.theme_type_variation = &"AnswerButton"
	_save.text = "Save"
	_save.custom_minimum_size = Vector2(96, 38)
	_save.focus_mode = Control.FOCUS_NONE
	_save.pressed.connect(_on_save_pressed)
	bottom.add_child(_save)


func _button(text: String, on_press: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.theme_type_variation = &"TabInactive"
	button.focus_mode = Control.FOCUS_NONE
	button.pressed.connect(on_press)
	return button


## An option inside the yellow strip: a chip that lights up when chosen.
func _option(text: String, on_press: Callable) -> Button:
	var button := _button(text, on_press)
	button.theme_type_variation = &"FilterChip"
	return button


## Highlight one button of a row and dim the others. Tools use the yellow tab
## look; options use the chip look.
func _highlight(buttons: Array, chosen: int, active := &"FilterChipActive", inactive := &"FilterChip") -> void:
	for i in buttons.size():
		buttons[i].theme_type_variation = active if i == chosen else inactive


func _select_tool(which: Tool) -> void:
	_tool = which
	_highlight(_tool_buttons, which, &"TabActive", &"OutlineButton")
	_dragging = ""
	if which != Tool.LAND:
		_clear_selection()
	_hide_markers()
	_options_title.text = "%s:" % TOOLS[which][0]
	for child in _options.get_children():
		child.queue_free()
	match which:
		Tool.MOVE:
			_options.add_child(_option("Turn the last one", _turn_last))
		Tool.LAND:
			_land_buttons = [
				_option("Add land", func(): _change_land(true)),
				_option("Remove land", func(): _change_land(false)),
				_option("Clear", _clear_selection),
			]
			var icons := [PLUS_ICON, MINUS_ICON, CLEAR_ICON]
			for i in _land_buttons.size():
				var button := _land_buttons[i]
				button.icon = icons[i]
				button.add_theme_constant_override("icon_max_width", 14)
				for state in ["icon_normal_color", "icon_hover_color", "icon_pressed_color", "icon_disabled_color"]:
					button.add_theme_color_override(state, INK)
				_options.add_child(button)
			_refresh_land_buttons()
		Tool.PLANTS:
			var kinds := ["tree", "bush", "remove"]
			var row: Array[Button] = []
			for kind in kinds:
				var button := _option(kind.capitalize(), func(): _set_plant_kind(kind))
				_options.add_child(button)
				row.append(button)
			_highlight(row, kinds.find(_plant_kind))
			_options.add_child(VSeparator.new())
			_options.add_child(_option("Auto-arrange", _auto_arrange))
		Tool.BUILDINGS:
			_show_step_toggles()
		Tool.POND:
			var row: Array[Button] = []
			var current := Vector2i(draft.pond.get("w", 0), draft.pond.get("h", 0))
			var chosen := -1
			for name in POND_SIZES:
				var button := _option(name, func(): _set_pond_size(POND_SIZES[name]))
				_options.add_child(button)
				row.append(button)
				if POND_SIZES[name] == current:
					chosen = row.size() - 1
			_highlight(row, chosen)
		Tool.SEASON:
			var row: Array[Button] = []
			for season in IslandLayout.SEASONS:
				var button := _option(SEASON_NAMES[season], func(): _set_season(season))
				_options.add_child(button)
				row.append(button)
			_highlight(row, IslandLayout.SEASONS.find(draft.season))
	_refresh_status()


## The line under the options: a hint, or what's wrong (red). Save only works
## when nothing is wrong.
func _refresh_status(message := "") -> void:
	var problem := draft.problem() if draft else ""
	var color := INK
	if _saving:
		_status.text = "Saving…"
	elif message != "":
		_status.text = message
		color = PROBLEM
	elif problem != "":
		_status.text = problem
		color = PROBLEM
	elif _tool == Tool.BUILDINGS and _task_on_road():
		_status.text = "A task is on the road: add or remove buildings once it's done."
	else:
		_status.text = TOOL_HINTS[_tool]
	_status.add_theme_color_override("font_color", color)
	_status_icon.modulate = color
	_save.disabled = problem != "" or _saving
	_save.modulate = Color(1, 1, 1, 0.45) if _save.disabled else Color.WHITE


## Rebuild the island from the draft (paths and plants follow).
func _rebuild() -> void:
	island.build(draft)
	_refresh_status()


# --- Mouse and keys -----------------------------------------------------------------


func _unhandled_input(event: InputEvent) -> void:
	if not is_open:
		return
	if event is InputEventMouseMotion:
		_mouse = event.position
		_on_hover(event.position)
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed:
			_on_press(event.position)
		else:
			_on_release()
		get_viewport().set_input_as_handled()
	elif (
		event is InputEventMouseButton
		and event.pressed
		and _carrying_building()
		and event.button_index in [MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]
	):
		_turn_carried(1 if event.button_index == MOUSE_BUTTON_WHEEL_UP else -1)
		get_viewport().set_input_as_handled()  # turn, don't zoom
	elif (
		event is InputEventKey
		and event.pressed
		and not event.echo
		and event.keycode == KEY_ESCAPE
		and not _selected.is_empty()
	):
		_clear_selection()
		get_viewport().set_input_as_handled()
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_R:
		if _carrying_building():
			_turn_carried(1)
		else:
			_turn_last()


## Where the mouse points on the ground (y = 0), in world space.
func _ground_point(screen: Vector2) -> Vector3:
	var camera := rig.camera
	var from := camera.project_ray_origin(screen)
	var direction := camera.project_ray_normal(screen)
	if absf(direction.y) < 0.0001:
		return Vector3.ZERO
	return from + direction * (-from.y / direction.y)


func _tile_at(screen: Vector2) -> Vector2i:
	return IslandLayout.world_to_tile(_ground_point(screen))


func _on_press(screen: Vector2) -> void:
	var tile := _tile_at(screen)
	match _tool:
		Tool.MOVE:
			_start_drag(tile)
		Tool.LAND:
			_select_before = _selected.duplicate() if Input.is_key_pressed(KEY_SHIFT) else {}
			_select_from = _clamp_to_editable(tile)
			_select_to(tile)
		Tool.PLANTS:
			_plant_at(IslandLayout.world_to_tile_units(_ground_point(screen)))
			_show_plant_marker(screen)


func _on_hover(screen: Vector2) -> void:
	var tile := _tile_at(screen)
	match _tool:
		Tool.MOVE:
			if _dragging != "":
				_drag_to(tile)
		Tool.LAND:
			if _select_from.x >= 0:
				_select_to(tile)
			_show_ghost(Rect2i(tile, Vector2i.ONE), _is_editable(tile))
		Tool.PLANTS:
			_show_plant_marker(screen)
		_:
			_hide_markers()


func _on_release() -> void:
	if _tool == Tool.MOVE and _dragging != "":
		_drop()
	if _tool == Tool.LAND:
		_select_from = Vector2i(-1, -1)


## Show the see-through box over a rectangle of tiles: green if `ok`, red if not.
func _show_ghost(tiles: Rect2i, ok: bool) -> void:
	var low := IslandLayout.tile_to_world(Vector2(tiles.position))
	var high := IslandLayout.tile_to_world(Vector2(tiles.end))
	var box := _ghost.mesh as BoxMesh
	box.size = Vector3(high.x - low.x, 0.25, high.z - low.z)
	_ghost.position = Vector3((low.x + high.x) / 2.0, 0.15, (low.z + high.z) / 2.0)
	_ghost.material_override = _ok_material if ok else _bad_material
	_ghost.visible = true


func _hide_markers() -> void:
	_ghost.visible = false
	_plant_ring.visible = false
	if _plant_preview:
		_plant_preview.visible = false


# --- Move -------------------------------------------------------------------------


func _start_drag(tile: Vector2i) -> void:
	for b in draft.buildings:
		if Rect2i(b["x"], b["y"], IslandLayout.LOT, IslandLayout.LOT).has_point(tile):
			_dragging = b["type"]
			_grab = tile - Vector2i(b["x"], b["y"])
			_drag_spec = b.duplicate()
			_last_moved = b["type"]
			_drag_to(tile)
			return
	var pond := draft.pond
	if not pond.is_empty() and Rect2i(pond["x"], pond["y"], pond["w"], pond["h"]).has_point(tile):
		_dragging = "pond"
		_grab = tile - Vector2i(pond["x"], pond["y"])
		_drag_spec = pond.duplicate()
		_drag_to(tile)


func _carrying_building() -> bool:
	return _tool == Tool.MOVE and _dragging != "" and _dragging != "pond"


func _drag_to(tile: Vector2i) -> void:
	var corner := tile - _grab
	_drag_spec["x"] = corner.x
	_drag_spec["y"] = corner.y
	if _dragging == "pond":
		var test := draft.copy()
		var ok := test.move_pond(_drag_spec.duplicate())
		_show_ghost(Rect2i(corner, Vector2i(_drag_spec["w"], _drag_spec["h"])), ok)
		return
	# Carry the building along with the mouse (turned the way it will face),
	# with its lot shown underneath.
	var node := island.building_node(_dragging)
	node.position = IslandLayout.lot_center(_drag_spec)
	node.rotation = Vector3(0, IslandLayout.facing_angle(_drag_spec), 0)
	_show_ghost(Rect2i(corner, Vector2i(IslandLayout.LOT, IslandLayout.LOT)), draft.can_place(_drag_spec))


## Turn the building you're carrying a quarter turn (+1 or -1). Its lot is
## square, so it stays under the mouse; whether it fits shows on the ghost.
func _turn_carried(direction: int) -> void:
	_drag_spec["facing"] = posmod(int(_drag_spec["facing"]) + direction, 4)
	_drag_to(_tile_at(_mouse))


func _drop() -> void:
	var what := _dragging
	_dragging = ""
	_ghost.visible = false
	if what == "pond":
		if draft.move_pond(_drag_spec.duplicate()):
			_rebuild()
		else:
			_refresh_status("The pond doesn't fit there.")
		return
	if draft.can_place(_drag_spec):
		_replace_building(_drag_spec)
		_rebuild()
	else:
		island.build(draft)  # put it back
		_refresh_status("The %s doesn't fit there." % IslandLayout.STEP_NAMES[what])


## Turn the last building you moved a quarter turn (or more, if it doesn't fit).
func _turn_last() -> void:
	var b := draft.building(_last_moved)
	if b.is_empty():
		b = draft.buildings[0] if not draft.buildings.is_empty() else {}
		if b.is_empty():
			return
		_last_moved = b["type"]
	for turn in range(1, 4):
		var spec := b.duplicate()
		spec["facing"] = (int(b["facing"]) + turn) % 4
		if draft.can_place(spec):
			_replace_building(spec)
			_rebuild()
			return
	_refresh_status("The %s has no room to turn there." % IslandLayout.STEP_NAMES[b["type"]])


func _replace_building(spec: Dictionary) -> void:
	for i in draft.buildings.size():
		if draft.buildings[i]["type"] == spec["type"]:
			draft.buildings[i] = spec.duplicate()


# --- Land ---------------------------------------------------------------------------


## Tiles that can change: everything but the grid's outer ring (it stays sea).
func _is_editable(tile: Vector2i) -> bool:
	return tile.x >= 1 and tile.y >= 1 and tile.x <= IslandLayout.GRID_W - 2 and tile.y <= IslandLayout.GRID_H - 2


func _clamp_to_editable(tile: Vector2i) -> Vector2i:
	return tile.clamp(Vector2i.ONE, Vector2i(IslandLayout.GRID_W - 2, IslandLayout.GRID_H - 2))


## While dragging: select the rectangle from where the drag started to `tile`
## (plus what was selected before, when Shift was held).
func _select_to(tile: Vector2i) -> void:
	var corner := _clamp_to_editable(tile)
	var low := _select_from.min(corner)
	var high := _select_from.max(corner)
	_selected = _select_before.duplicate()
	for y in range(low.y, high.y + 1):
		for x in range(low.x, high.x + 1):
			_selected[Vector2i(x, y)] = true
	_show_selection()


func _clear_selection() -> void:
	_selected.clear()
	_select_from = Vector2i(-1, -1)
	_show_selection()


## Put a yellow square on every selected tile, and say how many there are.
func _show_selection() -> void:
	var multimesh := _selection_marks.multimesh
	multimesh.instance_count = _selected.size()
	var i := 0
	for tile: Vector2i in _selected:
		var middle := IslandLayout.tile_to_world(Vector2(tile) + Vector2(0.5, 0.5))
		multimesh.set_instance_transform(i, Transform3D(Basis.IDENTITY, middle + Vector3(0, 0.12, 0)))
		i += 1
	_refresh_land_buttons()
	if _tool == Tool.LAND and is_open:
		if _selected.is_empty():
			_refresh_status()
		else:
			var land := _selected.keys().filter(func(t): return draft.is_land(t)).size()
			_status.text = (
				"%d selected: %d land, %d sea. Add or Remove land, or Esc to clear."
				% [_selected.size(), land, _selected.size() - land]
			)


func _refresh_land_buttons() -> void:
	for button in _land_buttons:
		if is_instance_valid(button):
			button.disabled = _selected.is_empty()


## Add or remove land on every selected tile. Tiles that would break the island
## (a building or the pond standing there, land cut in two, ...) are skipped.
func _change_land(on: bool) -> void:
	var tiles: Array = _selected.keys().filter(func(t): return draft.is_land(t) != on)
	# Remove from the outside in, add from the inside out: that way each step
	# keeps the island in one piece as long as possible.
	var middle := Vector2(IslandLayout.GRID_W, IslandLayout.GRID_H) / 2.0
	tiles.sort_custom(
		func(a, b):
			var da := Vector2(a).distance_squared_to(middle)
			var db := Vector2(b).distance_squared_to(middle)
			return da > db if not on else da < db
	)
	var skipped := 0
	var reason := ""
	for tile: Vector2i in tiles:
		draft.set_land(tile, on)
		var problem := draft.problem()
		if problem != "":
			draft.set_land(tile, not on)  # undo: that would break the island
			skipped += 1
			reason = problem
	_clear_selection()
	_rebuild()
	if skipped > 0:
		_refresh_status("%d tile%s stayed as they were: %s" % [skipped, "" if skipped == 1 else "s", reason])


# --- Plants -------------------------------------------------------------------------


func _set_plant_kind(kind: String) -> void:
	_plant_kind = kind
	_select_tool(Tool.PLANTS)


## The plants as they are now: the hand-placed list, or the auto-arranged ones.
func _current_plants() -> Array:
	return draft.plants if draft.plants is Array else draft.shown_plants


## Hand-placing starts from what's there now (the auto-arranged plants).
func _own_plants() -> Array:
	if draft.plants == null:
		draft.plants = draft.shown_plants.duplicate(true)
	return draft.plants


## The plant nearest a point (tile units), within one tile; -1 if none.
func _plant_near(at: Vector2, list: Array) -> int:
	var nearest := -1
	var best := 1.0
	for i in list.size():
		var d := at.distance_to(Vector2(list[i]["x"], list[i]["y"]))
		if d < best:
			best = d
			nearest = i
	return nearest


func _plant_at(at: Vector2) -> void:
	if _plant_kind == "remove":
		var index := _plant_near(at, _current_plants())
		if index >= 0:
			_own_plants().remove_at(index)
			_rebuild()
		return
	var list := _current_plants()
	if list.size() >= 150:
		_refresh_status("That's a lot of plants! Remove some first.")
		return
	var plant := {"kind": _plant_kind, "x": at.x, "y": at.y, "rot": randf() * 360.0, "scale": randf_range(0.9, 1.1)}
	if not draft.plant_fits(plant, list):
		_refresh_status("No room for a %s there." % _plant_kind)
		return
	_own_plants().append(plant)
	_rebuild()


## The plant marker under the mouse: a see-through tree or bush standing on a
## green disc where it can go (red where it can't). In Remove mode, a red disc
## under the plant that a click would remove.
func _show_plant_marker(screen: Vector2) -> void:
	var at := IslandLayout.world_to_tile_units(_ground_point(screen))
	var list := _current_plants()
	if _plant_kind == "remove":
		if _plant_preview:
			_plant_preview.visible = false
		var index := _plant_near(at, list)
		if index < 0:
			_plant_ring.visible = false
			return
		var plant: Dictionary = list[index]
		_show_ring(Vector2(plant["x"], plant["y"]), plant["kind"], false)
		return
	var fits := draft.plant_fits({"kind": _plant_kind, "x": at.x, "y": at.y}, list)
	_show_ring(at, _plant_kind, fits)
	_show_plant_preview(at, fits)


func _show_ring(at: Vector2, kind: String, ok: bool) -> void:
	var disc := _plant_ring.mesh as CylinderMesh
	var radius := 1.4 if kind == "tree" else 1.0
	disc.top_radius = radius
	disc.bottom_radius = radius
	_plant_ring.position = IslandLayout.tile_to_world(at) + Vector3(0, 0.05, 0)
	_plant_ring.material_override = _ok_material if ok else _bad_material
	_plant_ring.visible = true


func _show_plant_preview(at: Vector2, ok: bool) -> void:
	if _plant_preview_kind != _plant_kind:
		if _plant_preview:
			_plant_preview.queue_free()
		var tree := _plant_kind == "tree"
		_plant_preview = (IslandBuilder.TreeScene if tree else IslandBuilder.BushScene).instantiate()
		var s := IslandBuilder.TREE_SCALE if tree else IslandBuilder.BUSH_SCALE
		_plant_preview.scale = Vector3(s, s, s)
		if _plant_preview.has_node("FallingLeaves"):
			_plant_preview.get_node("FallingLeaves").free()
		add_child(_plant_preview)
		_plant_preview_kind = _plant_kind
	var material := _preview_ok if ok else _preview_bad
	for mesh: MeshInstance3D in _plant_preview.find_children("*", "MeshInstance3D", true, false):
		mesh.material_override = material
	_plant_preview.position = IslandLayout.tile_to_world(at)
	_plant_preview.visible = true


func _auto_arrange() -> void:
	draft.plants = null
	_rebuild()


# --- Buildings ----------------------------------------------------------------------


func _task_on_road() -> bool:
	return Net.island.get("tasks", []).any(func(t): return t.get("status") == "in_progress")


func _show_step_toggles() -> void:
	var busy := _task_on_road()
	for type in IslandLayout.STEP_ORDER:
		var button := _option(IslandLayout.STEP_NAMES[type], func(): _toggle_step(type))
		button.theme_type_variation = &"FilterChipActive" if draft.has_building(type) else &"FilterChip"
		button.disabled = type in IslandLayout.REQUIRED_STEPS or busy
		if type in IslandLayout.REQUIRED_STEPS:
			button.tooltip_text = "Every island needs this one."
		_options.add_child(button)


func _toggle_step(type: String) -> void:
	if draft.has_building(type):
		draft.buildings = draft.buildings.filter(func(b): return b["type"] != type)
	else:
		var spot := draft.find_spot(type)
		if spot.is_empty():
			_refresh_status("There's no room for the %s. Add some land first." % IslandLayout.STEP_NAMES[type])
			return
		draft.buildings.append(spot)
		draft.buildings = IslandLayout.in_step_order(draft.buildings)
	_rebuild()
	_select_tool(Tool.BUILDINGS)


# --- Pond and season ----------------------------------------------------------------


func _set_pond_size(size: Vector2i) -> void:
	if size == Vector2i.ZERO:
		draft.pond = {}
		_rebuild()
		_select_tool(Tool.POND)
		return
	# Keep it where it is if it fits, else find the nearest spot that does.
	var middle := Vector2(9, 6)
	if not draft.pond.is_empty():
		middle = Vector2(draft.pond["x"] + draft.pond["w"] / 2.0, draft.pond["y"] + draft.pond["h"] / 2.0)
	var spots := []
	for y in range(1, IslandLayout.GRID_H - size.y):
		for x in range(1, IslandLayout.GRID_W - size.x):
			spots.append([Vector2(x + size.x / 2.0, y + size.y / 2.0).distance_squared_to(middle), x, y])
	spots.sort_custom(func(a, b): return a[0] < b[0])
	for spot in spots:
		if draft.move_pond({"x": spot[1], "y": spot[2], "w": size.x, "h": size.y}):
			_rebuild()
			_select_tool(Tool.POND)
			return
	_refresh_status("There's no room for a pond that size.")


func _set_season(season: String) -> void:
	draft.season = season
	_rebuild()
	_select_tool(Tool.SEASON)
