class_name ResidentCreator
extends PanelContainer
## The resident creator: name, colour, catchphrase, job, brain (ACP agent), the
## four personality sliders, gifts (MCP servers), and house keys (permissions)
## on the About page; skin, hair, eyes, brows, nose and mouth on the Look page.
## A live 3D preview of the Mii on the left follows every change.
##
## The same screen edits an existing resident: open() it with their data.
##
## The fields match the Resident table (backend/mii/models.py); see
## docs/mii-customization.md for how `gifts` and `keys` reach ACP.
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
const SURPRISE_PHRASES := [
	"Let me poke at it!",
	"Tiny steps!",
	"Ship it, but safely!",
	"Hmm, interesting…",
	"One more test!",
	"Let's map it out!",
	"Tidy code, happy island."
]

## Same buckets as level() in backend/mii/personality.py, as short phrases for
## the "How Mo will act" box. [low, medium, high]
const ACT_PHRASES := {
	"caution": ["moves fast and takes reasonable risks", "balances speed with care", "asks before anything risky"],
	"chattiness": ["keeps notes short", "explains briefly", "explains in friendly detail"],
	"creativity": ["sticks to well-known solutions", "is open to new ideas", "suggests creative alternatives"],
	"bluntness": ["is gentle with bad news", "is direct but polite", "is blunt about what's wrong"],
}

## The Look page's tabs: [appearance key, label, the style shown as the tab's icon]. See MiiParts.
const PART_TABS := [
	["hair", "Hair", 0], ["eyes", "Eyes", 0], ["brows", "Brows", 1], ["nose", "Nose", 4], ["mouth", "Mouth", 2]
]
const ABOUT_ICON := preload("res://assets/icons/user.svg")
const LOOK_ICON := preload("res://assets/icons/smile.svg")
const INK := Color("292E50")  # the theme's dark text colour
const PART_COLUMNS := 14

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
var _part_tiles := {}  # kind -> Array of tile buttons, index = style (hair: index 0 is "none")
var _part_grids := {}  # kind -> GridContainer
var _part_tabs := {}  # kind -> tab button

## Model ids for the model picker, per brain (GET /brains/{brain}/models), loaded
## the first time that brain is picked. ACP brains are asked by starting their CLI,
## which takes a few seconds.
var _models := {}  # brain -> Array of [id, name]
var _models_asking := {}  # brains whose list is on its way
var _model_picker := OptionButton.new()

@onready var preview: MiiPreview = %Preview


func _ready() -> void:
	_build_swatches()
	_build_look()
	_build_jobs()
	_build_brains()
	_build_sliders()
	_build_gifts()
	_build_keys()
	_build_works_at()
	_build_move_away()

	# Godot concept: text_changed fires on every keystroke, so the preview
	# updates while you type.
	%NameEdit.text_changed.connect(
		func(text: String):
			_data["name"] = text
			_refresh()
	)
	%CatchphraseEdit.text_changed.connect(
		func(text: String):
			_data["catchphrase"] = text
			_refresh()
	)
	%ModelEdit.text_changed.connect(func(text: String): _data["model"] = text if text.strip_edges() else null)
	# The page tabs get white Lucide icons, tinted dark like the text.
	for pair in [[%AboutTab, ABOUT_ICON], [%LookTab, LOOK_ICON]]:
		_set_tab_icon(pair[0], pair[1], INK, 18)
	%AboutTab.pressed.connect(_show_page.bind(false))
	%LookTab.pressed.connect(_show_page.bind(true))
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
	# A resident with no saved look keeps the one the island picked for them.
	_data["appearance"] = MiiParts.for_resident(resident) if _editing else MiiParts.random()
	_data.erase("buildings")  # only sent when "Works at" changes
	_move_away.visible = live and _editing and not Net.find_resident(str(resident.get("id", ""))).is_empty()
	_fill_works_at()
	%Title.text = "Edit %s" % resident.get("name", "") if _editing else "Create a resident"
	%MovingLabel.text = ("Lives on %s" if _editing else "Moving to %s") % island_name
	%MoveInButton.text = "Save" if _editing else "Move in"
	%MoveInButton.disabled = false
	_show_page(false)
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
	for kind in _part_tiles:
		# Hair's tile 0 is "None" (style -1), so its tiles are shifted by one.
		var index := int(_data["appearance"][kind]) + (1 if kind == "hair" else 0)
		var tiles: Array = _part_tiles[kind]
		index = clampi(index, 0, tiles.size() - 1)
		# set_pressed_no_signal() doesn't release the rest of a ButtonGroup, so set every tile.
		for i in tiles.size():
			tiles[i].set_pressed_no_signal(i == index)


