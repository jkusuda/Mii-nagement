class_name TalkRoom
extends Control
## "Talk" on a resident card in a BuildingPanel: the whole window becomes the
## room they work in, with just them in it. What they say shows as a speech
## bubble above their head; when they're waiting on you (a bubble from the
## server), your answer goes in the bar at the bottom.
##
## The answer goes to Net.send_answer(), like AnswerPopup. Everything else is
## display: the resident's look, line and bubble come from Net.island and
## refresh on Net.island_changed. Esc or ✕ closes it, back to the island.
##
## Godot concept: the room is a SubViewport with `own_world_3d`, a 3D world of
## its own (see mii_preview.gd), shown through a SubViewportContainer that fills
## the window. The 2D speech bubble sits on top and follows the Mii's head each
## frame with Camera3D.unproject_position() (a 3D point -> a spot on screen).

signal closed

const MII_SCENE := preload("res://scenes/mii/mii.tscn")
const CLOSE_ICON := preload("res://assets/icons/x.svg")
const INK := Color("292E50")
const CREAM := Color("FFFDF3")
const HEAD := Vector3(0, 1.95, 0)  # just above the Mii's head, where the bubble's tail points
const BUBBLE_WIDTH := 460.0
## What they say when nothing needs you, by state.
const GREETINGS := {
	"working": "Hi! Busy at the %s, but happy to see you.",
	"sleeping": "z Z... oh! Hi.",
	"walking": "On my way, can't stop long!",
	"idle": "Hi! Nothing needs you right now.",
}

var resident_id := ""
var _bubble_id := ""  # the open bubble shown, "" when none
var _line := ""  # what the speech bubble says now

var _view := SubViewportContainer.new()
var _viewport := SubViewport.new()
var _camera := Camera3D.new()
var _floor := MeshInstance3D.new()
var _wall := MeshInstance3D.new()
var _mii: Mii
var _hop: Tween

var _name := Label.new()
var _about := Label.new()
var _speech := PanelContainer.new()
var _speech_text := Label.new()
var _speech_detail := Label.new()
var _tail := Polygon2D.new()
var _answer_bar := PanelContainer.new()
var _answer_row := HBoxContainer.new()
var _reply: LineEdit


func _init() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	visible = false
	_build_room()
	_build_overlay()
	Net.island_changed.connect(
		func():
			if visible:
				_refresh()
	)
	Net.bubble_resolved.connect(
		func(id: String):
			if visible and id == _bubble_id:
				_thank()
	)
	Net.resident_removed.connect(
		func(id: String):
			if id == resident_id:
				close()
	)


## Open the room for one resident (an id from Net.island["residents"]).
func open(id: String) -> void:
	resident_id = id
	_bubble_id = ""
	_line = ""
	var resident := Net.find_resident(id)
	if resident.is_empty():
		return
	var info: Dictionary = BuildingData.TYPES.get(_building_type(resident), BuildingData.TYPES["workshop"])
	var color: Color = info["color"]
	_floor.material_override = _matte(color.lerp(Color.WHITE, 0.55))
	_wall.material_override = _matte(color.lerp(Color.WHITE, 0.8))
	_viewport.world_3d.environment.background_color = color.lerp(Color.WHITE, 0.85)
	_refresh()
	visible = true
	modulate.a = 0.0
	create_tween().tween_property(self, "modulate:a", 1.0, 0.2)
	_hop_once()


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


func _process(_delta: float) -> void:
	if not visible or not _speech.visible:
		return
	# Keep the bubble above the head, its tail's tip on the head point.
	var head := _camera.unproject_position(_mii.global_position + HEAD)
	_speech.position = Vector2(head.x - _speech.size.x / 2.0, head.y - _speech.size.y - 18)
	_tail.position = Vector2(_speech.size.x / 2.0, _speech.size.y)


# --- What's shown -------------------------------------------------------------------


