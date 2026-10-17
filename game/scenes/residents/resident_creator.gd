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
## "Move away" (editing only) emits `move_away_requested` after asking first.
##
## Gifts the backend can't run on this computer (GET /gifts) are greyed out.
## "Works at" picks the buildings this resident runs (`buildings` in
## create_resident / update_resident): they take over from whoever was there.

signal saved(resident: Dictionary)
signal cancelled
signal move_away_requested(resident_id: String)

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
var _gift_info := {}  # GET /gifts: gift -> {ok, label, hint}; empty until it answers
var _building_chips := {}  # building id -> toggle Button ("Works at")
var _works_at_now: Array = []  # building ids this resident runs already (editing)
var _works_at_box := VBoxContainer.new()
var _live := true  # false while drafting a resident for a new island (not on Net.island yet)
var _move_away := Button.new()
var _key_pickers := {}
var _brain_ids: Array = ResidentData.BRAINS.keys()
## OpenRouter model ids for the model picker (GET /models), loaded the first
## time the openrouter brain is picked.
var _model_ids: Array = []
var _model_picker := OptionButton.new()

@onready var preview: MiiPreview = %Preview


func _ready() -> void:
	_build_swatches()
	_build_jobs()
	_build_brains()
	_build_sliders()
	_build_gifts()
	_build_keys()
	_build_works_at()
	_build_move_away()

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
## `live` = false when drafting a resident for an island that doesn't exist yet.
func open(resident: Dictionary = {}, island_name := "Pebble Cove", live := true) -> void:
	_editing = not resident.is_empty()
	_live = live
	_data = ResidentData.blank().merged(resident, true)
	_data.erase("buildings")  # only sent when "Works at" changes
	_move_away.visible = live and _editing and not Net.find_resident(str(resident.get("id", ""))).is_empty()
	_fill_works_at()
	%Title.text = "Edit %s" % resident.get("name", "") if _editing else "Create a resident"
	%MovingLabel.text = ("Lives on %s" if _editing else "Moving to %s") % island_name
	%MoveInButton.text = "Save" if _editing else "Move in"
	%MoveInButton.disabled = false
	_load_widgets()
	_refresh()
	_check_brains()
	_check_gifts()


## Copy _data into every widget (after open() or "Surprise me").
func _load_widgets() -> void:
	%NameEdit.text = str(_data["name"])
	%CatchphraseEdit.text = str(_data["catchphrase"])
	%ModelEdit.text = str(_data["model"]) if _data["model"] else ""
	_show_model_picker()
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
	var picked := _picked_buildings().map(func(id): return str(Net.find_building(id).get("name", id)))
	if picked.size() > _works_at_now.size():
		%FooterLabel.text = "%s will work at the %s." % [shown_name, " and the ".join(picked)]
	elif _editing:
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
	var data := _data.duplicate(true)
	var picked := _picked_buildings()
	if picked.size() > _works_at_now.size():
		data["buildings"] = picked  # take over these buildings
	%MoveInButton.disabled = true
	saved.emit(data)


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
	%BrainPicker.item_selected.connect(func(index: int):
		_data["brain"] = _brain_ids[index]
		_show_model_picker())

	# The OpenRouter catalogue, next to the model field (only for that brain).
	_model_picker.theme_type_variation = &"OutlineButton"
	_model_picker.add_theme_icon_override("arrow", ARROW)
	_model_picker.custom_minimum_size = Vector2(0, 34)
	_model_picker.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_model_picker.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_model_picker.focus_mode = Control.FOCUS_NONE
	_model_picker.tooltip_text = "Pick any model from OpenRouter's catalogue"
	_model_picker.visible = false
	_model_picker.item_selected.connect(func(index: int):
		if index > 0:
			%ModelEdit.text = _model_ids[index - 1]
			_data["model"] = _model_ids[index - 1])
	%ModelEdit.add_sibling(_model_picker)


## Show the OpenRouter model picker when that brain is picked (loading the
## catalogue from GET /models the first time).
func _show_model_picker() -> void:
	_model_picker.visible = _data["brain"] == "openrouter"
	if not _model_picker.visible:
		return
	if not _model_ids.is_empty():
		_model_picker.select(_model_ids.find(%ModelEdit.text) + 1)
		return
	if _model_picker.item_count > 0 and _model_picker.get_item_text(0) == "Loading models…":
		return
	_model_picker.clear()
	_model_picker.add_item("Loading models…")
	Net.request_json("/models", func(ok: bool, data: Dictionary):
		_model_picker.clear()
		if not ok:  # e.g. no OpenRouter key yet: tried again next time
			_model_picker.add_item("No catalogue: %s" % data["error"])
			_model_picker.tooltip_text = str(data["error"])
			return
		_model_picker.add_item("Choose a model…")
		for model in data.get("models", []):
			_model_ids.append(str(model["id"]))
			_model_picker.add_item(str(model.get("name", model["id"])))
		_model_picker.select(_model_ids.find(%ModelEdit.text) + 1))


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
				_data["gifts"].erase(g[0])
			_show_gift(g[0]))
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


