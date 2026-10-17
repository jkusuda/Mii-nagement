class_name ResidentCreator
extends PanelContainer
## The resident creator: name, colour, catchphrase, job, brain (ACP agent), the
## four personality sliders, gifts (MCP servers), and house keys (permissions).
## A live 3D preview of the Mii on the left follows every change.
##
## The same screen edits an existing resident: open() it with their data.
##
## The fields match the Resident table (backend/mii/models.py). `gifts` and
## `keys` are new: see docs/mii-customization.md for how they reach ACP.
##
## "Move in" / "Save" emits `saved`; game.gd sends it to the backend
## (Net.send_resident) and comes back here with show_error() if it's refused.

signal saved(resident: Dictionary)
signal cancelled

var brains_url := Net.url.replace("ws://", "http://").replace("/ws", "/brains")
const ARROW := preload("res://assets/ui/dropdown_arrow.svg")

const SURPRISE_NAMES := ["Mo", "Bea", "Otto", "Pip", "Juniper", "Rory", "Tess", "Kai", "Nell", "Ivy", "Sol", "Wren"]
const SURPRISE_PHRASES := ["Let me poke at it!", "Tiny steps!", "Ship it, but safely!", "Hmm, interesting…",
	"One more test!", "Let's map it out!", "Tidy code, happy island."]

## Same buckets as level() in backend/mii/personality.py, as short phrases for
## the "How Mo will act" box. [low, medium, high]
const ACT_PHRASES := {
	"caution": ["moves fast and takes reasonable risks", "balances speed with care", "asks before anything risky"],
	"chattiness": ["keeps notes short", "explains briefly", "explains in friendly detail"],
	"creativity": ["sticks to well-known solutions", "is open to new ideas", "suggests creative alternatives"],
	"bluntness": ["is gentle with bad news", "is direct but polite", "is blunt about what's wrong"],
}

var _data := ResidentData.blank()
var _editing := false
var _swatches: Array[Button] = []
var _job_buttons := {}
var _sliders := {}
var _gift_buttons := {}
var _key_pickers := {}
var _brain_ids: Array = ResidentData.BRAINS.keys()

@onready var preview: MiiPreview = %Preview


func _ready() -> void:
	_build_swatches()
	_build_jobs()
	_build_brains()
	_build_sliders()
	_build_gifts()
	_build_keys()

	# Godot concept: text_changed fires on every keystroke, so the preview
	# updates while you type.
	%NameEdit.text_changed.connect(func(text: String):
		_data["name"] = text
		_refresh())
	%CatchphraseEdit.text_changed.connect(func(text: String):
		_data["catchphrase"] = text
		_refresh())
	%ModelEdit.text_changed.connect(func(text: String):
		_data["model"] = text if text.strip_edges() else null)
	%SurpriseButton.pressed.connect(_surprise)
	%NextAnimButton.pressed.connect(preview.next_animation)
	%CancelButton.pressed.connect(cancelled.emit)
	%MoveInButton.pressed.connect(_on_move_in)
	open()


## Show the creator, empty (a new resident) or filled in (editing one).
func open(resident: Dictionary = {}, island_name := "Pebble Cove") -> void:
	_editing = not resident.is_empty()
	_data = ResidentData.blank().merged(resident, true)
	%Title.text = "Edit %s" % resident.get("name", "") if _editing else "Create a resident"
	%MovingLabel.text = ("Lives on %s" if _editing else "Moving to %s") % island_name
	%MoveInButton.text = "Save" if _editing else "Move in"
	%MoveInButton.disabled = false
	_load_widgets()
	_refresh()
	_check_brains()


## Copy _data into every widget (after open() or "Surprise me").
func _load_widgets() -> void:
	%NameEdit.text = str(_data["name"])
	%CatchphraseEdit.text = str(_data["catchphrase"])
	%ModelEdit.text = str(_data["model"]) if _data["model"] else ""
	_job_buttons.get(_data["job"], _job_buttons["coder"]).button_pressed = true
	%BrainPicker.select(maxi(_brain_ids.find(_data["brain"]), 0))
	for trait_name in _sliders:
		_sliders[trait_name].set_value_no_signal(float(_data[trait_name]))
	for gift in _gift_buttons:
		_gift_buttons[gift].set_pressed_no_signal(gift in _data["gifts"])
	for key in _key_pickers:
		var choices: Array = ResidentData.KEY_CHOICES.keys()
		_key_pickers[key].select(choices.find(_data["keys"].get(key, "ask")))


