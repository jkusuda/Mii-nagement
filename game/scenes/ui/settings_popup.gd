class_name SettingsPopup
extends Control
## Settings (the Archipelago's Settings button): a cream panel in the middle of a
## dimmed window, laid out like the resident creator, with white cards for
## Island work, Brains, API keys, and the Jukebox (Audio: songs and volumes).
##
## It reads and writes the backend's saved settings and API keys (GET / PUT
## /settings), logs in when the server has Auth0 on (POST /auth/device, /auth/token;
## the Account strip under the title), shows which real brains can run on this
## computer (GET /brains/detail), and OpenRouter's catalogue (GET /models) for the
## default model's dropdown.
##
## API keys go to the OS keychain and are never sent back, only "saved" or not.
## Anything set in the backend's .env wins over what's saved here, so those
## fields are read-only. Developer settings (mock agents, kept worktrees, the
## judge) are left out: `make` / `make dev` and the .env handle those.
##
## Built in code, like IslandDropdown. Esc, Cancel or a click on the dimmed
## backdrop closes it without saving.

const FORGET_ICON := preload("res://assets/icons/x.svg")  # also the close button
const CHECK_ICON := preload("res://assets/icons/check.svg")
const FOLDER_ICON := preload("res://assets/icons/folder-open.svg")
const MINUS_ICON := preload("res://assets/icons/minus.svg")
const PLUS_ICON := preload("res://assets/icons/plus.svg")
const SPROUT_ICON := preload("res://assets/icons/sprout.svg")
const PLAY_ICON := preload("res://assets/icons/play.svg")
const BACK_ICON := preload("res://assets/icons/skip-back.svg")
const NEXT_ICON := preload("res://assets/icons/skip-forward.svg")
const PAUSE_ICON := preload("res://assets/icons/pause.svg")
const USER_ICON := preload("res://assets/icons/user.svg")
const ARROW := preload("res://assets/ui/dropdown_arrow.svg")
const INK := Color("292E50")  # the theme's dark text colour
const MUTED := Color("8A8FA8")
const GOOD := Color("2FA36B")
const MODEL_MATCHES := 40  # rows in the model dropdown
const MAX_PACKAGES := 8  # parallel_packages is 1-8 (backend/mii/prefs.py)
const FOOTER_HINT := "Changes start with each island's next package."

## Text settings shown here: [setting, label, placeholder, help]. Keys must be in
## SETTINGS in backend/mii/prefs.py.
const TEXTS := {
	"islands_root": ["Where new islands go", "Documents/Mii-nagement", "Each new island gets its own git repo here."],
	"test_command": ["Lab test command", "e.g. uv run pytest", "What the Lab runs. Empty: the reviewer decides."],
	"openrouter_model":
	["Default OpenRouter model", "e.g. anthropic/claude-sonnet-4.5", "For OpenRouter residents without their own."],
	"godot_path": ["Godot for the Godot gift", "the Godot 4 app or binary", "Residents with the Godot gift run this."],
}
## [secret, label]. Must match SECRETS in backend/mii/prefs.py.
const SECRETS := [
	["anthropic_api_key", "Anthropic (Claude Code)"],
	["openai_api_key", "OpenAI (Codex)"],
	["gemini_api_key", "Google (Gemini CLI)"],
	["openrouter_api_key", "OpenRouter"],
	["typesafe_api_key", "TypeSafe (Jev: moods, helpers)"],
]

var _texts := {}  # setting -> LineEdit
var _browse := {}  # setting -> its Browse button (islands_root, godot_path)
var _packages := 3
var _packages_label := Label.new()
var _packages_buttons: Array[Button] = []  # [-, +]
var _ask_first := Button.new()  # master key off
var _never_ask := Button.new()  # master key on
var _locked: Array = []  # settings the .env sets
var _keys := {}  # secret -> LineEdit
var _forget := {}  # secret -> true: delete it on Save
var _loaded := {}  # GET /settings `settings`, to send only what changed
var _brains := VBoxContainer.new()
var _models: Array = []  # GET /models: [{id, name}], OpenRouter's catalogue
var _models_error := ""  # why the catalogue didn't load (no key, offline...)
var _message := Label.new()
var _save := Button.new()
var _dialog := FileDialog.new()
var _disc := Control.new()  # the jukebox's record
var _song: Label
var _song_note: Label
var _play: Button
var _account := PanelContainer.new()  # shown only when the server has login on
var _account_text := Label.new()
var _login := Button.new()
var _login_page := Button.new()  # opens Auth0's page while a login waits
var _login_code := ""  # the device_code being polled, "" when not logging in
var _dialog_for := ""  # which setting the open file dialog fills


