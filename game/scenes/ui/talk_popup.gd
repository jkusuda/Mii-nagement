class_name TalkPopup
extends Control
## Talk to a resident at a building (the building panel → a resident → Talk).
##
## Left: the resident standing in a little room tinted like the building
## (TalkRoom behind a MiiPreview). Right: the conversation as speech bubbles and
## a text box. Each message goes to the server as a `talk` (Net.send_talk), with
## the conversation so far; the resident's brain answers in character, in that
## building's role, without changing any files (docs/protocol.md "talk").
##
## The conversation lives only while the popup is open. Built in code, like
## BuildingPanel: a haze over the window and a card in the middle. Click the
## haze, the ✕, or press Esc to close.

const MiiPreviewScene := preload("res://scenes/mii/mii_preview.tscn")
const CLOSE_ICON := preload("res://assets/icons/x.svg")
const STAGE_SIZE := Vector2(330, 440)
const CHAT_WIDTH := 430.0
const HISTORY_TURNS := 10
const INK := Color("292E50")
const MUTED := Color("68738B")
const CREAM := Color("FFFDF3")
const LINE := Color("E3E5EE")

var resident_id := ""
var building_id := ""

var _turns: Array = []  # [{"from": "you" | "resident", "text"}], oldest first
var _pending := ""  # the talk id we're waiting on ("" = none)
var _haze := ColorRect.new()
var _card := PanelContainer.new()
var _room := TalkRoom.new()
var _preview: MiiPreview
var _name := Label.new()
var _about := Label.new()
var _scroll := ScrollContainer.new()
var _bubbles := VBoxContainer.new()
var _status := Label.new()
var _input := LineEdit.new()
var _send := Button.new()
var _thinking_bubble: Control


func _init() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	visible = false

	_haze.color = Color(1, 1, 1, 0.35)
	_haze.set_anchors_preset(Control.PRESET_FULL_RECT)
	_haze.gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				close()
	)
	add_child(_haze)

	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(center)
	_card.add_theme_stylebox_override("panel", _box(CREAM, 28, Color.WHITE, 5, 28, Vector4(18, 18, 22, 18)))
	center.add_child(_card)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 22)
	_card.add_child(row)
	row.add_child(_build_stage())
	row.add_child(_build_chat())

	Net.talk_thinking.connect(_on_thinking)
	Net.talk_replied.connect(_on_reply)
	Net.talk_failed.connect(_on_failed)
	Net.resident_removed.connect(
		func(id: String):
			if id == resident_id:
				close()
	)


## Open a fresh conversation with a resident, at a building (ids from Net.island).
func open(new_resident: String, new_building: String) -> void:
	var r := Net.find_resident(new_resident)
	if r.is_empty():
		return
	resident_id = new_resident
	building_id = new_building
	_turns.clear()
	_pending = ""
	for child in _bubbles.get_children():
		child.queue_free()
	_thinking_bubble = null

	var building := Net.find_building(building_id)
	var type := str(building.get("type", ""))
	var place := str(building.get("name", BuildingData.TYPES.get(type, {}).get("name", "island")))
	var tint: Color = BuildingData.TYPES[type]["color"] if BuildingData.TYPES.has(type) else Color("F4A7B9")
	_room.color = tint
	_preview.use_shot(MiiPreview.ROOM_SHOT)
	_preview.set_color(Color(str(r.get("color", "#FFFFFF"))))
	_preview.set_appearance(MiiParts.for_resident(r))
	var who := str(r.get("name", "?"))
	_name.text = who
	_about.text = "%s at the %s" % [str(r.get("job", "resident")).capitalize(), place]
	if r.get("state") == "working":
		_about.text += " · busy with a package"
	_input.placeholder_text = "Say something to %s..." % who
	_set_status("%s answers as the %s's %s and won't change any files." % [who, place, str(r.get("job", "resident"))])
	_update_send()

	# Fade in and grow, like the building panel.
	visible = true
	modulate.a = 0.0
	_card.pivot_offset = _card.size / 2.0
	_card.scale = Vector2.ONE * 0.96
	var tween := create_tween().set_parallel().set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tween.tween_property(self, "modulate:a", 1.0, 0.18)
	tween.tween_property(_card, "scale", Vector2.ONE, 0.25)
	_preview.hop()
	_input.grab_focus.call_deferred()


