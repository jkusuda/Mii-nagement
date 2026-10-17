class_name TalkPopup
extends Control
## Talk to a resident inside a building (the building panel → a resident → Talk),
## laid out like Tomodachi Life's conversations:
##
## - the whole window is a room tinted like the building (TalkRoom), with the resident
##   standing in the middle (a MiiPreview);
## - what they say is a big speech bubble over their head (residents' names in orange);
## - bottom left: the resident → what they're working on → the building;
## - bottom right: two answers to pick (the yellow wavy one and a white one), and a box
##   to type anything else.
##
## Opening it starts a conversation on the server (Net.send_talk_open): the resident stays
## inside the building until it closes, so they never walk out mid-chat, and their agent
## starts warming up. Each message is a `talk` (Net.send_talk) with the conversation so
## far; the reply streams into the bubble as it's written (talk_partial). The resident's
## brain answers in character, in the building's role, without changing any files
## (docs/protocol.md "talk"). Only a resident working inside the building can talk
## (ResidentData.inside).
##
## History (top right) shows everything said in talks at this building, from the server
## (GET /islands/{id}/talks). Built in code; ✕ or Esc closes it (Esc closes History first).

const MiiPreviewScene := preload("res://scenes/mii/mii_preview.tscn")
const CLOSE_ICON := preload("res://assets/icons/x.svg")
const HISTORY_TURNS := 10
const INK := Color("292E50")
const MUTED := Color("68738B")
const NAME_COLOR := "#E8803A"  # residents' names in the bubble
const MII_SIZE := Vector2(420, 620)  # the Mii stage; with ROOM_SHOT the feet land at 80% of its height
const FEET_AT := 0.88  # where the Mii's feet stand, as a fraction of the window height
const HEAD_TOP := 0.15  # the top of the hair, as a fraction of the stage height
const BUBBLE_MAX_WIDTH := 760.0
const BUBBLE_MAX_TEXT := 230.0  # tallest the bubble's text gets before it scrolls
const TYPE_SPEED := 60.0  # characters per second in the speech bubble
const FIRST_ANSWERS := ["What are you working on?", "How's it going?"]
const LATER_ANSWERS := ["Tell me more.", "Thanks! That's all."]  # the second one says goodbye

var resident_id := ""
var building_id := ""

var _turns: Array = []  # [{"from": "you" | "resident", "text"}], oldest first: this resident's, for the prompt
var _lines: Array = []  # everything said at this building ({"agent", "from", "text"}), for History
var _conversation := ""  # the open conversation's id ("" = none)
var _ready := false  # the server said talk_ready: we may send
var _pending := ""  # the talk id we're waiting on ("" = none)
var _partial := ""  # the reply so far, while it streams in
var _history := PanelContainer.new()
var _history_list := VBoxContainer.new()
var _history_scroll := ScrollContainer.new()
var _history_title := Label.new()
var _room := TalkRoom.new()
var _preview: MiiPreview
var _bubble := PanelContainer.new()
var _bubble_scroll := ScrollContainer.new()
var _bubble_text := RichTextLabel.new()
var _tail := Control.new()
var _status := Label.new()
var _you_said := Label.new()
var _answers: Array[Button] = []
var _input := LineEdit.new()
var _send := Button.new()
var _where := Label.new()
var _headshot := TextureRect.new()
var _arrow := ArrowPill.new()
var _building_icon := TextureRect.new()
var _building_disc := PanelContainer.new()
var _dots := 0.0  # the "…" animation while waiting
var _typed := 0.0  # characters of the bubble shown so far (the typewriter)
var _typed_text := ""  # the bubble's plain text last time; new text that continues it keeps typing