## Update everything that depends on _data: preview, labels, colours.
func _refresh() -> void:
	var color := Color(str(_data["color"]))
	var shown_name: String = _data["name"] if _data["name"] else "New friend"
	preview.set_color(color)
	%PreviewName.text = shown_name
	%Speech.text = "“%s”" % (_data["catchphrase"] if _data["catchphrase"] else "Hi there!")
	%JobLabel.text = str(_data["job"]).capitalize()
	%ActTitle.text = "How %s will act" % shown_name
	%ActText.text = _describe()
	var home: String = ResidentData.JOB_HOMES.get(_data["job"], "island")
	%FooterLabel.remove_theme_color_override("font_color")
	if _editing:
		%FooterLabel.text = "Changes start with %s's next package." % shown_name
	else:
		%FooterLabel.text = "%s will move into the %s and pick up the next package there." % [shown_name, home]

	_tint(%PreviewPanel, "SectionPanel", color.lerp(Color.WHITE, 0.82))
	_tint(%JobPill, "TagPill", color.darkened(0.1))
	for swatch in _swatches:
		var style: StyleBoxFlat = swatch.get_theme_stylebox("normal")
		var selected := Color(swatch.get_meta("color")) == color
		style.border_color = Color("292E50") if selected else Color.WHITE


## "Asks before anything risky, keeps notes short, ... and is gentle with bad news."
func _describe() -> String:
	var parts := []
	for trait_name in ["caution", "chattiness", "creativity", "bluntness"]:
		var value: float = _data[trait_name]
		var level := 0 if value < 1.0 / 3.0 else (1 if value < 2.0 / 3.0 else 2)
		parts.append(ACT_PHRASES[trait_name][level])
	var text := "%s, %s, %s, and %s." % parts
	return text.left(1).to_upper() + text.substr(1)


func _surprise() -> void:
	_data["name"] = SURPRISE_NAMES.pick_random()
	_data["catchphrase"] = SURPRISE_PHRASES.pick_random()
	_data["color"] = ResidentData.COLORS.pick_random()
	_data["job"] = ResidentData.JOBS.pick_random()
	for t in ResidentData.TRAITS:
		_data[t[0]] = snappedf(randf(), 0.05)
	_load_widgets()
	_refresh()


func _on_move_in() -> void:
	if str(_data["name"]).strip_edges() == "":
		%NameEdit.grab_focus()
		%NameEdit.placeholder_text = "Everyone needs a name!"
		return
	# A new resident has no id yet: the backend picks it (and their home building).
	%MoveInButton.disabled = true
	saved.emit(_data.duplicate(true))


## The backend refused the resident (an `error` event). Show why, and let the
## player fix it and try again.
func show_error(message: String) -> void:
	%MoveInButton.disabled = false
	%FooterLabel.text = "Hmm, that didn't work: %s" % message
	%FooterLabel.add_theme_color_override("font_color", Color("C0392B"))


# --- Building the widgets ------------------------------------------------------

func _build_swatches() -> void:
	for hex in ResidentData.COLORS:
		var swatch := Button.new()
		swatch.custom_minimum_size = Vector2(26, 26)
		swatch.focus_mode = Control.FOCUS_NONE
		swatch.set_meta("color", hex)
		swatch.tooltip_text = hex
		var style: StyleBoxFlat = get_theme_stylebox("panel", "AvatarCircle").duplicate()
		style.bg_color = Color(hex)
		style.set_border_width_all(3)
		style.set_corner_radius_all(13)
		for state in ["normal", "hover", "pressed", "hover_pressed"]:
			swatch.add_theme_stylebox_override(state, style)
		swatch.pressed.connect(func():
			_data["color"] = hex
			_refresh())
		%Swatches.add_child(swatch)
		_swatches.append(swatch)


func _build_jobs() -> void:
	# Godot concept: a ButtonGroup makes toggle buttons act like radio buttons.
	var group := ButtonGroup.new()
	for job in ResidentData.JOBS:
		var button := Button.new()
		button.text = job.capitalize()
		button.toggle_mode = true
		button.button_group = group
		button.theme_type_variation = &"ChoiceButton"
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.focus_mode = Control.FOCUS_NONE
		button.toggled.connect(func(on: bool):
			if on:
				_data["job"] = job
				_refresh())
		%Jobs.add_child(button)
		_job_buttons[job] = button


