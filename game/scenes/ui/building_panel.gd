class_name BuildingPanel
extends Control
## The card that opens when you click a building on the island.
##
## - Mailbox: every open package ("Current issues") with where it is and who
##   carries it, and "Send a new issue" (PackageForm) to drop a new one.
## - Other buildings: the packages at that stage, the residents there and what
##   they're doing right now (their latest `activity`), and where packages go next.
##
## Everything comes from Net.island and redraws on Net.island_changed, so it
## stays live while open. game.gd opens it and zooms the camera in.
##
## Built in code. It covers the whole window: a light haze, and the card in the
## middle. Click the haze, the ✕, or press Esc to close.

signal closed
## A resident's "Answer" button: open that bubble's reply in the sidebar.
signal answer_requested(bubble_id: String)

const AnswerButtonScript := preload("res://scenes/ui/answer_button.gd")
const CLOSE_ICON := preload("res://assets/icons/x.svg")
const SEND_ICON := preload("res://assets/icons/plus.svg")
const CARD_WIDTH := 780.0
const LIST_HEIGHT := 330.0
const INK := Color("292E50")       # the theme's dark navy
const CREAM := Color("FFFDF3")
const LINE := Color("E3E5EE")
const STATES := {"working": "Working", "walking": "Walking", "idle": "Idle", "sleeping": "Sleeping"}

var building_type := ""
var _composing := false

var _haze := ColorRect.new()
var _card := PanelContainer.new()
var _header := PanelContainer.new()
var _icon_circle := PanelContainer.new()
var _icon := TextureRect.new()
var _title := Label.new()
var _tagline := Label.new()
var _meta := Label.new()
var _section := Label.new()
var _count := Label.new()
var _count_pill := PanelContainer.new()
var _list := VBoxContainer.new()
var _scroll := ScrollContainer.new()
var _form := PackageForm.new()
var _footer_title := Label.new()
var _footer_text := Label.new()
var _back := Button.new()
var _action := Button.new()


func _init() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	visible = false

	_haze.color = Color(1, 1, 1, 0.35)
	_haze.set_anchors_preset(Control.PRESET_FULL_RECT)
	_haze.gui_input.connect(func(event: InputEvent):
		if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			close())
	add_child(_haze)

	# The card, centered. Godot concept: a CenterContainer keeps its child in
	# the middle at its minimum size, however big the window is.
	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE  # clicks around the card reach the haze
	add_child(center)
	_card.custom_minimum_size.x = CARD_WIDTH
	_card.add_theme_stylebox_override("panel", _box(CREAM, 28, Color.WHITE, 5, 28))
	center.add_child(_card)
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 0)
	_card.add_child(rows)

	rows.add_child(_build_header())
	rows.add_child(_margins(_build_body(), 28, 22, 28, 8))
	rows.add_child(_margins(_build_footer(), 28, 14, 28, 24))

	_form.visible = false
	_form.sent.connect(func(): _show_list())
	_form.busy_changed.connect(func(busy: bool): _action.disabled = busy)
	Net.island_changed.connect(func(): if visible and not _composing: _refresh())


## Open the card for a building type ("mailbox", "lab", ...). `compose` opens
## the Mailbox straight on the "Send a new issue" form.
func open(type: String, compose := false) -> void:
	building_type = type
	var info: Dictionary = BuildingData.TYPES[type]
	var color: Color = info["color"]
	_header.add_theme_stylebox_override("panel", _box(color.lerp(Color.WHITE, 0.78), 24, Color.TRANSPARENT, 0, 0, true))
	_icon.texture = info["icon"]
	_icon.modulate = color
	_count_pill.add_theme_stylebox_override("panel", _box(color.lerp(Color.WHITE, 0.8), 14, Color.TRANSPARENT, 0, 0, false, Vector4(12, 4, 12, 4)))
	_count.add_theme_color_override("font_color", color.darkened(0.3))
	_title.text = str(_building().get("name", info["name"]))
	_tagline.text = info["tagline"]
	var repo = Net.island.get("repo")
	_meta.text = "%s · %s" % [Net.island.get("name", "Island"), repo if repo else "no repo linked"]
	if compose and type == "mailbox":
		_show_form()
	else:
		_show_list()

	# Godot concept: a Tween animates properties over time. Here: fade in and
	# grow from 96% to full size, from the card's middle.
	visible = true
	modulate.a = 0.0
	_card.pivot_offset = _card.size / 2.0
	_card.scale = Vector2.ONE * 0.96
	var tween := create_tween().set_parallel().set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tween.tween_property(self, "modulate:a", 1.0, 0.18)
	tween.tween_property(_card, "scale", Vector2.ONE, 0.25)