func close() -> void:
	if not visible:
		return
	_pending = ""  # a late reply is dropped
	var tween := create_tween()
	tween.tween_property(self, "modulate:a", 0.0, 0.12)
	tween.tween_callback(hide)


func _unhandled_input(event: InputEvent) -> void:
	if visible and event.is_action_pressed("ui_cancel"):
		close()
		get_viewport().set_input_as_handled()


# --- Talking ---------------------------------------------------------------------


func _on_send(_text := "") -> void:
	var text := _input.text.strip_edges()
	if text == "" or _pending != "" or not Net.is_online:
		return
	var talk_id := "k_%08x" % (randi() & 0x7fffffff)
	var history := _turns.slice(-HISTORY_TURNS)
	if not Net.send_talk(talk_id, resident_id, building_id, text, history):
		_set_status("Not connected to the island right now.")
		return
	_pending = talk_id
	_input.text = ""
	_add_turn("you", text)
	_thinking_bubble = _bubble("…", false, true)
	_bubbles.add_child(_thinking_bubble)
	_scroll_down()
	_set_status("%s is thinking..." % _name.text)
	_update_send()


func _on_thinking(talk_id: String, text: String) -> void:
	if talk_id == _pending and text != "":
		_set_status("› " + text)


func _on_reply(talk_id: String, text: String) -> void:
	if talk_id != _pending:
		return
	_finish_waiting()
	_add_turn("resident", text)
	_set_status("")
	_preview.hop(2 if text.ends_with("!") else 1)


func _on_failed(talk_id: String, message: String) -> void:
	if talk_id != _pending:
		return
	_finish_waiting()
	_set_status(message if message != "" else "%s couldn't answer." % _name.text)


func _finish_waiting() -> void:
	_pending = ""
	if _thinking_bubble and is_instance_valid(_thinking_bubble):
		_thinking_bubble.queue_free()
	_thinking_bubble = null
	_update_send()
	_input.grab_focus()


func _add_turn(from: String, text: String) -> void:
	_turns.append({"from": from, "text": text})
	_bubbles.add_child(_bubble(text, from == "you"))
	_scroll_down()


func _update_send() -> void:
	_send.disabled = _pending != "" or not Net.is_online
	_input.editable = _pending == ""


func _set_status(text: String) -> void:
	_status.text = text
	_status.visible = text != ""


## Scroll to the newest bubble once it has been laid out (next frame).
func _scroll_down() -> void:
	(
		(func():
			var bar := _scroll.get_v_scroll_bar()
			_scroll.scroll_vertical = int(bar.max_value))
		. call_deferred()
	)


# --- Building the card -----------------------------------------------------------


## The resident standing in their room. The room is drawn behind the Mii stage,
## whose background is transparent (mii_preview.tscn).
func _build_stage() -> Control:
	var frame := PanelContainer.new()
	frame.add_theme_stylebox_override("panel", _box(Color.WHITE, 22, Color.WHITE, 4))
	var stage := Control.new()
	stage.custom_minimum_size = STAGE_SIZE
	stage.clip_contents = true
	frame.add_child(stage)
	_room.set_anchors_preset(Control.PRESET_FULL_RECT)
	stage.add_child(_room)
	_preview = MiiPreviewScene.instantiate()
	_preview.spin_enabled = false
	_preview.custom_minimum_size = STAGE_SIZE
	_preview.set_anchors_preset(Control.PRESET_FULL_RECT)
	_preview.mouse_filter = Control.MOUSE_FILTER_IGNORE
	stage.add_child(_preview)
	return frame