func _init() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	visible = false
	mouse_filter = Control.MOUSE_FILTER_STOP  # the room covers the island: no clicks through

	_room.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(_room)
	_preview = MiiPreviewScene.instantiate()
	_preview.spin_enabled = false
	_preview.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_preview)

	_build_bubble()
	add_child(_build_who())
	add_child(_build_answers())
	add_child(_top_buttons())
	_status.theme_type_variation = &"LabelMuted"
	_status.add_theme_font_size_override("font_size", 14)
	_status.add_theme_color_override("font_color", MUTED)
	_status.position = Vector2(24, 20)
	add_child(_status)
	add_child(_build_history())

	resized.connect(_layout)
	Net.talk_ready.connect(_on_ready)
	Net.talk_thinking.connect(_on_thinking)
	Net.talk_partial.connect(_on_partial)
	Net.talk_replied.connect(_on_reply)
	Net.talk_failed.connect(_on_failed)
	Net.talk_closed.connect(_on_closed)
	Net.island_changed.connect(_on_island_changed)
	Net.connected.connect(_update_inputs)
	Net.disconnected.connect(_update_inputs)


## Open a fresh conversation with a resident inside a building (ids from Net.island).
func open(new_resident: String, new_building: String) -> void:
	var r := Net.find_resident(new_resident)
	if r.is_empty():
		return
	_end_conversation()  # a talk still open with someone else
	resident_id = new_resident
	building_id = new_building
	_turns.clear()
	_lines.clear()
	_pending = ""
	_partial = ""
	_ready = false
	_history.visible = false
	# Hold them inside and warm their agent up while you read the greeting.
	_conversation = "c_%08x" % (randi() & 0x7fffffff)
	if not Net.send_talk_open(_conversation, resident_id, building_id):
		_conversation = ""
	_load_history()

	var building := Net.find_building(building_id)
	var type := str(building.get("type", ""))
	var info: Dictionary = BuildingData.TYPES.get(type, {})
	var tint: Color = info.get("color", Color("F4A7B9"))
	_room.color = tint
	_preview.use_shot(MiiPreview.ROOM_SHOT)
	_preview.set_color(Color(str(r.get("color", "#FFFFFF"))))
	_preview.set_appearance(MiiParts.for_resident(r))

	_where.text = "At the %s" % _place()
	_headshot.texture = Portraits.of(r)
	_building_disc.add_theme_stylebox_override("panel", _box(tint.lerp(Color.WHITE, 0.55), 46, Color.WHITE, 4))
	_building_icon.texture = info.get("icon")
	_building_icon.modulate = tint.darkened(0.25)
	_arrow.text = _working_on()

	_you_said.visible = false
	_input.text = ""
	_input.placeholder_text = "Say something to %s..." % r.get("name", "them")
	_set_status("")
	_say(_greeting(r))
	_show_answers(FIRST_ANSWERS)
	_update_inputs()

	visible = true
	modulate.a = 0.0
	create_tween().tween_property(self, "modulate:a", 1.0, 0.2)
	_layout.call_deferred()
	_preview.hop()


func close() -> void:
	if not visible:
		return
	_pending = ""  # a late reply is dropped
	_end_conversation()  # the resident may go back to work
	var tween := create_tween()
	tween.tween_property(self, "modulate:a", 0.0, 0.15)
	tween.tween_callback(hide)


func _end_conversation() -> void:
	if _conversation != "":
		Net.send_talk_close(_conversation)
	_conversation = ""
	_ready = false


func _unhandled_input(event: InputEvent) -> void:
	if visible and event.is_action_pressed("ui_cancel"):
		if _history.visible:
			_history.visible = false
		else:
			close()
		get_viewport().set_input_as_handled()


func _process(delta: float) -> void:
	if visible and _pending != "" and _partial == "":  # dots until the first words arrive
		_dots += delta * 2.5
		_bubble_text.visible_characters = -1  # -1 = show everything
		_bubble_text.text = "[center]%s[/center]" % ".".repeat(1 + int(_dots) % 3)
	elif _bubble_text.visible_characters >= 0:  # typewriter: reveal a few more characters
		_typed += delta * TYPE_SPEED
		_apply_typed()


# --- Talking ---------------------------------------------------------------------