## Update everything that depends on _data: preview, labels, colours.
func _refresh() -> void:
	var color := Color(str(_data["color"]))
	var shown_name: String = _data["name"] if _data["name"] else "New friend"
	preview.set_color(color)
	preview.set_appearance(_data["appearance"])
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
		# Each swatch knows which field it sets: "color", or "skin" / "hair_color" in appearance.
		var field: String = swatch.get_meta("field")
		var current = _data[field] if field == "color" else _data["appearance"][field]
		var style: StyleBoxFlat = swatch.get_theme_stylebox("normal")
		var selected := Color(swatch.get_meta("color")) == Color(str(current))
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


## "Surprise me": on the Look page, a new look only. On the About page, a new
## everything, except that someone you're editing keeps their name.
func _surprise() -> void:
	_data["appearance"] = MiiParts.random()
	if %LookPage.visible:
		_load_widgets()
		_refresh()
		return
	if not _editing:
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
		_add_swatch(%Swatches, "color", hex)


## A round colour button that sets `field`: "color", or "skin" / "hair_color" in appearance.
func _add_swatch(row: Container, field: String, hex: String) -> void:
	var swatch := Button.new()
	swatch.custom_minimum_size = Vector2(26, 26)
	swatch.focus_mode = Control.FOCUS_NONE
	swatch.set_meta("color", hex)
	swatch.set_meta("field", field)
	swatch.tooltip_text = hex
	var style: StyleBoxFlat = get_theme_stylebox("panel", "AvatarCircle").duplicate()
	style.bg_color = Color(hex)
	style.set_border_width_all(3)
	style.set_corner_radius_all(13)
	for state in ["normal", "hover", "pressed", "hover_pressed"]:
		swatch.add_theme_stylebox_override(state, style)
	swatch.pressed.connect(
		func():
			if field == "color":
				_data["color"] = hex
			else:
				_data["appearance"][field] = hex
			_refresh()
	)
	row.add_child(swatch)
	_swatches.append(swatch)


# --- The Look page ---------------------------------------------------------------


## Show the About page (false) or the Look page (true). On the Look page the
## preview camera moves in close to the face.
func _show_page(look: bool) -> void:
	%AboutPage.visible = not look
	%LookPage.visible = look
	%AboutTab.theme_type_variation = &"TabInactive" if look else &"TabActive"
	%LookTab.theme_type_variation = &"TabActive" if look else &"TabInactive"
	preview.focus_face(look)


## Give a tab button an icon left of its text. `tint` multiplies the icon in every
## state (theme styles would otherwise recolour it); `width` is its size in px.
func _set_tab_icon(button: Button, icon: Texture2D, tint: Color, width: int) -> void:
	button.icon = icon
	button.expand_icon = true
	button.add_theme_constant_override("icon_max_width", width)
	button.add_theme_constant_override("h_separation", 6)
	for state in [
		"icon_normal_color", "icon_hover_color", "icon_pressed_color", "icon_hover_pressed_color", "icon_focus_color"
	]:
		button.add_theme_color_override(state, tint)


## Show one part's tiles (hair, eyes, ...).
func _show_part(kind: String) -> void:
	for k in _part_grids:
		_part_grids[k].visible = k == kind
		_part_tabs[k].theme_type_variation = &"TabActive" if k == kind else &"TabInactive"
	%PartScroll.scroll_vertical = 0