func _init() -> void:
	visible = false
	# Godot concept: anchors pin a Control to its parent's edges; PRESET_FULL_RECT
	# stretches this overlay over the whole window.
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_STOP  # the island underneath gets no clicks

	var backdrop := ColorRect.new()
	backdrop.color = Color(INK, 0.45)
	backdrop.set_anchors_preset(Control.PRESET_FULL_RECT)
	backdrop.gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				hide()
	)
	add_child(backdrop)

	# Godot concept: a CenterContainer keeps its child at its own size, in the middle.
	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(center)
	var panel := PanelContainer.new()
	panel.theme_type_variation = &"ScreenPanel"
	panel.custom_minimum_size = Vector2(1040, 0)
	center.add_child(panel)
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 14)
	panel.add_child(rows)

	rows.add_child(_header())
	rows.add_child(_account_strip())
	var columns := HBoxContainer.new()
	columns.add_theme_constant_override("separation", 14)
	rows.add_child(columns)
	columns.add_child(_column([_island_card(), _jukebox_card()]))
	columns.add_child(_column([_brains_card()]))
	columns.add_child(_column([_keys_card()]))
	rows.add_child(_footer())

	# Godot concept: FileDialog is Godot's file picker; use_native_dialog asks the
	# operating system for its own (Finder on macOS) where there is one.
	_dialog.access = FileDialog.ACCESS_FILESYSTEM
	_dialog.use_native_dialog = true
	_dialog.dir_selected.connect(_on_picked)
	_dialog.file_selected.connect(_on_picked)
	add_child(_dialog)


## Open in the middle of the window and load everything fresh.
func open() -> void:
	_say("Loading…")
	_save.disabled = true
	_forget.clear()
	show()
	Audio.sfx("popup_open")
	_load()


func _unhandled_key_input(event: InputEvent) -> void:
	if visible and event.is_action_pressed("ui_cancel"):
		hide()
		get_viewport().set_input_as_handled()


# --- Layout --------------------------------------------------------------------


func _header() -> Control:
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 12)
	var titles := VBoxContainer.new()
	titles.add_theme_constant_override("separation", 0)
	titles.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var title := Label.new()
	title.text = "Settings"
	title.theme_type_variation = &"ScreenTitle"
	titles.add_child(title)
	var subtitle := Label.new()
	subtitle.text = "How your islands work, and the keys to your agents."
	subtitle.theme_type_variation = &"LabelMuted"
	titles.add_child(subtitle)
	header.add_child(titles)

	var close := _icon_button(FORGET_ICON, "Close without saving", &"FlatIconButton")
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	close.pressed.connect(hide)
	header.add_child(close)
	return header


func _footer() -> Control:
	var footer := HBoxContainer.new()
	footer.add_theme_constant_override("separation", 10)
	footer.add_child(_icon(SPROUT_ICON, GOOD, 18))
	_message.theme_type_variation = &"LabelMuted"
	_message.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	footer.add_child(_message)
	var cancel := Button.new()
	cancel.text = "Cancel"
	cancel.theme_type_variation = &"OutlineButton"
	cancel.custom_minimum_size = Vector2(110, 40)
	cancel.pressed.connect(hide)
	footer.add_child(cancel)
	_save.text = "Save"
	_save.theme_type_variation = &"AnswerButton"
	_save.custom_minimum_size = Vector2(130, 40)
	_save.set_script(AnswerButton)  # the wavy orange pill
	_save.pressed.connect(_on_save)
	footer.add_child(_save)
	return footer


## A column of cards, sharing the panel's width equally with the others.
func _column(cards: Array) -> Control:
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_theme_constant_override("separation", 14)
	for card in cards:
		column.add_child(card)
	return column


