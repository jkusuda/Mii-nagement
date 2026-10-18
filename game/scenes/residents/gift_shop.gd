class_name GiftShop
extends Control
## The gift shop, opened from the resident creator's "Find more" tile: search the
## official MCP Registry for more MCP servers and put them on the shelf as gifts.
##
##   GiftShop.open(self, gift_info, func(gifts): ...)
##
## The backend does the work (backend/mii/gifts.py): GET /gifts/search finds servers
## that can run here, POST /gifts adds one, PUT /gifts/{id} saves the values it needs
## (API keys go to the OS keychain), DELETE /gifts/{id} takes it off the shelf.
## Each change calls `on_change` with the new GET /gifts answer.
##
## Built in code, like SettingsPopup: a cream panel in the middle of a dimmed window.
## Search results scroll on the left, the shelf (gifts added before) on the right.
## Esc, Done, the close button or a click on the backdrop closes it.

const SEARCH_ICON := preload("res://assets/icons/search.svg")
const CLOSE_ICON := preload("res://assets/icons/x.svg")
const CHECK_ICON := preload("res://assets/icons/check.svg")
const SPROUT_ICON := preload("res://assets/icons/sprout.svg")
const INK := Color("292E50")
const MUTED := Color("8A8FA8")
const BAD := Color("C0392B")
const GOOD := Color("2FA36B")
const ROW_COLOR := Color("F7F3E3")  # a result or shelf row, on the white card
const LIST_HEIGHT := 420  # the two scrolling lists
const IDEAS := ["notion", "postgres", "slack", "figma", "sentry", "linear"]  # one-click searches

var _gifts := {}  # the latest GET /gifts answer: gift id -> {ok, label, hint, key, custom?, env?}
var _on_change: Callable
var _query := LineEdit.new()
var _results := VBoxContainer.new()
var _shelf := VBoxContainer.new()
var _status := Label.new()
var _searching := false


## Open the shop over the whole game. `gifts` is the creator's GET /gifts answer.
static func open(from: Node, gifts: Dictionary, on_change: Callable) -> void:
	var shop := GiftShop.new()
	shop._gifts = gifts
	shop._on_change = on_change
	# On top of everything else in the game scene, like SettingsPopup.
	var tree := from.get_tree()
	(tree.current_scene if tree.current_scene else tree.root).add_child(shop)
	Audio.sfx("popup_open")
	shop._query.grab_focus.call_deferred()


func _init() -> void:
	# Godot concept: anchors pin a Control to its parent's edges; PRESET_FULL_RECT
	# stretches this overlay over the whole window.
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_STOP  # nothing underneath gets clicks

	var backdrop := ColorRect.new()
	backdrop.color = Color(INK, 0.45)
	backdrop.set_anchors_preset(Control.PRESET_FULL_RECT)
	backdrop.gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				queue_free()
	)
	add_child(backdrop)

	# Godot concept: a CenterContainer keeps its child at its own size, in the middle.
	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(center)
	var panel := PanelContainer.new()
	panel.theme_type_variation = &"ScreenPanel"
	panel.custom_minimum_size = Vector2(940, 0)
	center.add_child(panel)
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 14)
	panel.add_child(rows)

	rows.add_child(_header())
	rows.add_child(_search_row())
	var columns := HBoxContainer.new()
	columns.add_theme_constant_override("separation", 14)
	columns.add_child(_list_card("Found", "click one to add it", _results, 0))
	columns.add_child(_list_card("On the shelf", "gifts you added", _shelf, 300))
	rows.add_child(columns)
	rows.add_child(_footer())


func _ready() -> void:
	_show_ideas()
	_fill_shelf()


func _unhandled_key_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		queue_free()
		get_viewport().set_input_as_handled()


# --- Layout --------------------------------------------------------------------------