## Send `text` (an answer pill, or what was typed).
func _send_text(text: String) -> void:
	text = text.strip_edges()
	if text == "" or _pending != "" or not Net.is_online or not _still_inside():
		return
	if not _ready:
		return
	var talk_id := "k_%08x" % (randi() & 0x7fffffff)
	if not Net.send_talk(talk_id, _conversation, resident_id, building_id, text, _turns.slice(-HISTORY_TURNS)):
		_set_status("Not connected to the island right now.")
		return
	_pending = talk_id
	_partial = ""
	_turns.append({"from": "you", "text": text})
	_add_line("you", text)
	_input.text = ""
	_you_said.text = "You: " + text
	_you_said.visible = true
	_dots = 0.0
	_typed = 0.0  # the reply types in from zero, whatever the greeting did
	_typed_text = ""
	_set_status("%s is thinking..." % _name())
	_update_inputs()
	_layout.call_deferred()


func _on_answer(i: int) -> void:
	var text := _answers[i].text
	if text == LATER_ANSWERS[1]:  # "Thanks! That's all."
		_say("Anytime! Come by again.")
		_preview.hop(2)
		get_tree().create_timer(1.2).timeout.connect(close)
		return
	_send_text(text)


func _on_ready(conversation_id: String) -> void:
	if conversation_id == _conversation:
		_ready = true
		_update_inputs()


func _on_thinking(talk_id: String, text: String) -> void:
	if talk_id == _pending and text != "":
		_set_status("› " + text)


## The next piece of the reply: show what's arrived so far.
func _on_partial(talk_id: String, text: String) -> void:
	if talk_id != _pending:
		return
	_partial += text
	_say(_partial)


func _on_reply(talk_id: String, text: String) -> void:
	if talk_id != _pending:
		return
	_pending = ""
	_partial = ""
	_turns.append({"from": "resident", "text": text})
	_add_line("resident", text)
	_say(text)
	_set_status("")
	_show_answers(LATER_ANSWERS)
	_update_inputs()
	_preview.hop(2 if text.strip_edges().ends_with("!") else 1)
	_input.grab_focus()


func _on_failed(talk_id: String, message: String) -> void:
	if talk_id == _conversation:  # the conversation couldn't open (they just left, or are busy talking)
		_conversation = ""
		_say("Hmm... I can't talk right now.")
		_set_status(message)
		_update_inputs()
		return
	if talk_id != _pending:
		return
	_pending = ""
	_partial = ""
	_say("Hmm... I can't answer that right now.")
	_set_status(message if message != "" else "%s couldn't answer." % _name())
	_update_inputs()


## The server ended the conversation (nothing said for a while).
func _on_closed(conversation_id: String, message: String) -> void:
	if conversation_id != _conversation:
		return
	_conversation = ""
	_ready = false
	_say(message if message != "" else "I have to get back to work now!")
	_update_inputs()


## Everything said in talks at this building (the server keeps it). This resident's
## latest lines also go back into the prompt, so they remember your last chat.
func _load_history() -> void:
	var asked_for := building_id
	var path := "/islands/%s/talks?building=%s" % [str(Net.island.get("id", "")).uri_encode(), building_id.uri_encode()]
	Net.request_json(
		path,
		func(ok: bool, data: Dictionary):
			if not ok or asked_for != building_id:
				return
			var stored: Array = data.get("turns", [])
			_lines = stored + _lines  # anything said while it loaded stays at the end
			var mine := stored.filter(func(t): return t.get("agent") == resident_id)
			var earlier := mine.map(func(t): return {"from": t["from"], "text": t["text"]})
			_turns = (earlier + _turns).slice(-HISTORY_TURNS)
			_fill_history()
	)


func _add_line(from: String, text: String) -> void:
	_lines.append({"agent": resident_id, "from": from, "text": text})
	_fill_history()