## A white card with a title and a muted hint beside it, like the creator's.
func _card(title: String, hint: String, grow := true) -> Array:
	var card := PanelContainer.new()
	card.theme_type_variation = &"SectionPanel"
	if grow:
		card.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	card.add_child(box)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 8)
	var label := Label.new()
	label.text = title
	label.theme_type_variation = &"SectionTitle"
	head.add_child(label)
	if hint:
		var muted := Label.new()
		muted.text = hint
		muted.theme_type_variation = &"LabelMuted"
		muted.size_flags_vertical = Control.SIZE_SHRINK_END
		head.add_child(muted)
	box.add_child(head)
	return [card, box]


func _island_card() -> Control:
	var parts := _card("Island work", "for every island")
	var box: VBoxContainer = parts[1]
	box.add_child(_text_field("islands_root", true))
	box.add_child(_text_field("test_command"))

	# Packages at once: a − 3 + stepper.
	var stepper := HBoxContainer.new()
	stepper.add_theme_constant_override("separation", 6)
	var minus := _icon_button(MINUS_ICON, "Fewer at once")
	var plus := _icon_button(PLUS_ICON, "More at once")
	minus.pressed.connect(_step_packages.bind(-1))
	plus.pressed.connect(_step_packages.bind(1))
	_packages_buttons = [minus, plus]
	var count := PanelContainer.new()
	count.theme_type_variation = &"SoftPill"
	count.custom_minimum_size = Vector2(52, 0)
	_packages_label.theme_type_variation = &"LabelStrong"
	_packages_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_packages_label.add_theme_font_size_override("font_size", 17)
	count.add_child(_packages_label)
	stepper.add_child(minus)
	stepper.add_child(count)
	stepper.add_child(plus)
	box.add_child(_field("Packages at once", "Issues one island works on together.", stepper))

	# Master key: two choice buttons that act like radio buttons.
	# Godot concept: a ButtonGroup lets only one of its toggle buttons be pressed.
	var group := ButtonGroup.new()
	var choices := HBoxContainer.new()
	choices.add_theme_constant_override("separation", 6)
	for pair in [[_ask_first, "Ask me first"], [_never_ask, "Never ask"]]:
		var button: Button = pair[0]
		button.text = pair[1]
		button.toggle_mode = true
		button.button_group = group
		button.theme_type_variation = &"ChoiceButton"
		button.custom_minimum_size.y = 32
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.focus_mode = Control.FOCUS_NONE
		choices.add_child(button)
	_never_ask.tooltip_text = "The master key: permissions granted, reviews approved, questions left to them."
	box.add_child(_field("When residents need you", "", choices))
	return parts[0]


## The jukebox (Audio): the song playing, back / play-pause / next, and the volumes.
## Back and next step through "follow the island" and every song.
func _jukebox_card() -> Control:
	var parts := _card("Jukebox", "", false)
	var box: VBoxContainer = parts[1]
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	row.add_child(_disc)
	_disc.custom_minimum_size = Vector2(48, 48)
	# Godot concept: the `draw` signal fires when the control is drawn; draw_* calls
	# inside it paint straight onto the control. _process spins it while music plays.
	_disc.draw.connect(
		func():
			var c := _disc.size / 2.0
			_disc.draw_circle(c, 24, INK)
			_disc.draw_arc(c, 17, 0, TAU, 32, Color(1, 1, 1, 0.15), 1.5, true)
			_disc.draw_arc(c, 12, 0, TAU, 32, Color(1, 1, 1, 0.15), 1.5, true)
			_disc.draw_arc(c, 20, -0.6, 0.3, 12, Color(1, 1, 1, 0.45), 2.0, true)  # a shine, to see it spin
			_disc.draw_circle(c, 8, Color("FFEC01"))
			_disc.draw_circle(c, 2, INK)
	)
	var track := VBoxContainer.new()
	track.add_theme_constant_override("separation", 0)
	track.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	track.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_song = UI.label("", 16, true)
	_song_note = UI.label("", 13, false)
	track.add_child(_song)
	track.add_child(_song_note)
	row.add_child(track)
	var back := _icon_button(BACK_ICON, "Previous song", &"FlatIconButton")
	back.pressed.connect(Audio.step_song.bind(-1))
	_play = _icon_button(PAUSE_ICON, "Pause the music", &"SelectorButton")
	_play.pressed.connect(Audio.toggle_paused)
	var next := _icon_button(NEXT_ICON, "Next song", &"FlatIconButton")
	next.pressed.connect(Audio.step_song.bind(1))
	for button in [back, _play, next]:
		button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(button)
	box.add_child(row)
	for volume in [
		["Music", Audio.music_volume, Audio.set_music_volume], ["Sounds", Audio.sound_volume, Audio.set_sound_volume]
	]:
		var line := HBoxContainer.new()
		line.add_theme_constant_override("separation", 10)
		var title := UI.label(volume[0], 14, true)
		title.custom_minimum_size.x = 60
		line.add_child(title)
		var slider := HSlider.new()
		slider.max_value = 1.0
		slider.step = 0.01
		slider.value = volume[1]
		slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		slider.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		slider.value_changed.connect(volume[2])
		line.add_child(slider)
		box.add_child(line)
	Audio.song_changed.connect(_show_song)
	_show_song()
	return parts[0]


