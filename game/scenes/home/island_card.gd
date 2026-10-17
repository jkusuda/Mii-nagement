class_name IslandCard
extends Button
## One island on the Archipelago screen: a picture of its 3D model, a name plate
## with its status, and a little sign above it ("? 3" when residents are waiting
## on you, "z z" when everyone's napping). Click it to visit the island.
## Built in code; the home screen makes one per island in Islands.islands.

signal visit(island_id: String)

var island_id := ""

var _picture := TextureRect.new()
var _name := Label.new()
var _status := Label.new()
var _status_pill := PanelContainer.new()
var _sign := PanelContainer.new()
var _sign_text := Label.new()


func _init() -> void:
	theme_type_variation = &"FlatIconButton"  # no button background; the picture is the button
	custom_minimum_size = Vector2(250, 190)
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	pressed.connect(func(): visit.emit(island_id))

	var rows := VBoxContainer.new()
	rows.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
	rows.add_theme_constant_override("separation", -14)  # the plate overlaps the island's shore
	add_child(rows)

	_picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_picture.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	rows.add_child(_picture)

	var plate := PanelContainer.new()
	plate.theme_type_variation = &"PillWhite"
	plate.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	plate.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var plate_rows := VBoxContainer.new()
	plate_rows.add_theme_constant_override("separation", 2)
	_name.theme_type_variation = &"CardName"
	_name.add_theme_font_size_override("font_size", 18)
	_name.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_status_pill.theme_type_variation = &"TagPill"
	_status_pill.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_status.theme_type_variation = &"TagText"
	_status_pill.add_child(_status)
	plate_rows.add_child(_name)
	plate_rows.add_child(_status_pill)
	plate.add_child(plate_rows)
	rows.add_child(plate)

	# The sign floats over the top-right of the picture.
	_sign.theme_type_variation = &"SpeechPill"
	_sign.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_sign.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	_sign.grow_horizontal = Control.GROW_DIRECTION_BEGIN  # grow leftward from the right edge
	_sign.offset_right = -16
	_sign.offset_top = 6
	_sign_text.theme_type_variation = &"LabelStrong"
	_sign_text.add_theme_font_size_override("font_size", 16)
	_sign.add_child(_sign_text)
	add_child(_sign)


func setup(island: Dictionary) -> void:
	island_id = str(island["id"])
	tooltip_text = "Visit %s%s" % [island.get("name", ""), " (%s)" % island["repo"] if island.get("repo") else ""]
	_picture.texture = Islands.thumbnail(island_id)
	_name.text = str(island.get("name", island_id))

	var status := Islands.status(island)
	_status.text = status["text"]
	_status.add_theme_color_override("font_color", status["color"])
	var pill: StyleBoxFlat = get_theme_stylebox("panel", "TagPill").duplicate()
	pill.bg_color = Color(status["color"]).lerp(Color.WHITE, 0.88)
	_status_pill.add_theme_stylebox_override("panel", pill)

	_sign.visible = status["kind"] in ["waiting", "napping"]
	if status["kind"] == "waiting":
		_sign_text.text = "? %d" % island.get("bubbles", []).size()
		_sign_text.add_theme_color_override("font_color", status["color"])
	else:
		_sign_text.text = "z z"
		_sign_text.add_theme_color_override("font_color", status["color"])
	_picture.modulate = Color(0.85, 0.88, 0.95) if status["kind"] == "napping" else Color.WHITE