## The History list: "You:" and the resident's name (in orange) before each line, newest at the bottom.
func _fill_history() -> void:
	for child in _history_list.get_children():
		child.queue_free()
	if _lines.is_empty():
		var empty := Label.new()
		empty.text = "Nothing said here yet. Say hi!"
		empty.add_theme_color_override("font_color", MUTED)
		empty.add_theme_font_size_override("font_size", 16)
		_history_list.add_child(empty)
	for line in _lines:
		var who := (
			"[color=#68738B]You[/color]"
			if line.get("from") == "you"
			else (
				"[color=%s]%s[/color]"
				% [NAME_COLOR, str(Net.find_resident(str(line.get("agent", ""))).get("name", "?"))]
			)
		)
		var label := RichTextLabel.new()
		label.bbcode_enabled = true
		label.fit_content = true
		label.selection_enabled = true
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.add_theme_font_size_override("normal_font_size", 16)
		label.add_theme_color_override("default_color", INK)
		label.text = "%s:  %s" % [who, _escape(str(line.get("text", "")))]
		_history_list.add_child(label)
	(func(): _history_scroll.scroll_vertical = int(_history_scroll.get_v_scroll_bar().max_value)).call_deferred()


func _toggle_history() -> void:
	_history.visible = not _history.visible
	if _history.visible:
		_history_title.text = "Talks at the %s" % _place()
		_fill_history()


## They left the building (the package moved on): no more talking here.
func _on_island_changed() -> void:
	if visible and not _still_inside() and _pending == "":
		_say("Oh! I have to head out now. Talk to me inside my next building!")
		_set_status("%s left the %s." % [_name(), _place()])
		_update_inputs()


func _still_inside() -> bool:
	return ResidentData.inside(Net.find_resident(resident_id), building_id)


## Put a line in the speech bubble (names in orange), sized to fit.
func _say(text: String) -> void:
	var size_px := 30 if text.length() < 90 else (25 if text.length() < 260 else 21)
	_bubble_text.add_theme_font_size_override("normal_font_size", size_px)
	_bubble_text.text = "[center]%s[/center]" % _highlight_names(_escape(text.strip_edges()))
	# Typewriter: text that continues what's showing keeps going; anything else restarts.
	var plain := _bubble_text.get_parsed_text()
	if not plain.begins_with(_typed_text):
		_typed = 0.0
	_typed_text = plain
	_apply_typed()
	_layout.call_deferred()


## Show only the first `_typed` characters of the bubble (-1 once all are out, so _process idles).
func _apply_typed() -> void:
	if _typed >= _bubble_text.get_total_character_count():
		_bubble_text.visible_characters = -1
	else:
		_bubble_text.visible_characters = int(_typed)


## A click on the bubble skips to the end of the text that has arrived.
func _on_bubble_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		_typed = INF
		_apply_typed()


func _show_answers(texts: Array) -> void:
	for i in _answers.size():
		_answers[i].text = texts[i]


func _update_inputs() -> void:
	var can := _ready and _pending == "" and Net.is_online and _still_inside()
	for b in _answers:
		b.disabled = not can
	_send.disabled = not can
	_input.editable = can


func _set_status(text: String) -> void:
	_status.text = text
	_status.visible = text != ""


## "Hi! I'm working on “Fix login typo” right now.", from what the island shows.
func _greeting(r: Dictionary) -> String:
	var task := _task_here()
	var hello := (
		"Hi! I'm working on “%s” right now." % task["title"]
		if not task.is_empty()
		else "Hi! Busy day at the %s." % _place()
	)
	var catchphrase := str(r.get("catchphrase", "")).strip_edges()
	return hello + (" " + catchphrase if catchphrase else "")


func _task_here() -> Dictionary:
	for t in Net.island.get("tasks", []):
		if t.get("agent") == resident_id and t.get("building") == building_id:
			return t
	return {}


func _working_on() -> String:
	var task := _task_here()
	if task.is_empty():
		return "Working here"
	var title := str(task["title"])
	return title if title.length() <= 24 else title.left(22).strip_edges() + "…"


func _name() -> String:
	return str(Net.find_resident(resident_id).get("name", "They"))


func _place() -> String:
	var building := Net.find_building(building_id)
	var type := str(building.get("type", ""))
	return str(building.get("name", BuildingData.TYPES.get(type, {}).get("name", "building")))