## Redraw from Net.island: the Mii's look, the name, and their line. The answer
## bar is only rebuilt when the bubble changes, so typing isn't thrown away.
func _refresh() -> void:
	var resident := Net.find_resident(resident_id)
	if resident.is_empty():
		close()
		return
	_mii.color = Color(str(resident.get("color", "#FFFFFF")))
	var look := MiiParts.for_resident(resident)
	if look != _mii.appearance:
		_mii.appearance = look
	_name.text = str(resident.get("name", "?"))
	var place := str(Net.find_building(str(resident.get("building", ""))).get("name", "island"))
	_about.text = (
		"%s · at the %s · feeling %s"
		% [str(resident.get("job", "")).capitalize(), place, ResidentData.vibe_word(resident)]
	)

	var bubble := Net.bubble_of(resident_id)
	var bubble_id := str(bubble.get("id", ""))
	if bubble_id != _bubble_id:
		_bubble_id = bubble_id
		_build_answer(bubble)
		if bubble_id:
			_hop_once()
	if bubble_id:
		_say(str(bubble["text"]), Net.text_or_empty(bubble.get("detail")))
	elif resident.get("activity"):
		_say(str(resident["activity"]))
	elif _line.is_empty():  # keep "Thanks!" up rather than flipping back to hello
		var state := str(resident.get("state", "idle"))
		var greeting: String = GREETINGS.get(state, GREETINGS["idle"])
		_say(greeting % place if "%s" in greeting else greeting)


## Put a line in the speech bubble (and a smaller detail line under it, e.g. a command).
func _say(text: String, detail := "") -> void:
	_line = text
	_speech_text.text = text
	_speech_detail.text = detail
	_speech_detail.visible = not detail.is_empty()
	_speech.visible = not text.is_empty()
	_speech.reset_size()  # shrink back to fit a shorter line


## The bar under the room: the bubble's options as buttons, and a text box when
## there's something to type (no options, or a note with a review).
func _build_answer(bubble: Dictionary) -> void:
	for child in _answer_row.get_children():
		_answer_row.remove_child(child)
		child.queue_free()
	_reply = null
	_answer_bar.visible = not bubble.is_empty()
	if bubble.is_empty():
		return
	var kind := str(bubble.get("kind", ""))
	var options: Array = bubble.get("options") if bubble.get("options") else []
	if options.is_empty() or kind == "ready":
		_reply = LineEdit.new()
		_reply.custom_minimum_size = Vector2(360, 42)
		_reply.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_reply.placeholder_text = "Type your answer…" if options.is_empty() else "A note to send with it (optional)"
		_reply.text_submitted.connect(func(_text: String): _send(str(options[0]) if options else ""))
		_answer_row.add_child(_reply)
		_reply.grab_focus.call_deferred()
	if options.is_empty():
		_answer_row.add_child(_button("Send", true, _send.bind("")))
		return
	# The first option is the main one (Approve, Allow once...): orange, last.
	for option in options.slice(1):
		_answer_row.add_child(_button(str(option), false, _send.bind(str(option))))
	_answer_row.add_child(_button(str(options[0]), true, _send.bind(str(options[0]))))


## Send `option` (plus the typed note), or just the typed text when there are no options.
func _send(option: String) -> void:
	var typed := _reply.text.strip_edges() if _reply else ""
	var text := option + (": " + typed if typed else "") if option else typed
	if text.is_empty():
		if _reply:
			_reply.grab_focus()
		return
	if Net.send_answer(_bubble_id, text):
		_thank()


## Answered (here or somewhere else): they say thanks, hop, and the bar goes away.
func _thank() -> void:
	_bubble_id = ""
	_build_answer({})
	_say("Thanks! Back to work I go.")
	_hop_once()


func _hop_once() -> void:
	if _hop:
		_hop.kill()
	_hop = create_tween()
	_hop.tween_property(_mii, "position:y", 0.25, 0.16).set_ease(Tween.EASE_OUT)
	_hop.tween_property(_mii, "position:y", 0.0, 0.16).set_ease(Tween.EASE_IN)


# --- Building it --------------------------------------------------------------------


## The 3D room: a floor, a back wall, a soft light, the Mii, and a camera on them.
func _build_room() -> void:
	_view.set_anchors_preset(Control.PRESET_FULL_RECT)
	_view.stretch = true  # the viewport takes the container's size, so 3D -> screen spots line up
	add_child(_view)
	_viewport.own_world_3d = true
	_viewport.msaa_3d = Viewport.MSAA_2X
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color.WHITE
	env.ambient_light_energy = 0.7
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	_viewport.world_3d = World3D.new()
	_viewport.world_3d.environment = env
	_view.add_child(_viewport)

	_camera.fov = 32.0
	_camera.position = Vector3(0, 1.35, 5.2)
	_camera.rotation.x = -0.05
	_viewport.add_child(_camera)
	var sun := DirectionalLight3D.new()
	sun.rotation = Vector3(-0.8, 0.4, 0)
	sun.light_energy = 0.9
	sun.shadow_enabled = true
	_viewport.add_child(sun)

	var plane := PlaneMesh.new()
	plane.size = Vector2(14, 10)
	_floor.mesh = plane
	_viewport.add_child(_floor)
	var wall := PlaneMesh.new()
	wall.size = Vector2(14, 8)
	wall.orientation = PlaneMesh.FACE_Z
	_wall.mesh = wall
	_wall.position = Vector3(0, 4, -2.5)
	_viewport.add_child(_wall)

	_mii = MII_SCENE.instantiate()
	_viewport.add_child(_mii)