func _header() -> Control:
	var header := HBoxContainer.new()
	var titles := VBoxContainer.new()
	titles.add_theme_constant_override("separation", 0)
	titles.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	titles.add_child(_label("Gift shop", &"ScreenTitle"))
	titles.add_child(
		_label("More tools for your residents: MCP servers from the official MCP Registry.", &"LabelMuted")
	)
	header.add_child(titles)
	var close := Button.new()
	close.icon = CLOSE_ICON
	close.tooltip_text = "Close"
	close.theme_type_variation = &"FlatIconButton"
	close.focus_mode = Control.FOCUS_NONE
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	close.pressed.connect(queue_free)
	header.add_child(close)
	return header


func _search_row() -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	_query.placeholder_text = "Search the registry: browser, notion, postgres…"
	_query.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_query.custom_minimum_size.y = 40
	_query.text_submitted.connect(func(_text): _search())
	row.add_child(_query)
	var go := Button.new()
	go.text = "Search"
	go.icon = SEARCH_ICON
	go.theme_type_variation = &"OutlineButton"
	go.custom_minimum_size = Vector2(120, 40)
	go.focus_mode = Control.FOCUS_NONE
	go.pressed.connect(_search)
	row.add_child(go)
	return row


## A white card with a title, holding a list that scrolls when it's taller than LIST_HEIGHT.
## width 0 = take the room that's left.
func _list_card(title: String, hint: String, list: VBoxContainer, width: float) -> PanelContainer:
	var card := PanelContainer.new()
	card.theme_type_variation = &"SectionPanel"
	if width > 0:
		card.custom_minimum_size.x = width
	else:
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	card.add_child(box)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 8)
	var heading := _label(title, &"SectionTitle")
	heading.autowrap_mode = TextServer.AUTOWRAP_OFF
	head.add_child(heading)
	var muted := _label(hint, &"LabelMuted")
	muted.autowrap_mode = TextServer.AUTOWRAP_OFF
	muted.size_flags_vertical = Control.SIZE_SHRINK_END
	head.add_child(muted)
	box.add_child(head)
	# Godot concept: a ScrollContainer shows a scrollbar when its one child is taller than it.
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size.y = LIST_HEIGHT
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	list.add_theme_constant_override("separation", 8)
	scroll.add_child(list)
	box.add_child(scroll)
	return card


func _footer() -> Control:
	var footer := HBoxContainer.new()
	footer.add_theme_constant_override("separation", 10)
	var sprout := TextureRect.new()
	sprout.texture = SPROUT_ICON
	sprout.modulate = GOOD
	sprout.custom_minimum_size = Vector2(18, 18)
	sprout.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	sprout.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	sprout.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	footer.add_child(sprout)
	_status.theme_type_variation = &"LabelMuted"
	_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	footer.add_child(_status)
	var done := Button.new()
	done.text = "Done"
	done.theme_type_variation = &"OutlineButton"
	done.custom_minimum_size = Vector2(110, 40)
	done.focus_mode = Control.FOCUS_NONE
	done.pressed.connect(queue_free)
	footer.add_child(done)
	_say("A gift runs on this computer whenever a resident uses it: only add ones you trust.", MUTED)
	return footer


# --- Search results --------------------------------------------------------------------


## Before the first search: a few ideas, one click each.
func _show_ideas() -> void:
	_clear(_results)
	_results.add_child(_label("Search for a tool, or try one of these:", &"LabelMuted"))
	var chips := HFlowContainer.new()
	chips.add_theme_constant_override("h_separation", 6)
	chips.add_theme_constant_override("v_separation", 6)
	for idea in IDEAS:
		var chip := Button.new()
		chip.text = idea
		chip.theme_type_variation = &"FilterChip"
		chip.focus_mode = Control.FOCUS_NONE
		chip.pressed.connect(
			func():
				_query.text = idea
				_search()
		)
		chips.add_child(chip)
	_results.add_child(chips)