## Wrap every resident's name in orange, like Tomodachi Life does.
func _highlight_names(text: String) -> String:
	for r in Net.island.get("residents", []):
		var name := str(r.get("name", ""))
		if name.length() < 2:
			continue
		var pattern := RegEx.create_from_string("\\b%s\\b" % _regex_escape(name))
		text = pattern.sub(text, "[color=%s]%s[/color]" % [NAME_COLOR, name], true)
	return text


static func _escape(text: String) -> String:
	return text.replace("[", "[lb]")  # agent text may contain "["


static func _regex_escape(text: String) -> String:
	var out := ""
	for c in text:
		out += ("\\" + c) if c in ".^$*+?()[]{}|\\" else c
	return out


# --- Layout ------------------------------------------------------------------------


## Place the Mii, its shadow and the bubble for the current window size. The bubble
## grows to fit its text (up to BUBBLE_MAX_TEXT, then it scrolls).
func _layout() -> void:
	var w := size.x
	var h := size.y
	var feet_y := h * FEET_AT
	_preview.size = MII_SIZE
	_preview.position = Vector2((w - MII_SIZE.x) / 2.0, feet_y - MII_SIZE.y * 0.8)
	_room.feet = Vector2(w / 2.0, feet_y)

	var text_height := minf(_bubble_text.get_content_height(), BUBBLE_MAX_TEXT)
	_bubble_scroll.custom_minimum_size.y = maxf(text_height, 36.0)
	_bubble.reset_size()
	var head_top := _preview.position.y + MII_SIZE.y * HEAD_TOP
	var bubble_size := _bubble.get_combined_minimum_size()
	var top := maxf(16.0, head_top - 26.0 - bubble_size.y)
	_bubble.position = Vector2((w - bubble_size.x) / 2.0, top)
	_bubble.size = bubble_size
	_tail.position = Vector2(w / 2.0 - 18.0, top + bubble_size.y - 2.0)


func _build_bubble() -> void:
	_bubble.add_theme_stylebox_override(
		"panel", _box(Color.WHITE, 64, Color.TRANSPARENT, 0, 18, Vector4(56, 26, 56, 26))
	)
	# Clicks pass through the scroll and text up to the bubble, which skips the typewriter.
	_bubble.gui_input.connect(_on_bubble_input)
	_bubble_scroll.mouse_filter = Control.MOUSE_FILTER_PASS
	_bubble_text.mouse_filter = Control.MOUSE_FILTER_PASS
	_bubble_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_bubble_scroll.custom_minimum_size.x = 380
	_bubble_text.bbcode_enabled = true
	_bubble_text.fit_content = true
	_bubble_text.scroll_active = false
	_bubble_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_bubble_text.custom_minimum_size.x = BUBBLE_MAX_WIDTH - 112
	_bubble_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_bubble_text.add_theme_color_override("default_color", INK)
	_bubble_text.finished.connect(func(): _layout.call_deferred())  # new text laid out: refit
	_bubble_scroll.add_child(_bubble_text)
	_bubble.add_child(_bubble_scroll)
	add_child(_bubble)
	# The tail: a small white triangle under the bubble, pointing at the Mii's head.
	_tail.size = Vector2(36, 24)
	_tail.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_tail.draw.connect(
		func():
			_tail.draw_colored_polygon(
				PackedVector2Array([Vector2(0, 0), Vector2(36, 0), Vector2(18, 22)]), Color.WHITE
			)
	)
	add_child(_tail)


## Bottom left: "At the Workshop", then the resident → what they're on → the building.
func _build_who() -> Control:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 2)
	# Godot concept: anchors pin a Control to a corner of its parent; the offsets are
	# distances from that corner, and grow_* says which way it grows when it gets bigger.
	box.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	box.offset_left = 24
	box.offset_bottom = -22
	box.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_where.add_theme_font_size_override("font_size", 26)
	_where.add_theme_color_override("font_color", Color.WHITE)
	_where.add_theme_color_override("font_outline_color", Color(0.16, 0.18, 0.31, 0.6))
	_where.add_theme_constant_override("outline_size", 10)
	box.add_child(_where)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	_headshot.custom_minimum_size = Vector2(110, 110)
	_headshot.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_headshot.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	row.add_child(_headshot)
	_arrow.custom_minimum_size = Vector2(250, 54)
	_arrow.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_arrow)
	_building_disc.custom_minimum_size = Vector2(92, 92)
	_building_disc.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_building_icon.custom_minimum_size = Vector2(44, 44)
	_building_icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_building_icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_building_icon.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_building_icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_building_disc.add_child(_building_icon)
	row.add_child(_building_disc)
	box.add_child(row)
	box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return box