func _show_song() -> void:
	_song.text = Audio.song_name()
	if Audio.paused:
		_song_note.text = "Paused"
	else:
		_song_note.text = "Picked on the jukebox" if Audio.pinned else "Changes with where you are"
	_play.icon = PLAY_ICON if Audio.paused else PAUSE_ICON
	_play.tooltip_text = "Play the music" if Audio.paused else "Pause the music"


func _process(delta: float) -> void:
	if visible and not Audio.paused:
		_disc.pivot_offset = _disc.size / 2.0
		_disc.rotation += delta * 1.6


func _brains_card() -> Control:
	var parts := _card("Brains", "on this computer")
	var box: VBoxContainer = parts[1]
	_brains.add_theme_constant_override("separation", 6)
	box.add_child(_brains)
	var gap := Control.new()
	gap.custom_minimum_size.y = 4
	box.add_child(gap)
	box.add_child(_text_field("openrouter_model"))
	box.add_child(_text_field("godot_path", true))
	return parts[0]


func _keys_card() -> Control:
	var parts := _card("API keys", "only if needed")
	var box: VBoxContainer = parts[1]
	var note := PanelContainer.new()
	note.theme_type_variation = &"NoteBox"
	var text := UI.label(
		"For agents not logged in with their own CLI. Kept in your keychain, never shown again.", 13, false
	)
	text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text.custom_minimum_size.x = 1
	note.add_child(text)
	box.add_child(note)
	for k in SECRETS:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 6)
		var edit := LineEdit.new()
		edit.secret = true
		edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		edit.custom_minimum_size.y = 34
		edit.text_changed.connect(func(_text): _forget.erase(k[0]))
		var forget := _icon_button(FORGET_ICON, "Forget this key (on Save)", &"FlatIconButton")
		forget.pressed.connect(
			func():
				_forget[k[0]] = true
				edit.clear()
				edit.placeholder_text = "forgotten on Save"
		)
		row.add_child(edit)
		row.add_child(forget)
		_keys[k[0]] = edit
		box.add_child(_field(k[1], "", row, 2))
	return parts[0]


## A text setting from TEXTS: its label, the field (plus a folder button for
## paths, or the model dropdown), and a line of help.
func _text_field(key: String, browse := false) -> Control:
	var info: Array = TEXTS[key]
	var edit := LineEdit.new()
	edit.placeholder_text = info[1]
	edit.custom_minimum_size.y = 36
	edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_texts[key] = edit
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	row.add_child(edit)
	if browse and not OS.has_feature("web"):  # in a browser, "this computer" is the server: nothing to browse
		var button := _icon_button(FOLDER_ICON, "Browse…")
		button.pressed.connect(_browse_for.bind(key))
		_browse[key] = button
		row.add_child(button)
	elif key == "openrouter_model":
		row.add_child(_model_menu(edit))
	return _field(info[0], info[2], row)


## A label, the input, and an optional line of help under it.
func _field(title: String, help: String, input: Control, gap := 4) -> Control:
	var field := VBoxContainer.new()
	field.add_theme_constant_override("separation", gap)
	var label := Label.new()
	label.text = title
	label.theme_type_variation = &"FieldLabel"
	field.add_child(label)
	field.add_child(input)
	if help:
		var muted := UI.label(help, 13, false)
		muted.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		muted.custom_minimum_size.x = 1  # wrap to the column, don't widen it
		field.add_child(muted)
	return field