## Ask the backend which brains can run here (GET /brains/detail), grey out
## the rest, and say why in their tooltip ("run `codex login`").
func _check_brains() -> void:
	Net.request_json("/brains/detail", func(ok: bool, brains: Dictionary):
		if not ok:
			return  # backend offline: leave every brain selectable
		var tips := []
		for i in _brain_ids.size():
			var info: Dictionary = brains.get(_brain_ids[i], {})
			var ready: bool = info.get("ok", false)
			%BrainPicker.set_item_disabled(i, not ready)
			%BrainPicker.set_item_text(i, ResidentData.BRAINS[_brain_ids[i]] + ("" if ready else " (not set up)"))
			tips.append("%s: %s" % [ResidentData.BRAINS[_brain_ids[i]], info.get("hint", "unknown")])
		%BrainPicker.tooltip_text = "\n".join(tips))


## Ask the backend which gifts can run here (GET /gifts) and grey out the rest,
## with why in the tooltip ("install `npx` first").
func _check_gifts() -> void:
	Net.request_json("/gifts", func(ok: bool, gifts: Dictionary):
		if not ok:
			return  # backend offline: leave every gift selectable
		_gift_info = gifts
		for gift in _gift_buttons:
			_show_gift(gift))


## A gift that can't run here is greyed out, unless the resident already has
## it: then it stays clickable, so you can take it back.
func _show_gift(gift: String) -> void:
	var info: Dictionary = _gift_info.get(gift, {})
	if info.is_empty():
		return
	var tile: Button = _gift_buttons[gift]
	var ready: bool = info.get("ok", false)
	tile.disabled = not ready and not tile.button_pressed
	tile.tooltip_text = "%s: %s" % [info.get("label", gift), info.get("hint", "")] if ready \
		else "Can't hand this over yet: %s" % info.get("hint", "not set up")


# --- Works at ---------------------------------------------------------------------

func _build_works_at() -> void:
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	var title := Label.new()
	title.text = "Works at"
	title.theme_type_variation = &"SectionTitle"
	var hint := Label.new()
	hint.text = "takes over from whoever's there"
	hint.theme_type_variation = &"LabelMuted"
	hint.add_theme_font_size_override("font_size", 11)
	hint.size_flags_vertical = Control.SIZE_SHRINK_END
	header.add_child(title)
	header.add_child(hint)
	_works_at_box.add_theme_constant_override("separation", 8)
	_works_at_box.add_child(header)
	var chips := HFlowContainer.new()
	chips.name = "Chips"
	chips.add_theme_constant_override("h_separation", 6)
	chips.add_theme_constant_override("v_separation", 6)
	_works_at_box.add_child(chips)
	%BrainPicker.get_parent().add_sibling(_works_at_box)  # under Brain, where there's room


## One chip per building on the island: "Lab · Mo". The buildings this resident
## already runs are locked on: every building needs someone, so give it to
## another resident (in their creator) instead.
func _fill_works_at() -> void:
	var chips: HFlowContainer = _works_at_box.get_node("Chips")
	for child in chips.get_children():
		child.queue_free()
	_building_chips.clear()
	var id := str(_data.get("id", ""))
	var buildings: Array = Net.island.get("buildings", []) if _live else []
	_works_at_box.visible = not buildings.is_empty()
	_works_at_now = buildings.filter(func(b): return id != "" and str(b.get("resident", "")) == id) \
		.map(func(b): return str(b["id"]))
	for b in buildings:
		var bid := str(b["id"])
		var worker := str(Net.find_resident(str(b.get("resident", ""))).get("name", "nobody"))
		var mine := bid in _works_at_now
		var chip := Button.new()
		chip.text = "%s · %s" % [b.get("name", bid), "you" if mine else worker]
		chip.toggle_mode = true
		chip.button_pressed = mine
		chip.disabled = mine
		chip.theme_type_variation = &"FilterChip"
		chip.add_theme_stylebox_override("disabled", get_theme_stylebox("pressed", "FilterChip"))  # locked on
		for state in ["font_pressed_color", "font_hover_pressed_color", "font_disabled_color"]:
			chip.add_theme_color_override(state, Color("292E50"))  # dark ink on the yellow "on" chip
		chip.focus_mode = Control.FOCUS_NONE
		chip.tooltip_text = ("Works here already. To move them, give the %s to someone else." % b.get("name", bid)) if mine \
			else "Make them the worker at the %s (instead of %s)" % [b.get("name", bid), worker]
		chip.toggled.connect(func(_on): _refresh())
		chips.add_child(chip)
		_building_chips[bid] = chip


func _picked_buildings() -> Array:
	return _building_chips.keys().filter(func(bid): return _building_chips[bid].button_pressed)


# --- Move away --------------------------------------------------------------------

func _build_move_away() -> void:
	_move_away.text = "Move away"
	_move_away.theme_type_variation = &"OutlineButton"
	_move_away.add_theme_color_override("font_color", Color("C0392B"))
	_move_away.add_theme_color_override("font_hover_color", Color("C0392B"))
	_move_away.focus_mode = Control.FOCUS_NONE
	_move_away.visible = false
	_move_away.pressed.connect(func():
		var who := str(_data.get("name", "This resident"))
		ConfirmPopup.ask(self, "%s is moving away?" % who,
			"%s leaves the island for good. Their past work stays in Insights and News." % who,
			"Say goodbye", func(): move_away_requested.emit(str(_data.get("id", "")))))
	%CancelButton.add_sibling(_move_away)
	%CancelButton.get_parent().move_child(_move_away, %CancelButton.get_index())


func _tint(node: Control, theme_type: String, color: Color) -> void:
	var style: StyleBoxFlat = get_theme_stylebox("panel", theme_type).duplicate()
	style.bg_color = color
	node.add_theme_stylebox_override("panel", style)
