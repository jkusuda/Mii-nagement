class_name SettingsPopup
extends PopupPanel
## Settings (the Archipelago's Settings button): the backend's saved settings
## and API keys (GET / PUT /settings), the mode it is running in (GET /health),
## which brains can run on this computer (GET /brains/detail), and OpenRouter's
## catalogue (GET /models) for the default model's dropdown.
##
## API keys go to the OS keychain and are never sent back, only "saved" or not.
## Anything set in the backend's .env wins over what's saved here, so those
## fields are read-only.
##
## Built in code, like IslandDropdown.

const THEME := preload("res://ui/theme.tres")
const FORGET_ICON := preload("res://assets/icons/x.svg")
const ARROW := preload("res://assets/ui/dropdown_arrow.svg")
const MODEL_MATCHES := 40  # rows in the model dropdown

## [setting, label, help, "bool" | "text" | "model"]. Must match SETTINGS in backend/mii/prefs.py.
const SETTINGS := [
	["mock_agents", "Mock agents", "Fake work, no cost. Turn off to let real coding agents work.", "bool"],
	["test_command", "Test command", "What the Lab runs, e.g. uv run pytest. Empty: the reviewer decides.", "text"],
	["islands_root", "Folder for new islands", "Where new island repositories go.", "text"],
	[
		"openrouter_model",
		"Default OpenRouter model",
		"For OpenRouter residents without a model of their own. Pick one, or type any model id.",
		"model"
	],
	["keep_worktrees", "Keep worktrees", "Leave each task's git worktree behind afterwards, for debugging.", "bool"],
	[
		"judge",
		"Judge",
		"auto: moods, help and flaky re-runs ask Jev when a TypeSafe key is saved. off: rules only.",
		"text"
	],
	["godot_path", "Godot executable", "For the Godot gift: the Godot 4 binary residents may run.", "text"],
]
## [secret, label]. Must match SECRETS in backend/mii/prefs.py.
const SECRETS := [
	["openrouter_api_key", "OpenRouter"],
	["typesafe_api_key", "TypeSafe (Jev: moods, helpers, flaky tests)"],
	["anthropic_api_key", "Anthropic (Claude Code)"],
	["openai_api_key", "OpenAI (Codex)"],
	["gemini_api_key", "Google (Gemini CLI)"],
]

var _mode := Label.new()
var _inputs := {}  # setting -> CheckButton or LineEdit
var _keys := {}  # secret -> LineEdit
var _forget := {}  # secret -> true: delete it on Save
var _loaded := {}  # GET /settings `settings`, to send only what changed
var _brains := VBoxContainer.new()
var _models: Array = []  # GET /models: [{id, name}], OpenRouter's catalogue
var _models_error := ""  # why the catalogue didn't load (no key, offline...)
var _message := Label.new()
var _save := Button.new()


func _init() -> void:
	theme = THEME  # a popup is its own window, so it doesn't inherit the game's theme
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(580, 560)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(scroll)
	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 10)
	scroll.add_child(box)

	var heading := Label.new()
	heading.text = "Settings"
	heading.theme_type_variation = &"SectionTitle"
	box.add_child(heading)
	_mode.theme_type_variation = &"LabelMuted"
	box.add_child(_mode)

	box.add_child(_section("Island work"))
	for s in SETTINGS:
		var input  # a CheckButton or a LineEdit
		if s[3] == "bool":
			input = CheckButton.new()
			input.text = s[1]
		else:
			input = LineEdit.new()
		input.tooltip_text = s[2]
		_inputs[s[0]] = input
		var shown: Control = _model_combo(input) if s[3] == "model" else input
		box.add_child(_field("" if s[3] == "bool" else s[1], s[2], shown))

	box.add_child(_section("API keys"))
	box.add_child(
		_help(
			(
				"Only needed when an agent isn't logged in with its own CLI. Kept in your "
				+ "computer's keychain and never shown again."
			)
		)
	)
	for k in SECRETS:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 6)
		var edit := LineEdit.new()
		edit.secret = true
		edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		edit.text_changed.connect(func(_text): _forget.erase(k[0]))
		var forget := Button.new()
		forget.icon = FORGET_ICON
		forget.theme_type_variation = &"FlatIconButton"
		forget.tooltip_text = "Forget this key (on Save)"
		forget.pressed.connect(
			func():
				_forget[k[0]] = true
				edit.clear()
				edit.placeholder_text = "will be forgotten on Save"
		)
		row.add_child(edit)
		row.add_child(forget)
		_keys[k[0]] = edit
		box.add_child(_field(k[1], "", row))

	box.add_child(_section("Brains on this computer"))
	_brains.add_theme_constant_override("separation", 4)
	box.add_child(_brains)

	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_message)
	var buttons := HBoxContainer.new()
	buttons.alignment = BoxContainer.ALIGNMENT_END
	buttons.add_theme_constant_override("separation", 8)
	var close := Button.new()
	close.text = "Close"
	close.theme_type_variation = &"OutlineButton"
	close.pressed.connect(hide)
	_save.text = "Save"
	_save.theme_type_variation = &"AnswerButton"
	_save.pressed.connect(_on_save)
	buttons.add_child(close)
	buttons.add_child(_save)
	box.add_child(buttons)