func close() -> void:
	if not visible:
		return
	var tween := create_tween()
	tween.tween_property(self, "modulate:a", 0.0, 0.12)
	tween.tween_callback(hide)
	closed.emit()


func _unhandled_input(event: InputEvent) -> void:
	if visible and event.is_action_pressed("ui_cancel"):
		close()
		get_viewport().set_input_as_handled()


# --- The two views ---------------------------------------------------------------

func _show_list() -> void:
	_composing = false
	_form.visible = false
	_scroll.visible = true
	_count_pill.visible = true
	_back.visible = false
	_action.visible = building_type == "mailbox"
	_action.text = "Send a new issue"
	_action.icon = SEND_ICON
	_refresh()


func _show_form() -> void:
	_composing = true
	_scroll.visible = false
	_count_pill.visible = false
	_form.visible = true
	_back.visible = true
	_action.visible = true
	_action.text = "Send it"
	_action.icon = null
	_section.text = "Send a new issue"
	_footer_title.text = "Into the Mailbox it goes"
	_footer_text.text = "Your residents will plan, build and check the work."
	_form.reset()


func _on_action() -> void:
	if _composing:
		_form.submit()
	else:
		_show_form()


## Redraw the list (and the parts of the footer that depend on the island).
func _refresh() -> void:
	for child in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	if building_type == "mailbox":
		_fill_mailbox()
	else:
		_fill_stage()


func _fill_mailbox() -> void:
	# Furthest along first (the Post Office end of the road), queued ones last.
	var stages: Array = BuildingData.TYPES.keys()
	var tasks: Array = Net.island.get("tasks", []).duplicate()
	tasks.sort_custom(func(a, b): return stages.find(_type_of(a.get("building"))) > stages.find(_type_of(b.get("building"))))
	_section.text = "Current issues"
	_count.text = "%d open issue%s" % [tasks.size(), "" if tasks.size() == 1 else "s"]
	_footer_title.text = "Got something for the island?"
	_footer_text.text = "Drop an issue here. Your residents will plan, build and check the work."
	if tasks.is_empty():
		_list.add_child(_empty("No open issues. Send one and watch the residents get to work."))
	for task in tasks:
		_list.add_child(_task_card(task, false))


func _fill_stage() -> void:
	var building := _building()
	var id := str(building.get("id", ""))
	var tasks: Array = Net.island.get("tasks", []).filter(func(t): return t.get("building") == id)
	_section.text = "At the %s now" % _title.text
	_count.text = "%d package%s" % [tasks.size(), "" if tasks.size() == 1 else "s"]
	_footer_title.text = "Next stop"
	_footer_text.text = _next_stops(id)

	for task in tasks:
		_list.add_child(_task_card(task, true))
	if tasks.is_empty():
		_list.add_child(_empty("No packages here right now."))

	# Who's here: residents standing at this building, plus the one who works here.
	var people := []
	for r in Net.island.get("residents", []):
		if r.get("building") == id or r.get("id") == building.get("resident"):
			people.append(r)
	_list.add_child(_subheading("Residents"))
	if people.is_empty():
		_list.add_child(_empty("Nobody works here yet. Move a resident in from the Residents tab."))
	for r in people:
		_list.add_child(_resident_card(r, id))


# --- Cards -----------------------------------------------------------------------