func _search() -> void:
	var query := _query.text.strip_edges()
	if query == "" or _searching:
		return
	_searching = true
	_clear(_results)
	_results.add_child(_label("Searching the MCP Registry for “%s”… it can take a few seconds." % query, &"LabelMuted"))
	Net.request_json(
		"/gifts/search?q=%s" % query.uri_encode(),
		func(ok: bool, data: Dictionary):
			if not is_inside_tree():
				return  # closed while it searched
			_searching = false
			_clear(_results)
			if not ok:
				_results.add_child(_label("Couldn't search: %s" % data.get("error", "unknown error"), &"LabelMuted"))
				return
			var servers: Array = data.get("servers", [])
			if servers.is_empty():
				_results.add_child(
					_label("Nothing that runs here matched “%s”. Try another word." % query, &"LabelMuted")
				)
			for server in servers:
				_results.add_child(_result_row(server))
	)


## One registry server as a clickable row: its name, description and package (and what
## it needs), with an Add button. Clicking anywhere on the row adds it too.
func _result_row(server: Dictionary) -> PanelContainer:
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", _row_style())
	card.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	row.mouse_filter = Control.MOUSE_FILTER_IGNORE  # clicks go to the card
	card.add_child(row)

	var text := VBoxContainer.new()
	text.add_theme_constant_override("separation", 2)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var title := _label(str(server.get("label", server["name"])), &"BodyText")
	title.add_theme_color_override("font_color", INK)
	text.add_child(title)
	var about := str(server.get("description", ""))
	if about != "":
		var line := _label(about, &"LabelMuted")
		line.max_lines_visible = 2
		text.add_child(line)
	var needs: Array = server.get("env", []).filter(func(e): return e.get("required", false))
	var package := "%s %s" % [server.get("runner", ""), server.get("package", "")]
	if not needs.is_empty():
		package += "  ·  needs " + ", ".join(needs.map(func(e): return str(e["name"])))
	var small := _label(package, &"LabelMuted")
	small.add_theme_font_size_override("font_size", 11)
	text.add_child(small)
	row.add_child(text)

	var add := Button.new()
	add.focus_mode = Control.FOCUS_NONE
	add.custom_minimum_size = Vector2(96, 34)
	add.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(add)
	if server.get("added", false):
		_show_added(add, card)
		return card
	add.text = "Add"
	add.theme_type_variation = &"OutlineButton"
	var adding := func():
		if add.disabled:
			return
		add.disabled = true
		add.text = "Adding…"
		_add(server, add, card)
	add.pressed.connect(adding)
	card.gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				adding.call()
	)
	return card


func _show_added(button: Button, card: PanelContainer) -> void:
	button.text = "Added"
	button.icon = CHECK_ICON
	button.disabled = true
	button.add_theme_constant_override("icon_max_width", 16)
	button.add_theme_color_override("font_disabled_color", GOOD)  # done, not greyed out
	button.add_theme_stylebox_override("disabled", StyleBoxEmpty.new())
	button.add_theme_color_override("icon_disabled_color", GOOD)
	card.mouse_default_cursor_shape = Control.CURSOR_ARROW


# --- The shelf: gifts added before -------------------------------------------------------


func _fill_shelf() -> void:
	_clear(_shelf)
	var added: Array = _gifts.keys().filter(func(g): return _gifts[g].get("custom", false))
	if added.is_empty():
		_shelf.add_child(_label("Nothing yet. Gifts you add show up here and in the creator.", &"LabelMuted"))
	for gift in added:
		_shelf.add_child(_shelf_item(gift, _gifts[gift]))