## A square button with an icon tinted like the text.
func _icon_button(icon: Texture2D, tip: String, variation := &"OutlineButton") -> Button:
	var button := Button.new()
	button.icon = icon
	button.tooltip_text = tip
	button.theme_type_variation = variation
	button.custom_minimum_size = Vector2(36, 36)
	button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button.focus_mode = Control.FOCUS_NONE
	for state in ["icon_normal_color", "icon_hover_color", "icon_pressed_color", "icon_hover_pressed_color"]:
		button.add_theme_color_override(state, INK)
	button.add_theme_color_override("icon_disabled_color", Color(MUTED, 0.6))
	button.add_theme_constant_override("icon_max_width", 18)
	return button


func _icon(texture: Texture2D, tint: Color, px: int) -> TextureRect:
	var icon := TextureRect.new()
	icon.texture = texture
	icon.modulate = tint
	icon.custom_minimum_size = Vector2(px, px)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return icon


# --- Loading -------------------------------------------------------------------


func _load() -> void:
	Net.request_json(
		"/auth/config",
		func(ok: bool, data: Dictionary):
			_account.visible = ok and data.get("enabled", false)
			_show_account()
	)
	Net.request_json(
		"/settings",
		func(ok: bool, data: Dictionary):
			if not ok:
				_say("Couldn't load settings: %s" % data["error"], true)
				return
			_show_settings(data)
			_say(FOOTER_HINT)
	)
	_load_brains()
	Net.request_json(
		"/models",
		func(ok: bool, data: Dictionary):
			_models = data.get("models", []) if ok else []
			_models_error = "" if ok else str(data.get("error", "unavailable"))
	)


## The ▾ button beside the model field: lists the catalogue's models matching
## what's typed. Godot has no combo box, so it's the LineEdit plus a MenuButton (a
## button that opens its own PopupMenu).
func _model_menu(edit: LineEdit) -> MenuButton:
	var pick := MenuButton.new()
	pick.icon = ARROW
	pick.theme_type_variation = &"OutlineButton"
	pick.tooltip_text = "Pick from OpenRouter's models (the text filters the list)"
	pick.flat = false
	pick.custom_minimum_size = Vector2(36, 36)
	pick.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	var menu := pick.get_popup()
	# Godot concept: `about_to_popup` fires just before the menu opens, so it's
	# filled with what's typed at that moment.
	menu.about_to_popup.connect(func(): _fill_model_menu(menu, edit.text.strip_edges()))
	menu.index_pressed.connect(
		func(index: int):
			var id = menu.get_item_metadata(index)
			if id != null:
				edit.text = str(id)
	)
	return pick


## The catalogue's models whose id or name contains `typed` (all of them when
## nothing, or an exact id, is typed), at most MODEL_MATCHES rows.
func _fill_model_menu(menu: PopupMenu, typed: String) -> void:
	menu.clear()
	if _models.is_empty():
		var why := _models_error if _models_error else "loading…"
		menu.add_item("No model list (%s). Type an id instead." % why)
		menu.set_item_disabled(0, true)
		return
	var needle := typed.to_lower()
	if _models.any(func(m): return str(m["id"]) == typed):
		needle = ""
	var shown := 0
	for model in _models:
		var id := str(model["id"])
		var name := str(model.get("name", id))
		if needle and needle not in id.to_lower() and needle not in name.to_lower():
			continue
		if shown == MODEL_MATCHES:
			menu.add_item("… type more to narrow it down")
			menu.set_item_disabled(menu.item_count - 1, true)
			break
		menu.add_item("%s  (%s)" % [name, id] if name != id else id)
		menu.set_item_metadata(menu.item_count - 1, id)
		if id == typed:
			menu.set_item_as_checkable(menu.item_count - 1, true)
			menu.set_item_checked(menu.item_count - 1, true)
		shown += 1
	if shown == 0:
		menu.add_item("No model matches “%s”: it'll be saved as typed." % typed)
		menu.set_item_disabled(0, true)