## One package: icon, title, "#91 · Mo is testing at the Lab", and a status pill.
## `with_activity`: add the carrier's latest activity line (on stage panels).
func _task_card(task: Dictionary, with_activity: bool) -> Control:
	var resident := Net.find_resident(str(task.get("agent", "")))
	var where := _type_of(task.get("building"))
	var color: Color = BuildingData.QUEUED_COLOR
	var status := "Queued"
	var line := "Waiting for a resident"
	if task.get("status") == "in_progress" and where != "":
		var info: Dictionary = BuildingData.TYPES[where]
		color = info["color"]
		status = info["stage"]
		var place := str(Net.find_building(str(task["building"])).get("name", info["name"]))
		var who := str(resident.get("name", "Someone"))
		if resident.get("state") == "walking":
			line = "%s is carrying it to the %s" % [who, place]
		else:
			line = "%s is %s at the %s" % [who, info["doing"], place]
	elif task.get("status") == "in_progress":
		status = "In progress"
		color = BuildingData.TYPES["workshop"]["color"]
		line = "On its way"
	var parts := []
	if task.get("issue") != null:
		parts.append("#%d" % int(task["issue"]))
	parts.append(line)
	if task.get("pr_url"):
		parts.append("PR open")

	var texts := VBoxContainer.new()
	texts.add_theme_constant_override("separation", 2)
	texts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	texts.add_child(_text(str(task.get("title", "A package")), 17, true))
	texts.add_child(_detail(" · ".join(parts), 13))
	if with_activity and resident.get("activity"):
		texts.add_child(_activity(str(resident["activity"])))
	var right := VBoxContainer.new()
	right.add_theme_constant_override("separation", 6)
	right.add_child(_pill(status, color))
	right.add_child(_take_back_button(task))
	return _card_row(_icon_tile(BuildingData.TYPES["mailbox"]["icon"], color), texts, right)


## "Take back": cancel the package (asks first). A queued one is cancelled on the
## spot; a running one stops mid-step. The server answers with a `task` event
## (status `cancelled`), which drops it from Net.island, so this card goes away.
func _take_back_button(task: Dictionary) -> Button:
	var button := Button.new()
	button.text = "Take back"
	button.theme_type_variation = &"OutlineButton"
	button.add_theme_font_size_override("font_size", 13)
	button.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	button.focus_mode = Control.FOCUS_NONE
	button.disabled = not Net.is_online
	var title := str(task.get("title", "this package"))
	var running: bool = task.get("status") == "in_progress"
	button.pressed.connect(func():
		ConfirmPopup.ask(self, "Take back “%s”?" % title,
			("The resident working on it stops right away and puts it down."
				if running else "It leaves the queue before anyone starts on it."),
			"Take it back", func(): Net.send_cancel_task(str(task["id"]))))
	return button


## One resident: initial, name, job · mood, what they're doing, and Answer if they need you.
func _resident_card(r: Dictionary, here_id: String) -> Control:
	var color := Color(str(r.get("color", "#8A8FA8")))
	var avatar := PanelContainer.new()
	avatar.custom_minimum_size = Vector2(44, 44)
	avatar.add_theme_stylebox_override("panel", _box(color.lerp(Color.WHITE, 0.7), 22))
	var initial := _text(str(r.get("name", "?")).left(1), 18, true)
	initial.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	initial.add_theme_color_override("font_color", color.darkened(0.25))
	avatar.add_child(initial)

	var texts := VBoxContainer.new()
	texts.add_theme_constant_override("separation", 2)
	texts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	texts.add_child(_text(str(r.get("name", "?")), 17, true))
	var about := "%s · feeling %s" % [str(r.get("job", "")).capitalize(), r.get("mood", "fine")]
	if r.get("building") != here_id:
		about += " · works here, now at the %s" % Net.find_building(str(r.get("building", ""))).get("name", "island")
	texts.add_child(_detail(about, 13))
	if r.get("activity"):
		texts.add_child(_activity(str(r["activity"])))

	var bubble := _bubble_of(str(r.get("id", "")))
	var side: Control
	if not bubble.is_empty():
		side = Button.new()
		side.set_script(AnswerButtonScript)  # the wavy orange button (paints itself in _ready)
		side.theme_type_variation = &"AnswerButton"
		side.custom_minimum_size = Vector2(96, 34)
		side.text = "Answer"
		side.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		side.pressed.connect(func():
			answer_requested.emit(str(bubble["id"]))
			close())
	else:
		var state := str(r.get("state", "idle"))
		side = _pill(STATES.get(state, state.capitalize()), color if state == "working" else Color("8A8FA8"))
	return _card_row(avatar, texts, side)


func _card_row(left: Control, middle: Control, right: Control) -> Control:
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", _box(Color.WHITE, 18, LINE, 2, 0, false, Vector4(18, 14, 18, 14)))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 16)
	for part in [left, middle, right]:
		if part != middle:
			part.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(part)
	card.add_child(row)
	return card