## The 2D parts over the room: name card, ✕, the speech bubble, the answer bar.
func _build_overlay() -> void:
	var top := HBoxContainer.new()
	top.set_anchors_preset(Control.PRESET_TOP_WIDE)
	top.offset_left = 32
	top.offset_top = 28
	top.offset_right = -32
	add_child(top)
	var names := VBoxContainer.new()
	names.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_name.theme_type_variation = &"ScreenTitle"
	_name.add_theme_font_size_override("font_size", 34)
	_about.theme_type_variation = &"BodyText"
	names.add_child(_name)
	names.add_child(_about)
	top.add_child(names)
	var close_button := Button.new()
	close_button.icon = CLOSE_ICON
	close_button.theme_type_variation = &"FlatIconButton"
	close_button.custom_minimum_size = Vector2(42, 42)
	close_button.expand_icon = true
	close_button.focus_mode = Control.FOCUS_NONE
	close_button.tooltip_text = "Back to the island (Esc)"
	close_button.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	close_button.pressed.connect(close)
	top.add_child(close_button)

	# The speech bubble: a white rounded box with a dark outline and a tail.
	var style := StyleBoxFlat.new()
	style.bg_color = Color.WHITE
	style.set_corner_radius_all(26)
	style.border_color = INK
	style.set_border_width_all(3)
	style.set_content_margin_all(18)
	style.anti_aliasing = true
	_speech.add_theme_stylebox_override("panel", style)
	_speech.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var lines := VBoxContainer.new()
	lines.add_theme_constant_override("separation", 6)
	_speech_text.theme_type_variation = &"QuoteText"
	_speech_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_speech_text.custom_minimum_size.x = BUBBLE_WIDTH
	lines.add_child(_speech_text)
	_speech_detail.theme_type_variation = &"LabelMuted"
	_speech_detail.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
	_speech_detail.custom_minimum_size.x = BUBBLE_WIDTH
	_speech_detail.max_lines_visible = 4
	lines.add_child(_speech_detail)
	_speech.add_child(lines)
	# Godot concept: a Polygon2D draws a filled shape; as a child of a Control it
	# moves with it. Drawn behind the box (show_behind_parent) so the outline stays clean.
	_tail.polygon = PackedVector2Array([Vector2(-16, -3), Vector2(16, -3), Vector2(0, 18)])
	_tail.color = INK
	_tail.show_behind_parent = true
	_speech.add_child(_tail)
	add_child(_speech)

	var bar_style := StyleBoxFlat.new()
	bar_style.bg_color = CREAM
	bar_style.set_corner_radius_all(24)
	bar_style.set_content_margin_all(14)
	bar_style.shadow_size = 18
	bar_style.shadow_color = Color(0.16, 0.18, 0.31, 0.18)
	_answer_bar.add_theme_stylebox_override("panel", bar_style)
	_answer_bar.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	_answer_bar.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_answer_bar.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_answer_bar.offset_bottom = -32
	_answer_row.add_theme_constant_override("separation", 10)
	_answer_bar.add_child(_answer_row)
	_answer_bar.visible = false
	add_child(_answer_bar)


func _button(text: String, main: bool, pressed: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.custom_minimum_size = Vector2(maxf(110, 24 + 9 * text.length()), 42)
	button.pressed.connect(pressed)
	if main:
		button.theme_type_variation = &"AnswerButton"
		button.set_script(AnswerButton)  # the wavy orange pill (painted in its _ready)
	else:
		button.theme_type_variation = &"OutlineButton"
	return button


func _building_type(resident: Dictionary) -> String:
	return str(Net.find_building(str(resident.get("building", ""))).get("type", "workshop"))


static func _matte(color: Color) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 1.0
	return material