func _build_brains() -> void:
	%BrainPicker.add_theme_icon_override("arrow", ARROW)
	for id in _brain_ids:
		%BrainPicker.add_item(ResidentData.BRAINS[id])
	%BrainPicker.item_selected.connect(func(index: int): _data["brain"] = _brain_ids[index])


func _build_sliders() -> void:
	for t in ResidentData.TRAITS:
		var header := HBoxContainer.new()
		var title := Label.new()
		title.text = t[1]
		title.theme_type_variation = &"FieldLabel"
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var ends := Label.new()
		ends.text = "%s – %s" % [t[2], t[3]]
		ends.theme_type_variation = &"LabelMuted"
		ends.add_theme_font_size_override("font_size", 11)
		header.add_child(title)
		header.add_child(ends)

		var slider := HSlider.new()
		slider.max_value = 1.0
		slider.step = 0.01
		slider.focus_mode = Control.FOCUS_NONE
		slider.value_changed.connect(func(value: float):
			_data[t[0]] = value
			_refresh())
		%Sliders.add_child(header)
		%Sliders.add_child(slider)
		_sliders[t[0]] = slider


func _build_gifts() -> void:
	for g in ResidentData.GIFTS:
		var tile := Button.new()
		tile.text = g[1]
		tile.icon = g[2]
		tile.toggle_mode = true
		tile.theme_type_variation = &"GiftTile"
		tile.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
		tile.vertical_icon_alignment = VERTICAL_ALIGNMENT_TOP
		tile.custom_minimum_size = Vector2(0, 56)
		tile.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		tile.focus_mode = Control.FOCUS_NONE
		tile.tooltip_text = "Hand over the %s MCP server" % g[1]
		tile.toggled.connect(func(on: bool):
			if on and g[0] not in _data["gifts"]:
				_data["gifts"].append(g[0])
			elif not on:
				_data["gifts"].erase(g[0]))
		%Gifts.add_child(tile)
		_gift_buttons[g[0]] = tile


func _build_keys() -> void:
	var choices: Array = ResidentData.KEY_CHOICES.keys()
	for k in ResidentData.HOUSE_KEYS:
		var label := Label.new()
		label.text = k[1]
		label.theme_type_variation = &"BodyText"
		label.tooltip_text = "ACP tool kinds: %s" % k[2]
		label.mouse_filter = Control.MOUSE_FILTER_PASS  # so the tooltip shows
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var picker := OptionButton.new()
		picker.theme_type_variation = &"OutlineButton"
		picker.add_theme_icon_override("arrow", ARROW)
		picker.add_theme_constant_override("h_separation", 6)
		picker.custom_minimum_size = Vector2(96, 30)
		picker.focus_mode = Control.FOCUS_NONE
		for choice in choices:
			picker.add_item(ResidentData.KEY_CHOICES[choice])
		picker.item_selected.connect(func(index: int): _data["keys"][k[0]] = choices[index])
		%Keys.add_child(label)
		%Keys.add_child(picker)
		_key_pickers[k[0]] = picker


## Ask the backend which brains are installed, and grey out the rest.
## Godot concept: HTTPRequest is a node; request() returns right away and the
## `request_completed` signal fires later with the response.
func _check_brains() -> void:
	var http := HTTPRequest.new()
	add_child(http)
	http.request_completed.connect(func(result: int, code: int, _headers, body: PackedByteArray):
		http.queue_free()
		if result != HTTPRequest.RESULT_SUCCESS or code != 200:
			return  # backend offline: leave every brain selectable
		var installed = JSON.parse_string(body.get_string_from_utf8())
		if typeof(installed) != TYPE_DICTIONARY:
			return
		for i in _brain_ids.size():
			var ok: bool = installed.get(_brain_ids[i], false)
			%BrainPicker.set_item_disabled(i, not ok)
			%BrainPicker.set_item_text(i, ResidentData.BRAINS[_brain_ids[i]] + ("" if ok else " (not installed)")))
	http.request(brains_url)


func _tint(node: Control, theme_type: String, color: Color) -> void:
	var style: StyleBoxFlat = get_theme_stylebox("panel", theme_type).duplicate()
	style.bg_color = color
	node.add_theme_stylebox_override("panel", style)