func _icon_tile(texture: Texture2D, color: Color) -> Control:
	var tile := PanelContainer.new()
	tile.custom_minimum_size = Vector2(44, 44)
	tile.add_theme_stylebox_override("panel", _box(color.lerp(Color.WHITE, 0.78), 12))
	var icon := TextureRect.new()
	icon.texture = texture
	icon.modulate = color.darkened(0.15)
	icon.custom_minimum_size = Vector2(22, 22)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	tile.add_child(icon)
	return tile


## "● Testing" on a tinted pill.
func _pill(text: String, color: Color) -> Control:
	var pill := PanelContainer.new()
	pill.custom_minimum_size.x = 116
	pill.add_theme_stylebox_override("panel", _box(color.lerp(Color.WHITE, 0.8), 16, Color.TRANSPARENT, 0, 0, false, Vector4(14, 7, 14, 7)))
	var label := _text("●  " + text, 13, true)
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.add_theme_color_override("font_color", color.darkened(0.2))
	pill.add_child(label)
	return pill


# --- Building the frame ----------------------------------------------------------

func _build_header() -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 20)
	_icon_circle.custom_minimum_size = Vector2(84, 84)
	_icon_circle.add_theme_stylebox_override("panel", _box(Color.WHITE, 42))
	_icon.custom_minimum_size = Vector2(40, 40)
	_icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_icon.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_icon_circle.add_child(_icon)
	row.add_child(_icon_circle)

	var names := VBoxContainer.new()
	names.add_theme_constant_override("separation", 2)
	names.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	names.alignment = BoxContainer.ALIGNMENT_CENTER
	_title.theme_type_variation = &"ScreenTitle"
	_title.add_theme_font_size_override("font_size", 32)
	_tagline.theme_type_variation = &"BodyText"
	_tagline.add_theme_font_size_override("font_size", 16)
	_meta.theme_type_variation = &"LabelMuted"
	_meta.add_theme_font_size_override("font_size", 12)
	for label in [_title, _tagline, _meta]:
		names.add_child(label)
	row.add_child(names)

	var close_button := Button.new()
	close_button.icon = CLOSE_ICON
	close_button.custom_minimum_size = Vector2(38, 38)
	close_button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	close_button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	close_button.expand_icon = true
	close_button.focus_mode = Control.FOCUS_NONE
	close_button.tooltip_text = "Close (Esc)"
	close_button.add_theme_color_override("icon_normal_color", INK)
	close_button.add_theme_color_override("icon_hover_color", INK)
	close_button.add_theme_color_override("icon_pressed_color", INK)
	var ring := _box(Color.TRANSPARENT, 19, INK, 2, 0, false, Vector4(10, 10, 10, 10))
	var ring_hover := _box(Color(1, 1, 1, 0.5), 19, INK, 2, 0, false, Vector4(10, 10, 10, 10))
	for state in ["normal", "focus"]:
		close_button.add_theme_stylebox_override(state, ring)
	for state in ["hover", "pressed"]:
		close_button.add_theme_stylebox_override(state, ring_hover)
	close_button.pressed.connect(close)
	row.add_child(close_button)

	_header.add_child(_margins(row, 28, 24, 28, 24))
	return _header


func _build_body() -> Control:
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 14)
	var heading := HBoxContainer.new()
	_section.theme_type_variation = &"SectionTitle"
	_section.add_theme_font_size_override("font_size", 20)
	_section.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(_section)
	_count.theme_type_variation = &"LabelStrong"
	_count.add_theme_font_size_override("font_size", 12)
	_count_pill.add_child(_count)
	_count_pill.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	heading.add_child(_count_pill)
	body.add_child(heading)

	_scroll.custom_minimum_size.y = LIST_HEIGHT
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.add_theme_constant_override("separation", 10)
	_scroll.add_child(_list)
	body.add_child(_scroll)
	_form.custom_minimum_size.y = LIST_HEIGHT
	body.add_child(_form)
	return body