func _build_look() -> void:
	for hex in MiiParts.SKIN_TONES:
		_add_swatch(%SkinSwatches, "skin", hex)
	for hex in MiiParts.HAIR_COLORS:
		_add_swatch(%HairSwatches, "hair_color", hex)

	for tab in PART_TABS:
		var kind: String = tab[0]
		var button := Button.new()
		button.text = tab[1]
		button.custom_minimum_size = Vector2(0, 62)
		# The icon is a real part from the atlas (an eye on the Eyes tab, ...), untinted,
		# above the label.
		_set_tab_icon(button, MiiParts.icon(kind, tab[2]), Color.WHITE, 40)
		button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
		button.vertical_icon_alignment = VERTICAL_ALIGNMENT_TOP
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.focus_mode = Control.FOCUS_NONE
		button.pressed.connect(_show_part.bind(kind))
		%PartTabRow.add_child(button)
		_part_tabs[kind] = button

		var grid := GridContainer.new()
		grid.columns = PART_COLUMNS
		grid.add_theme_constant_override("h_separation", 6)
		grid.add_theme_constant_override("v_separation", 6)
		%PartGrids.add_child(grid)
		_part_grids[kind] = grid
		# One toggle tile per style, in a ButtonGroup so only one is picked.
		var group := ButtonGroup.new()
		var tiles := []
		var first := MiiParts.NO_HAIR if kind == "hair" else 0
		for style in range(first, MiiParts.count(kind)):
			var tile := Button.new()
			tile.toggle_mode = true
			tile.button_group = group
			tile.theme_type_variation = &"GiftTile"
			tile.custom_minimum_size = Vector2(62, 62)
			tile.focus_mode = Control.FOCUS_NONE
			if style == MiiParts.NO_HAIR:
				tile.text = "None"
				tile.tooltip_text = "No hair"
			else:
				# expand_icon lets the 128 px thumbnail shrink to fit the tile. GiftTile
				# caps and tints icons (its Lucide icons are white), so undo both here.
				tile.icon = MiiParts.icon(kind, style)
				tile.expand_icon = true
				tile.add_theme_constant_override("icon_max_width", 56)
				for state in [
					"icon_normal_color",
					"icon_hover_color",
					"icon_pressed_color",
					"icon_hover_pressed_color",
					"icon_focus_color"
				]:
					tile.add_theme_color_override(state, Color.WHITE)
				tile.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
				var hat := kind == "hair" and MiiParts.is_hat(style)
				tile.tooltip_text = "Hat" if hat else "%s %d" % [tab[1], style + 1]
			tile.toggled.connect(
				func(on: bool):
					if on:
						_data["appearance"][kind] = style
						_refresh()
			)
			grid.add_child(tile)
			tiles.append(tile)
		_part_tiles[kind] = tiles
	_show_part("hair")


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
		button.toggled.connect(
			func(on: bool):
				if on:
					_data["job"] = job
					_refresh()
		)
		%Jobs.add_child(button)
		_job_buttons[job] = button


func _build_brains() -> void:
	%BrainPicker.add_theme_icon_override("arrow", ARROW)
	for id in _brain_ids:
		%BrainPicker.add_item(ResidentData.BRAINS[id])
	%BrainPicker.item_selected.connect(
		func(index: int):
			_data["brain"] = _brain_ids[index]
			_show_model_picker()
	)

	# The brain's models, next to the model field (every brain but mock).
	_model_picker.theme_type_variation = &"OutlineButton"
	_model_picker.add_theme_icon_override("arrow", ARROW)
	_model_picker.custom_minimum_size = Vector2(0, 34)
	_model_picker.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_model_picker.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_model_picker.focus_mode = Control.FOCUS_NONE
	_model_picker.tooltip_text = "Pick one of the models this brain offers (or type an id)"
	_model_picker.visible = false
	_model_picker.item_selected.connect(
		func(index: int):
			var models: Array = _models.get(_data["brain"], [])
			if index > 0 and index <= models.size():
				%ModelEdit.text = models[index - 1][0]
				_data["model"] = models[index - 1][0]
	)
	%ModelEdit.add_sibling(_model_picker)


## Show the model picker for the picked brain (none for mock), loading that brain's
## models from GET /brains/{brain}/models the first time.
func _show_model_picker() -> void:
	var brain := str(_data["brain"])
	_model_picker.visible = brain != "mock"
	if not _model_picker.visible:
		return
	if _models.has(brain):
		_fill_model_picker(brain)
		return
	_model_picker.clear()
	_model_picker.add_item("Asking %s for its models…" % ResidentData.BRAINS.get(brain, brain))
	if _models_asking.has(brain):
		return
	_models_asking[brain] = true
	Net.request_json(
		"/brains/%s/models" % brain.uri_encode(),
		func(ok: bool, data: Dictionary):
			_models_asking.erase(brain)
			if not ok:  # not installed or logged in, no OpenRouter key...: tried again next time
				if _data["brain"] == brain:
					_model_picker.clear()
					_model_picker.add_item("No model list: %s" % data["error"])
					_model_picker.tooltip_text = str(data["error"])
				return
			var models := []
			for model in data.get("models", []):
				models.append([str(model["id"]), str(model.get("name", model["id"]))])
			_models[brain] = models
			if _data["brain"] == brain:  # still the picked brain (the answer can take a while)
				_fill_model_picker(brain)
	)