func _build_chat() -> Control:
	var column := VBoxContainer.new()
	column.custom_minimum_size = Vector2(CHAT_WIDTH, STAGE_SIZE.y)
	column.add_theme_constant_override("separation", 12)

	var header := HBoxContainer.new()
	var names := VBoxContainer.new()
	names.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	names.add_theme_constant_override("separation", 0)
	_name.theme_type_variation = &"ScreenTitle"
	_name.add_theme_font_size_override("font_size", 30)
	_about.theme_type_variation = &"LabelMuted"
	_about.add_theme_font_size_override("font_size", 14)
	names.add_child(_name)
	names.add_child(_about)
	header.add_child(names)
	header.add_child(_close_button())
	column.add_child(header)

	var frame := PanelContainer.new()
	frame.size_flags_vertical = Control.SIZE_EXPAND_FILL
	frame.add_theme_stylebox_override("panel", _box(Color.WHITE, 18, LINE, 2, 0, Vector4(12, 12, 12, 12)))
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_bubbles.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_bubbles.add_theme_constant_override("separation", 8)
	_scroll.add_child(_bubbles)
	frame.add_child(_scroll)
	column.add_child(frame)

	_status.theme_type_variation = &"LabelMuted"
	_status.add_theme_font_size_override("font_size", 12)
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	column.add_child(_status)

	var input_row := HBoxContainer.new()
	input_row.add_theme_constant_override("separation", 10)
	_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_input.custom_minimum_size.y = 44
	_input.max_length = 2000
	_input.add_theme_font_size_override("font_size", 15)
	_input.text_submitted.connect(_on_send)
	input_row.add_child(_input)
	_send.set_script(AnswerButton)  # the wavy orange button
	_send.theme_type_variation = &"AnswerButton"
	_send.text = "Send"
	_send.custom_minimum_size = Vector2(96, 44)
	_send.focus_mode = Control.FOCUS_NONE
	_send.pressed.connect(_on_send)
	input_row.add_child(_send)
	column.add_child(input_row)
	Net.connected.connect(_update_send)
	Net.disconnected.connect(_update_send)
	return column


## One speech bubble: yours on the right (tinted), the resident's on the left (white).
func _bubble(text: String, mine: bool, waiting := false) -> Control:
	var tint := Color(str(Net.find_resident(resident_id).get("color", "#8A8FA8")))
	var bubble := PanelContainer.new()
	var bg := Color("EEF0FB") if mine else Color.WHITE
	var border := Color("C9CEF0") if mine else tint.lerp(Color.WHITE, 0.4)
	bubble.add_theme_stylebox_override("panel", _box(bg, 16, border, 2, 0, Vector4(14, 9, 14, 9)))
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.theme_type_variation = &"LabelStrong" if not waiting else &"LabelMuted"
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", MUTED if waiting else INK)
	bubble.add_child(label)
	# The other side keeps a margin, so the bubbles alternate left and right.
	var side := MarginContainer.new()
	side.add_theme_constant_override("margin_left" if mine else "margin_right", 56)
	side.add_child(bubble)
	return side


func _close_button() -> Button:
	var button := Button.new()
	button.icon = CLOSE_ICON
	button.custom_minimum_size = Vector2(38, 38)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button.expand_icon = true
	button.focus_mode = Control.FOCUS_NONE
	button.tooltip_text = "Close (Esc)"
	for state in ["icon_normal_color", "icon_hover_color", "icon_pressed_color"]:
		button.add_theme_color_override(state, INK)
	var ring := _box(Color.TRANSPARENT, 19, INK, 2, 0, Vector4(10, 10, 10, 10))
	var ring_hover := _box(Color(1, 1, 1, 0.5), 19, INK, 2, 0, Vector4(10, 10, 10, 10))
	for state in ["normal", "focus"]:
		button.add_theme_stylebox_override(state, ring)
	for state in ["hover", "pressed"]:
		button.add_theme_stylebox_override(state, ring_hover)
	button.pressed.connect(close)
	return button


## A rounded box. `pad`: content margins (left, top, right, bottom).
func _box(
	bg: Color, radius: int, border := Color.TRANSPARENT, border_width := 0, shadow := 0, pad := Vector4.ZERO
) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = bg
	style.set_corner_radius_all(radius)
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