## One row per brain: a tick or a cross, its name, and (when it can't run) how to fix that.
func _load_brains() -> void:
	Net.request_json(
		"/brains/detail",
		func(ok: bool, brains: Dictionary):
			for child in _brains.get_children():
				child.queue_free()
			if not ok:
				_brains.add_child(UI.label("Couldn't ask the backend: %s" % brains["error"], 13, false))
				return
			for id in ResidentData.BRAINS:
				if id == "mock":
					continue  # the fake brain is for development, not a choice here
				var info: Dictionary = brains.get(id, {})
				var can_run := bool(info.get("ok"))
				var row := HBoxContainer.new()
				row.add_theme_constant_override("separation", 8)
				row.tooltip_text = str(info.get("hint", ""))
				row.mouse_filter = Control.MOUSE_FILTER_PASS
				row.add_child(_icon(CHECK_ICON if can_run else FORGET_ICON, GOOD if can_run else MUTED, 16))
				var title := UI.label(ResidentData.BRAINS[id], 15, can_run)
				title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
				row.add_child(title)
				row.add_child(UI.label("ready" if can_run else "not set up", 13, false))
				_brains.add_child(row)
	)


## Fill the fields from a GET / PUT /settings reply.
func _show_settings(data: Dictionary) -> void:
	_loaded = data.get("settings", {})
	_locked = data.get("from_env", [])
	for key in _texts:
		var edit: LineEdit = _texts[key]
		var value = _loaded.get(key)
		edit.text = "" if value == null else str(value)
		edit.editable = key not in _locked
		edit.tooltip_text = "Set in the backend's .env, which wins over Settings." if key in _locked else ""
		if _browse.has(key):
			_browse[key].disabled = key in _locked
	var packages = _loaded.get("parallel_packages")
	_packages = clampi(int(packages if packages != null else 3), 1, MAX_PACKAGES)
	_show_packages()
	_never_ask.set_pressed_no_signal(bool(_loaded.get("master_key")))
	_ask_first.set_pressed_no_signal(not _never_ask.button_pressed)
	_ask_first.disabled = "master_key" in _locked
	_never_ask.disabled = "master_key" in _locked
	var saved: Dictionary = data.get("secrets", {})
	for key in _keys:
		var edit: LineEdit = _keys[key]
		edit.clear()
		edit.editable = key not in _locked
		edit.placeholder_text = (
			"set in .env" if key in _locked else ("saved ✓  type to replace" if saved.get(key) else "not set")
		)
	_save.disabled = false


func _step_packages(by: int) -> void:
	_packages = clampi(_packages + by, 1, MAX_PACKAGES)
	_show_packages()


func _show_packages() -> void:
	_packages_label.text = str(_packages)
	var locked := "parallel_packages" in _locked
	_packages_buttons[0].disabled = locked or _packages <= 1
	_packages_buttons[1].disabled = locked or _packages >= MAX_PACKAGES


# --- Browse --------------------------------------------------------------------


## Pick a folder (where new islands go) or the Godot app with the system's file picker.
func _browse_for(key: String) -> void:
	_dialog_for = key
	var folder := key == "islands_root"
	_dialog.file_mode = FileDialog.FILE_MODE_OPEN_DIR if folder else FileDialog.FILE_MODE_OPEN_FILE
	_dialog.title = "Where should new islands go?" if folder else "Find Godot 4"
	var current: String = _texts[key].text.strip_edges()
	if current and DirAccess.dir_exists_absolute(current):
		_dialog.current_dir = current
	elif current:
		_dialog.current_dir = current.get_base_dir()
	_dialog.popup_centered_ratio(0.6)


func _on_picked(path: String) -> void:
	# On macOS the file picker hands back an app bundle (Godot.app); the program is inside it.
	if _dialog_for == "godot_path" and path.ends_with(".app"):
		path = path.path_join("Contents/MacOS").path_join(path.get_file().get_basename())
	_texts[_dialog_for].text = path


# --- Saving --------------------------------------------------------------------


