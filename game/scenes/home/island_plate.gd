class_name IslandPlate
extends Button
## An island's name plate on the Archipelago sea: name, status pill, and a sign
## floating over the island ("? 3" when residents are waiting on you, "z z"
## when everyone's napping). Click it to visit the island. Built in code; the
## home screen keeps it pinned to the island's shore every frame.

signal visit(island_id: String)

var island_id := ""
## The sign above the island. The home screen positions it.
var sign_panel := PanelContainer.new()

var _name := Label.new()
var _status := Label.new()
var _status_pill := PanelContainer.new()
var _sign_text := Label.new()


func _init() -> void:
	theme_type_variation = &"FlatIconButton"
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	focus_mode = Control.FOCUS_NONE
	pressed.connect(func(): visit.emit(island_id))

	var plate := PanelContainer.new()
	plate.theme_type_variation = &"PillWhite"
	plate.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 2)
	rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_name.theme_type_variation = &"CardName"
	_name.add_theme_font_size_override("font_size", 18)
	_name.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_status_pill.theme_type_variation = &"TagPill"
	_status_pill.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_status_pill.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_status.theme_type_variation = &"TagText"
	_status_pill.add_child(_status)
	rows.add_child(_name)
	rows.add_child(_status_pill)
	plate.add_child(rows)
	add_child(plate)
	# Size the button to the plate inside it.
	plate.minimum_size_changed.connect(func(): custom_minimum_size = plate.get_combined_minimum_size())

	sign_panel.theme_type_variation = &"SpeechPill"
	sign_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_sign_text.theme_type_variation = &"LabelStrong"
	_sign_text.add_theme_font_size_override("font_size", 16)
	sign_panel.add_child(_sign_text)


func setup(island: Dictionary) -> void:
	island_id = str(island["id"])
	tooltip_text = "Visit %s%s" % [island.get("name", ""), " (%s)" % island["repo"] if island.get("repo") else ""]
	_name.text = str(island.get("name", island_id))

	var status := Islands.status(island)
	_status.text = status["text"]
	_status.add_theme_color_override("font_color", status["color"])
	var pill: StyleBoxFlat = get_theme_stylebox("panel", "TagPill").duplicate()
	pill.bg_color = Color(status["color"]).lerp(Color.WHITE, 0.88)
	_status_pill.add_theme_stylebox_override("panel", pill)

	sign_panel.visible = status["kind"] in ["waiting", "napping"]
	_sign_text.text = "? %d" % island.get("bubbles", []).size() if status["kind"] == "waiting" else "z z"
	_sign_text.add_theme_color_override("font_color", status["color"])
	size = custom_minimum_size