## Bottom right: what you said last, two answer pills, and a box to type anything.
func _build_answers() -> Control:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 12)
	box.set_anchors_preset(Control.PRESET_BOTTOM_RIGHT)
	box.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	box.grow_vertical = Control.GROW_DIRECTION_BEGIN
	box.offset_right = -28
	box.offset_bottom = -26
	box.alignment = BoxContainer.ALIGNMENT_END

	_you_said.add_theme_font_size_override("font_size", 15)
	_you_said.add_theme_color_override("font_color", MUTED)
	_you_said.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_you_said.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_you_said.custom_minimum_size.x = 420
	box.add_child(_you_said)

	for i in 2:
		var button := Button.new()
		if i == 0:
			button.set_script(AnswerButton)  # the wavy pill, painted yellow here
			button.base_color = AnswerButton.YELLOW[0]
			button.wave_color = AnswerButton.YELLOW[1]
			button.theme_type_variation = &"AnswerButton"
		else:
			for state in ["normal", "hover", "pressed", "disabled", "focus"]:
				var white := _box(Color.WHITE if state != "hover" else Color("FAFAFA"), 34, Color.TRANSPARENT, 0, 10)
				button.add_theme_stylebox_override(state, white)
		button.custom_minimum_size = Vector2(420, 66)
		button.focus_mode = Control.FOCUS_NONE
		button.add_theme_font_size_override("font_size", 25)
		for c in [
			"font_color", "font_hover_color", "font_pressed_color", "font_focus_color", "font_hover_pressed_color"
		]:
			button.add_theme_color_override(c, INK)
		button.add_theme_color_override("font_disabled_color", MUTED)
		button.pressed.connect(_on_answer.bind(i))
		_answers.append(button)
		box.add_child(button)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	_input.custom_minimum_size = Vector2(320, 50)
	_input.max_length = 2000
	_input.add_theme_font_size_override("font_size", 17)
	_input.add_theme_color_override("font_color", INK)
	_input.add_theme_stylebox_override(
		"normal", _box(Color(1, 1, 1, 0.92), 25, Color.TRANSPARENT, 0, 6, Vector4(20, 8, 20, 8))
	)
	_input.add_theme_stylebox_override("focus", _box(Color.WHITE, 25, Color("C9CEF0"), 2, 6, Vector4(20, 8, 20, 8)))
	_input.text_submitted.connect(func(text: String): _send_text(text))
	row.add_child(_input)
	_send.set_script(AnswerButton)
	_send.base_color = AnswerButton.YELLOW[0]
	_send.wave_color = AnswerButton.YELLOW[1]
	_send.theme_type_variation = &"AnswerButton"
	_send.text = "Say it"
	_send.custom_minimum_size = Vector2(92, 50)
	_send.focus_mode = Control.FOCUS_NONE
	_send.add_theme_font_size_override("font_size", 17)
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color"]:
		_send.add_theme_color_override(c, INK)
	_send.pressed.connect(func(): _send_text(_input.text))
	row.add_child(_send)
	box.add_child(row)
	return box