## Open in the middle of the window and load everything fresh.
func open() -> void:
	_say("Loading…")
	_save.disabled = true
	_forget.clear()
	popup_centered(Vector2i(get_contents_minimum_size()))
	_load()


func _load() -> void:
	Net.request_json(
		"/health",
		func(ok: bool, data: Dictionary):
			if not ok:
				_mode.text = "Backend offline: start it, then reopen Settings."
			elif data.get("mock_agents"):
				_mode.text = "Running in mock mode: residents do fake work, no cost."
			else:
				_mode.text = "Running real agents in each island's git repository."
	)
	Net.request_json(
		"/settings",
		func(ok: bool, data: Dictionary):
			if not ok:
				_say("Couldn't load settings: %s" % data["error"], true)
				return
			_show_settings(data)
			_say("")
	)
	_load_brains()
	Net.request_json(
		"/models",
		func(ok: bool, data: Dictionary):
			_models = data.get("models", []) if ok else []
			_models_error = "" if ok else str(data.get("error", "unavailable"))
	)


## A combo box: the text field (free typing) plus a ▾ button that lists the catalogue's
## models matching what's typed. Godot has no combo box, so it's a LineEdit and a
## MenuButton (a button that opens its own PopupMenu) side by side.
func _model_combo(edit: LineEdit) -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	edit.placeholder_text = "e.g. anthropic/claude-sonnet-4.5"
	var pick := MenuButton.new()
	pick.icon = ARROW
	pick.theme_type_variation = &"OutlineButton"
	pick.tooltip_text = "Pick from OpenRouter's models (the text filters the list)"
	pick.flat = false
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
	row.add_child(edit)
	row.add_child(pick)
	return row


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


func _load_brains() -> void:
	Net.request_json(
		"/brains/detail",
		func(ok: bool, brains: Dictionary):
			for child in _brains.get_children():
				child.queue_free()
			if not ok:
				_brains.add_child(_help("Couldn't ask the backend: %s" % brains["error"]))
				return
			for id in ResidentData.BRAINS:
				var info: Dictionary = brains.get(id, {})
				var row := Label.new()
				row.theme_type_variation = &"BodyText"
				row.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
				row.text = (
					"%s  %s · %s"
					% ["✓" if info.get("ok") else "✗", ResidentData.BRAINS[id], info.get("hint", "unknown")]
				)
				if not info.get("ok"):
					row.add_theme_color_override("font_color", Color("8A8FA8"))
				_brains.add_child(row)
	)


## Fill the fields from a GET / PUT /settings reply.
func _show_settings(data: Dictionary) -> void:
	_loaded = data.get("settings", {})
	var locked: Array = data.get("from_env", [])
	for key in _inputs:
		var input = _inputs[key]  # a CheckButton or a LineEdit
		var value = _loaded.get(key)
		if input is CheckButton:
			input.set_pressed_no_signal(bool(value))
			input.disabled = key in locked
		else:
			input.text = "" if value == null else str(value)
			input.editable = key not in locked
		if key in locked:
			input.tooltip_text = "Set in the backend's .env, which wins over Settings."
	var saved: Dictionary = data.get("secrets", {})
	for key in _keys:
		var edit: LineEdit = _keys[key]
		edit.clear()
		edit.editable = key not in locked
		edit.placeholder_text = (
			"set in .env" if key in locked else ("saved ✓ (type to replace)" if saved.get(key) else "not set")
		)
	_save.disabled = false


func _on_save() -> void:
	var settings := {}
	for key in _inputs:
		var input = _inputs[key]  # a CheckButton or a LineEdit
		var value = input.button_pressed if input is CheckButton else input.text.strip_edges()
		var before = _loaded.get(key)
		if (
			(input is CheckButton and value != bool(before))
			or (input is LineEdit and value != ("" if before == null else str(before)))
		):
			settings[key] = value
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
			_say("Saved. Changes apply from the next package.")
			_load_brains(),
		HTTPClient.METHOD_PUT,
		{"settings": settings, "secrets": secrets}
	)


func _say(text: String, error := false) -> void:
	_message.text = text
	_message.add_theme_color_override("font_color", Color("C0392B") if error else Color("4A5472"))


func _section(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"FieldLabel"
	label.add_theme_font_size_override("font_size", 16)
	return label


func _help(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"LabelMuted"
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return label


## A label, the input, and a line of help under it.
func _field(title: String, help: String, input: Control) -> Control:
	var field := VBoxContainer.new()
	field.add_theme_constant_override("separation", 2)
	if title:
		var label := Label.new()
		label.text = title
		label.theme_type_variation = &"BodyText"
		field.add_child(label)
	field.add_child(input)
	if help:
		field.add_child(_help(help))
	return field
