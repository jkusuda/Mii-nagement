class_name IslandDropdown
extends PopupPanel
## The island switcher that drops down from the island name in the top bar.
## Lists every island (Islands autoload) with its picture and status; click one
## to go there, or "Archipelago" for the home screen.
##
## Godot concept: a PopupPanel is a small window that closes by itself when you
## click outside it. popup() shows it at a rectangle on screen.

signal visit(island_id: String)
signal go_home

const CHECK := preload("res://assets/icons/check.svg")
const MAP := preload("res://assets/icons/map.svg")
const PLUS := preload("res://assets/icons/plus.svg")

var _rows := VBoxContainer.new()
var _summary := Label.new()


func _init() -> void:
	# A popup is its own window, so it doesn't inherit the game's theme: set it here.
	theme = preload("res://ui/theme.tres")
	var box := VBoxContainer.new()
	box.custom_minimum_size.x = 320
	box.add_theme_constant_override("separation", 10)
	add_child(box)

	var header := HBoxContainer.new()
	var title := Label.new()
	title.text = "Your islands"
	title.theme_type_variation = &"SectionTitle"
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_summary.theme_type_variation = &"LabelMuted"
	_summary.add_theme_font_size_override("font_size", 12)
	header.add_child(title)
	header.add_child(_summary)
	box.add_child(header)

	_rows.add_theme_constant_override("separation", 4)
	box.add_child(_rows)

	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 8)
	var new_island := _button("New island", PLUS, &"SelectorButton")
	new_island.disabled = true
	new_island.tooltip_text = "Island creation is coming next"
	var home := _button("Archipelago", MAP, &"OutlineButton")
	home.pressed.connect(func():
		hide()
		go_home.emit())
	buttons.add_child(new_island)
	buttons.add_child(home)
	box.add_child(buttons)

	Islands.changed.connect(func(): if visible: _fill())


## Open just below `anchor` (the island name pill).
func open_below(anchor: Control) -> void:
	Islands.refresh()
	_fill()
	var at := anchor.get_global_rect()
	popup(Rect2i(Vector2i(at.position + Vector2(0, at.size.y + 6)), Vector2i(get_contents_minimum_size())))


func _fill() -> void:
	for child in _rows.get_children():
		_rows.remove_child(child)  # out now, so it doesn't count toward the popup's size
		child.queue_free()
	_summary.text = "%d island%s · %d residents" % [Islands.islands.size(),
		"" if Islands.islands.size() == 1 else "s", Islands.resident_count()]
	for island in Islands.islands:
		_rows.add_child(_row(island))
	size = Vector2i(get_contents_minimum_size())  # shrink-wrap to the rows


func _row(island: Dictionary) -> Control:
	var id := str(island["id"])
	var here: bool = id == Net.island.get("id")
	var row := Button.new()
	row.theme_type_variation = &"IslandRowHere" if here else &"IslandRow"
	row.custom_minimum_size.y = 58
	row.focus_mode = Control.FOCUS_NONE
	row.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	row.pressed.connect(func():
		hide()
		visit.emit(id))

	var line := HBoxContainer.new()
	line.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	line.offset_left = 10
	line.offset_right = -10
	line.mouse_filter = Control.MOUSE_FILTER_IGNORE
	line.add_theme_constant_override("separation", 10)
	row.add_child(line)

	var picture := TextureRect.new()
	picture.texture = Islands.thumbnail(id)
	picture.custom_minimum_size = Vector2(60, 40)
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE

	var names := VBoxContainer.new()
	names.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	names.alignment = BoxContainer.ALIGNMENT_CENTER
	names.add_theme_constant_override("separation", -2)
	names.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var name_label := _label(str(island.get("name", id)), 15, true)
	var repo := _label(str(island["repo"]) if island.get("repo") else "no repo linked", 11, false)
	names.add_child(name_label)
	names.add_child(repo)

	var status := Islands.status(island)
	var tag := HBoxContainer.new()
	tag.add_theme_constant_override("separation", 4)
	tag.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if here:
		var check := TextureRect.new()
		check.texture = CHECK
		check.custom_minimum_size = Vector2(12, 12)
		check.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		check.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		check.modulate = Color("2E8B57")
		check.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		tag.add_child(check)
		var label := _label("You're here", 11, true)
		label.add_theme_color_override("font_color", Color("2E8B57"))
		tag.add_child(label)
	else:
		var label := _label(_short_status(island, status), 11, true)
		label.add_theme_color_override("font_color", status["color"])
		tag.add_child(label)

	for part in [picture, names, tag]:
		part.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		line.add_child(part)
	return row


## "2 need you" / "Busy · 4 working" / "Napping": short enough for the row.
func _short_status(island: Dictionary, status: Dictionary) -> String:
	if status["kind"] == "waiting":
		return "%d need you" % island.get("bubbles", []).size()
	if status["kind"] == "napping":
		return "Napping"
	return status["text"]


func _button(text: String, icon: Texture2D, style: StringName) -> Button:
	var button := Button.new()
	button.text = text
	button.icon = icon
	button.theme_type_variation = style
	button.add_theme_constant_override("icon_max_width", 14)
	button.custom_minimum_size.y = 36
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.focus_mode = Control.FOCUS_NONE
	return button


func _label(text: String, size: int, strong: bool) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"LabelStrong" if strong else &"LabelMuted"
	label.add_theme_font_size_override("font_size", size)
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return label