func _fill_model_picker(brain: String) -> void:
	var models: Array = _models[brain]
	_model_picker.clear()
	_model_picker.tooltip_text = "Pick one of the models this brain offers (or type an id)"
	_model_picker.add_item("Choose a model…" if models else "This brain offers no model choice")
	var picked := 0
	for i in models.size():
		var id: String = models[i][0]
		var name: String = models[i][1]
		_model_picker.add_item(name if name == id else "%s  (%s)" % [name, id])
		if id == %ModelEdit.text:
			picked = i + 1
	_model_picker.select(picked)


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
		slider.value_changed.connect(
			func(value: float):
				_data[t[0]] = value
				_refresh()
		)
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
		tile.toggled.connect(
			func(on: bool):
				if on and g[0] not in _data["gifts"]:
					_data["gifts"].append(g[0])
				elif not on:
					_data["gifts"].erase(g[0])
				_show_gift(g[0])
		)
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
	Net.request_json(
		"/brains/detail",
		func(ok: bool, brains: Dictionary):
			if not ok:
				return  # backend offline: leave every brain selectable
			var tips := []
			for i in _brain_ids.size():
				var info: Dictionary = brains.get(_brain_ids[i], {})
				var ready: bool = info.get("ok", false)
				%BrainPicker.set_item_disabled(i, not ready)
				%BrainPicker.set_item_text(i, ResidentData.BRAINS[_brain_ids[i]] + ("" if ready else " (not set up)"))
				tips.append("%s: %s" % [ResidentData.BRAINS[_brain_ids[i]], info.get("hint", "unknown")])
			%BrainPicker.tooltip_text = "\n".join(tips)
	)


## Ask the backend which gifts can run here (GET /gifts) and grey out the rest,
## with why in the tooltip ("install `npx` first").
func _check_gifts() -> void:
	Net.request_json(
		"/gifts",
		func(ok: bool, gifts: Dictionary):
			if not ok:
				return  # backend offline: leave every gift selectable
			_gift_info = gifts
			for gift in _gift_buttons:
				_show_gift(gift)
	)


## A gift that can't run here is greyed out, unless the resident already has
## it: then it stays clickable, so you can take it back.
func _show_gift(gift: String) -> void:
	var info: Dictionary = _gift_info.get(gift, {})
	if info.is_empty():
		return
	var tile: Button = _gift_buttons[gift]
	var ready: bool = info.get("ok", false)
	tile.disabled = not ready and not tile.button_pressed
	tile.tooltip_text = (
		"%s: %s" % [info.get("label", gift), info.get("hint", "")]
		if ready
		else "Can't hand this over yet: %s" % info.get("hint", "not set up")
	)


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
	_works_at_now = (
		buildings
		. filter(func(b): return id != "" and str(b.get("resident", "")) == id)
		. map(func(b): return str(b["id"]))
	)
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
		chip.tooltip_text = (
			("Works here already. To move them, give the %s to someone else." % b.get("name", bid))
			if mine
			else "Make them the worker at the %s (instead of %s)" % [b.get("name", bid), worker]
		)
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
	_move_away.pressed.connect(
		func():
			var who := str(_data.get("name", "This resident"))
			ConfirmPopup.ask(
				self,
				"%s is moving away?" % who,
				"%s leaves the island for good. Their past work stays in Insights and News." % who,
				"Say goodbye",
				func(): move_away_requested.emit(str(_data.get("id", "")))
			)
	)
	%CancelButton.add_sibling(_move_away)
	%CancelButton.get_parent().move_child(_move_away, %CancelButton.get_index())


func _tint(node: Control, theme_type: String, color: Color) -> void:
	var style: StyleBoxFlat = get_theme_stylebox("panel", theme_type).duplicate()
	style.bg_color = color
	node.add_theme_stylebox_override("panel", style)