func _on_save() -> void:
	var settings := {}
	for key in _texts:
		var before = _loaded.get(key)
		var value: String = _texts[key].text.strip_edges()
		if key not in _locked and value != ("" if before == null else str(before)):
			settings[key] = value
	if str(_packages) != str(_loaded.get("parallel_packages")) and "parallel_packages" not in _locked:
		settings["parallel_packages"] = _packages
	if _never_ask.button_pressed != bool(_loaded.get("master_key")) and "master_key" not in _locked:
		settings["master_key"] = _never_ask.button_pressed
	var secrets := {}
	for key in _keys:
		if _forget.has(key):
			secrets[key] = null
		elif _keys[key].text.strip_edges() != "":
			secrets[key] = _keys[key].text.strip_edges()
	if settings.is_empty() and secrets.is_empty():
		_say("Nothing changed.")
		return
	_save.disabled = true
	_say("Saving…")
	Net.request_json(
		"/settings",
		func(ok: bool, data: Dictionary):
			_save.disabled = false
			if not ok:
				_say("Couldn't save: %s" % data["error"], true)
				return
			_forget.clear()
			_show_settings(data)
			_say("Saved. " + FOOTER_HINT)
			_load_brains(),
		HTTPClient.METHOD_PUT,
		{"settings": settings, "secrets": secrets}
	)


# --- Account (login on the server) ---------------------------------------------------


## A strip under the title: who is logged in (or why to log in), and the buttons.
func _account_strip() -> Control:
	_account.theme_type_variation = &"NoteBox"
	_account.visible = false
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	row.add_child(_icon(USER_ICON, INK, 18))
	_account_text.theme_type_variation = &"LabelStrong"
	_account_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_account_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_account_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_account_text)
	_login.theme_type_variation = &"OutlineButton"
	_login.custom_minimum_size = Vector2(100, 34)
	_login.pressed.connect(_on_login_pressed)
	_login_page.text = "Open the login page"
	_login_page.theme_type_variation = &"SelectorButton"
	_login_page.custom_minimum_size.y = 34
	_login_page.visible = false
	row.add_child(_login_page)
	row.add_child(_login)
	_account.add_child(row)
	return _account


## Who is logged in, or why to log in.
func _show_account() -> void:
	if _login_code:
		return  # a login is waiting; its text stays
	_login_page.visible = false
	_login.disabled = false
	if Net.token:
		_account_text.text = "Logged in as %s." % (Net.user_name if Net.user_name else "you")
		_login.text = "Log out"
	else:
		_account_text.text = "Log in to found islands and to see your private ones."
		_login.text = "Log in"


## Log out, or start a device login: Auth0 shows a page where the player types a code,
## and we ask the backend every few seconds whether they did.
func _on_login_pressed() -> void:
	if Net.token:
		Net.log_out()
		_show_account()
		return
	_login.disabled = true
	Net.request_json(
		"/auth/device",
		func(ok: bool, data: Dictionary):
			if not ok:
				_login.disabled = false
				_account_text.text = "Couldn't start the login: %s" % data["error"]
				return
			_login_code = str(data["device_code"])
			var page := str(data["verification_uri_complete"])
			_account_text.text = (
				"Check that the login page shows the code %s, then confirm it there." % data["user_code"]
			)
			# A button, not an automatic tab: browsers block pages opened without a click.
			for old in _login_page.pressed.get_connections():
				_login_page.pressed.disconnect(old["callable"])
			_login_page.pressed.connect(func(): OS.shell_open(page))
			_login_page.visible = true
			_poll_login(float(data.get("interval", 5))),
		HTTPClient.METHOD_POST,
		{}
	)


func _poll_login(interval: float) -> void:
	# Godot concept: create_timer() is a one-shot timer; `await` pauses this function
	# until its `timeout` signal fires, without freezing the game.
	await get_tree().create_timer(interval).timeout
	if not _login_code:
		return
	Net.request_json(
		"/auth/token",
		func(ok: bool, data: Dictionary):
			if ok and data.get("pending"):
				_poll_login(interval)
				return
			_login_code = ""
			if ok:
				var who = data.get("name") if data.get("name") else data.get("email", "")
				Net.log_in(str(data["access_token"]), str(who), float(data.get("expires_in", 86400)))
				_show_account()
			else:
				_show_account()
				_account_text.text = "The login didn't go through (%s). Try again." % data["error"],
		HTTPClient.METHOD_POST,
		{"device_code": _login_code}
	)


func _say(text: String, error := false) -> void:
	_message.text = text
	_message.add_theme_color_override("font_color", Color("C0392B") if error else Color("4A5472"))