## Top right: History, then ✕.
func _top_buttons() -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	row.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	row.offset_right = -24
	row.offset_top = 20
	row.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	var history := Button.new()
	history.text = "History"
	history.custom_minimum_size = Vector2(110, 46)
	history.focus_mode = Control.FOCUS_NONE
	history.tooltip_text = "Everything said in talks at this building"
	history.add_theme_font_size_override("font_size", 18)
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color"]:
		history.add_theme_color_override(c, INK)
	for state in ["normal", "focus", "hover", "pressed"]:
		var bg := Color.WHITE if state != "hover" else Color("F1F2F8")
		history.add_theme_stylebox_override(state, _box(bg, 23, Color.TRANSPARENT, 0, 8, Vector4(16, 8, 16, 8)))
	history.pressed.connect(_toggle_history)
	row.add_child(history)
	row.add_child(_close_button())
	return row


## History: a white card over the room, listing what was said at this building.
func _build_history() -> Control:
	_history.visible = false
	_history.set_anchors_preset(Control.PRESET_CENTER)
	_history.custom_minimum_size = Vector2(640, 500)
	_history.offset_left = -320
	_history.offset_top = -250
	_history.offset_right = 320
	_history.offset_bottom = 250
	_history.add_theme_stylebox_override(
		"panel", _box(Color.WHITE, 28, Color.TRANSPARENT, 0, 24, Vector4(28, 22, 28, 22))
	)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 12)
	var header := HBoxContainer.new()
	_history_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_history_title.add_theme_font_size_override("font_size", 26)
	_history_title.add_theme_color_override("font_color", INK)
	header.add_child(_history_title)
	var done := Button.new()
	done.text = "Close"
	done.theme_type_variation = &"OutlineButton"
	done.focus_mode = Control.FOCUS_NONE
	done.custom_minimum_size = Vector2(90, 38)
	done.pressed.connect(func(): _history.visible = false)
	header.add_child(done)
	column.add_child(header)
	_history_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_history_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_history_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_history_list.add_theme_constant_override("separation", 10)
	_history_scroll.add_child(_history_list)
	column.add_child(_history_scroll)
	_history.add_child(column)
	return _history


func _close_button() -> Button:
	var button := Button.new()
	button.icon = CLOSE_ICON
	button.custom_minimum_size = Vector2(46, 46)
	button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button.expand_icon = true
	button.focus_mode = Control.FOCUS_NONE
	button.tooltip_text = "Close (Esc)"
	for state in ["icon_normal_color", "icon_hover_color", "icon_pressed_color"]:
		button.add_theme_color_override(state, INK)
	for state in ["normal", "focus", "hover", "pressed"]:
		var bg := Color.WHITE if state != "hover" else Color("F1F2F8")
		button.add_theme_stylebox_override(state, _box(bg, 23, Color.TRANSPARENT, 0, 8, Vector4(12, 12, 12, 12)))
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
		style.shadow_color = Color(0.16, 0.18, 0.31, 0.16)
		style.shadow_offset = Vector2(0, 5)
	style.content_margin_left = pad.x
	style.content_margin_top = pad.y
	style.content_margin_right = pad.z
	style.content_margin_bottom = pad.w
	style.anti_aliasing = true
	return style


## The green "Wants to be friends"-style arrow: a rounded pill with a point on the right,
## white-edged, with a line of text in it.
class ArrowPill:
	extends Control

	const GREEN := Color("8FD9A8")
	const DARK := Color("1F6B47")

	var text := "":
		set(value):
			text = value
			queue_redraw()

	func _draw() -> void:
		var w := size.x
		var h := size.y
		var head := h * 0.55
		var points := PackedVector2Array(
			[Vector2(6, 6), Vector2(w - head, 6), Vector2(w - head, 0), Vector2(w, h / 2.0)]
		)
		points.append_array(PackedVector2Array([Vector2(w - head, h), Vector2(w - head, h - 6), Vector2(6, h - 6)]))
		draw_colored_polygon(points, GREEN)
		points.append(points[0])
		draw_polyline(points, Color.WHITE, 4.0, true)
		var font := get_theme_default_font()
		var font_size := 20
		var width := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
		var room := w - head - 16
		while width > room and font_size > 12:
			font_size -= 1
			width = font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
		var baseline := h / 2.0 + font.get_ascent(font_size) / 2.0 - 2
		draw_string(
			font, Vector2(14 + (room - width) / 2.0, baseline), text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, DARK
		)