## One added gift: whether it's ready, the values its server needs, and remove.
func _shelf_item(gift: String, info: Dictionary) -> PanelContainer:
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", _row_style())
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 6)
	card.add_child(rows)

	var top := HBoxContainer.new()
	var name_label := _label(str(info.get("label", gift)), &"BodyText")
	name_label.add_theme_color_override("font_color", INK)
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	top.add_child(name_label)
	var remove := Button.new()
	remove.icon = CLOSE_ICON
	remove.theme_type_variation = &"FlatIconButton"
	remove.focus_mode = Control.FOCUS_NONE
	remove.tooltip_text = "Take it off the shelf (and back from every resident)"
	remove.pressed.connect(func(): _change("/gifts/%s" % gift.uri_encode(), HTTPClient.METHOD_DELETE, null, "Removed."))
	top.add_child(remove)
	rows.add_child(top)
	var ready: bool = info.get("ok", false)
	var state := _label("Ready to give" if ready else str(info.get("hint", "")), &"LabelMuted")
	state.add_theme_color_override("font_color", GOOD if ready else BAD)
	rows.add_child(state)

	# The values its server needs. Secrets are typed hidden and never shown again.
	var inputs := {}
	for env in info.get("env", []):
		var field := LineEdit.new()
		field.secret = env.get("secret", false)  # Godot concept: `secret` shows dots instead of the text
		var mark := " ✓ saved" if env.get("set", false) else (" (required)" if env.get("required", false) else "")
		field.placeholder_text = str(env["name"]) + mark
		field.tooltip_text = str(env.get("description", ""))
		rows.add_child(field)
		inputs[env["name"]] = field
	if not inputs.is_empty():
		var save := Button.new()
		save.text = "Save"
		save.theme_type_variation = &"OutlineButton"
		save.size_flags_horizontal = Control.SIZE_SHRINK_END
		save.focus_mode = Control.FOCUS_NONE
		save.pressed.connect(
			func():
				var env := {}
				for env_name in inputs:
					if inputs[env_name].text.strip_edges() != "":
						env[env_name] = inputs[env_name].text.strip_edges()
				if not env.is_empty():
					_change("/gifts/%s" % gift.uri_encode(), HTTPClient.METHOD_PUT, {"env": env}, "Saved.")
		)
		rows.add_child(save)
	return card


# --- Talking to the backend --------------------------------------------------------------


## POST /gifts: put a registry server on the shelf. It answers {"id", "gifts"}.
func _add(server: Dictionary, button: Button, card: PanelContainer) -> void:
	_say("Adding %s…" % server.get("label", server["name"]), MUTED)
	var done := func(ok: bool, data: Dictionary):
		if not is_inside_tree():
			return
		if not ok:
			button.disabled = false
			button.text = "Add"
			_say("Couldn't add it: %s" % data.get("error", "unknown error"), BAD)
			return
		_show_added(button, card)
		_gifts = data.get("gifts", {})
		var info: Dictionary = _gifts.get(str(data.get("id", "")), {})
		if info.get("ok", false):
			_say("Added %s! Hand it to a resident in their Gifts." % info.get("label", ""), GOOD)
		else:
			_say("Added. Before it works: %s" % info.get("hint", ""), BAD)
		_fill_shelf()
		_on_change.call(_gifts)
	Net.request_json("/gifts", done, HTTPClient.METHOD_POST, {"name": server["name"]})


## PUT / DELETE /gifts/{id}: they answer the gifts.
func _change(path: String, method: int, body: Variant, success: String) -> void:
	var done := func(ok: bool, data: Dictionary):
		if not is_inside_tree():
			return
		if not ok:
			_say(str(data.get("error", "that didn't work")), BAD)
			return
		_gifts = data
		_say(success, GOOD)
		_fill_shelf()
		_on_change.call(_gifts)
	Net.request_json(path, done, method, body)


# --- Small helpers ---------------------------------------------------------------------


func _say(text: String, color: Color) -> void:
	_status.text = text
	_status.add_theme_color_override("font_color", color)


func _clear(box: Container) -> void:
	for child in box.get_children():
		child.queue_free()


func _row_style() -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = ROW_COLOR
	style.set_corner_radius_all(12)
	style.set_content_margin_all(10)
	return style


func _label(text: String, variation: StringName) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = variation
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return label