func _build_footer() -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	var texts := VBoxContainer.new()
	texts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	texts.add_theme_constant_override("separation", 2)
	_footer_title.theme_type_variation = &"LabelStrong"
	_footer_title.add_theme_font_size_override("font_size", 15)
	_footer_text.theme_type_variation = &"LabelMuted"
	_footer_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_footer_text.custom_minimum_size.x = 340
	texts.add_child(_footer_title)
	texts.add_child(_footer_text)
	row.add_child(texts)

	_back.text = "Back"
	_back.theme_type_variation = &"OutlineButton"
	_back.custom_minimum_size = Vector2(90, 46)
	_back.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_back.pressed.connect(_show_list)
	row.add_child(_back)

	_action.set_script(AnswerButtonScript)  # the wavy orange button
	_action.theme_type_variation = &"AnswerButton"
	_action.add_theme_constant_override("icon_max_width", 16)
	_action.custom_minimum_size = Vector2(196, 46)
	_action.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_action.add_theme_font_size_override("font_size", 17)
	_action.pressed.connect(_on_action)
	row.add_child(_action)
	return row


# --- Small helpers ---------------------------------------------------------------

## This island's building of the open type (from Net.island), or {}.
func _building() -> Dictionary:
	for b in Net.island.get("buildings", []):
		if b.get("type") == building_type:
			return b
	return {}


## The type of the building with this id (null or "" = none), or "".
func _type_of(building_id) -> String:
	return str(Net.find_building(str(building_id)).get("type", "")) if building_id else ""


func _bubble_of(agent: String) -> Dictionary:
	for b in Net.island.get("bubbles", []):
		if b.get("agent") == agent:
			return b
	return {}


## "Passes go to the Courthouse · fails go back to the Workshop", from the roads.
func _next_stops(from_id: String) -> String:
	var words := {"pass": "Passes go to", "fail": "Fails go back to", "always": "Packages go on to"}
	var parts := []
	for road in Net.island.get("roads", []):
		if road.get("from") == from_id:
			var to := str(Net.find_building(str(road["to"])).get("name", road["to"]))
			parts.append("%s the %s" % [words.get(road.get("condition"), "On to"), to])
	return " · ".join(parts) if parts else "Last stop: packages are finished here."


func _activity(text: String) -> Label:
	var label := _detail("“%s”" % text, 12)
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	label.tooltip_text = text
	label.mouse_filter = Control.MOUSE_FILTER_PASS
	return label


func _subheading(text: String) -> Label:
	var label := _text(text, 15, true)
	label.custom_minimum_size.y = 34  # a little air above the section
	label.vertical_alignment = VERTICAL_ALIGNMENT_BOTTOM
	return label


func _empty(text: String) -> Label:
	var label := _text(text, 14, false)
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.custom_minimum_size.y = 36
	return label


func _text(text: String, size: int, strong: bool) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"LabelStrong" if strong else &"LabelMuted"
	label.add_theme_font_size_override("font_size", size)
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	return label


## A detail line inside a card: the title's ink (not the muted grey), so it reads on white.
func _detail(text: String, size: int) -> Label:
	var label := _text(text, size, false)
	label.add_theme_color_override("font_color", INK)
	return label


func _margins(child: Control, left: int, top: int, right: int, bottom: int) -> MarginContainer:
	var box := MarginContainer.new()
	box.add_theme_constant_override("margin_left", left)
	box.add_theme_constant_override("margin_top", top)
	box.add_theme_constant_override("margin_right", right)
	box.add_theme_constant_override("margin_bottom", bottom)
	box.add_child(child)
	return box


## A rounded box. `top_only` rounds just the top corners (the header). `pad`:
## content margins (left, top, right, bottom).
func _box(bg: Color, radius: int, border := Color.TRANSPARENT, border_width := 0, shadow := 0,
		top_only := false, pad := Vector4.ZERO) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = bg
	style.set_corner_radius_all(radius)
	if top_only:
		style.corner_radius_bottom_left = 0
		style.corner_radius_bottom_right = 0
	style.border_color = border
	style.set_border_width_all(border_width)
	if shadow:
		style.shadow_size = shadow
		style.shadow_color = Color(0.16, 0.18, 0.31, 0.18)
		style.shadow_offset = Vector2(0, 8)
	style.content_margin_left = pad.x
	style.content_margin_top = pad.y
	style.content_margin_right = pad.z
	style.content_margin_bottom = pad.w
	style.anti_aliasing = true
	return style
