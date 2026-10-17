class_name Home
extends Control
## The Archipelago: the home screen. Every island on the backend sits in one 3D
## sea (SeaView: the island view's water, sun and shadows), with a name plate
## and status at its shore. "Waiting on you" lists the open bubbles of *every*
## island, grouped by island. Click an island, its plate, or "Visit" to go there.
##
## Everything comes from the Islands autoload (GET /islands + each island's
## snapshot). Settings opens SettingsPopup (game.gd). The Caretaker button is a
## placeholder (TODO.md).

signal visit(island_id: String)
signal new_island_requested
signal settings_requested

var _plates := {}  # island id -> IslandPlate

@onready var sea: SeaView = %Sea


func _ready() -> void:
	sea.island_clicked.connect(visit.emit)
	%NewIsland.pressed.connect(new_island_requested.emit)
	%NewIslandNav.pressed.connect(new_island_requested.emit)
	%Settings.pressed.connect(settings_requested.emit)
	Islands.changed.connect(
		func():
			if is_visible_in_tree():
				_show()
	)
	Clock.ticked.connect(_show_time)
	_show_time()
	Net.connected.connect(_show_connection)
	Net.disconnected.connect(_show_connection)
	visibility_changed.connect(
		func():
			if visible:
				Islands.refresh()
				_show()
	)
	_show()


func _show() -> void:
	_show_connection()
	var islands := Islands.islands
	%Count.text = "%d island%s" % [islands.size(), "" if islands.size() == 1 else "s"]
	_show_islands(islands)
	_show_waiting(islands)


## The connection pill only shows when the backend drops: "Reconnecting… backend offline".
func _show_connection() -> void:
	%ConnectedPill.visible = not Net.is_online
	%StatusLabel.text = "Reconnecting…"
	%SyncedLabel.text = "backend offline"
	var dot: StyleBoxFlat = get_theme_stylebox("panel", "StatusDot").duplicate()
	dot.bg_color = Color("F2994A")
	%StatusDot.add_theme_stylebox_override("panel", dot)


func _show_islands(islands: Array) -> void:
	var entries := islands.map(func(i): return {"id": str(i["id"]), "layout": IslandLayout.from_snapshot(i)})
	sea.show_islands(entries, true)

	var ids := entries.map(func(e): return e["id"])
	for id in _plates.keys():
		if id not in ids:
			_plates[id].sign_panel.queue_free()
			_plates[id].queue_free()
			_plates.erase(id)
	for island in islands:
		var id := str(island["id"])
		if not _plates.has(id):
			var plate := IslandPlate.new()
			plate.visit.connect(visit.emit)
			%Overlay.add_child(plate)
			%Overlay.add_child(plate.sign_panel)
			_plates[id] = plate
		_plates[id].setup(island)


## Keep each plate at its island's front shore and each sign above the island,
## as the sea view resizes.
func _process(_delta: float) -> void:
	if not is_visible_in_tree():
		return
	for id in _plates:
		var plate: IslandPlate = _plates[id]
		var shore := sea.screen_point(id, Vector3(0, 0, SeaView.ISLAND_HALF.y - 1.0))
		plate.position = (shore - Vector2(plate.size.x / 2.0, plate.size.y * 0.35)).round()
		var sign := plate.sign_panel
		var top := sea.screen_point(id, Vector3(SeaView.ISLAND_HALF.x * 0.55, 7.0, -SeaView.ISLAND_HALF.y * 0.4))
		sign.position = (top - sign.size / 2.0).round()
	var slot := sea.screen_point("slot:new")
	%NewIsland.position = (slot - %NewIsland.size / 2.0).round()


## "Waiting on you": one card per island with open bubbles.
func _show_waiting(islands: Array) -> void:
	for child in %Groups.get_children():
		child.queue_free()
	var waiting := islands.filter(func(i): return not i.get("bubbles", []).is_empty())
	var total := Islands.all_bubbles().size()
	%WaitingSummary.text = (
		(
			"%d bubble%s across %d island%s"
			% [total, "" if total == 1 else "s", waiting.size(), "" if waiting.size() == 1 else "s"]
		)
		if total
		else "Nobody's waiting. Enjoy the quiet!"
	)
	for island in waiting:
		%Groups.add_child(_group(island))


func _group(island: Dictionary) -> Control:
	var card := PanelContainer.new()
	card.theme_type_variation = &"CardPanel"
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 8)
	card.add_child(rows)

	var header := HBoxContainer.new()
	var title := UI.label(str(island.get("name", "")), 16, true)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var pill := PanelContainer.new()
	pill.theme_type_variation = &"NeedPill"
	var count := UI.label(str(island["bubbles"].size()), 12, true)
	count.theme_type_variation = &"NeedText"
	pill.add_child(count)
	header.add_child(title)
	header.add_child(pill)
	rows.add_child(header)

	var residents := {}
	for r in island.get("residents", []):
		residents[r["id"]] = r
	for bubble in island["bubbles"]:
		rows.add_child(_bubble_row(bubble, residents.get(bubble["agent"], {})))

	var go := Button.new()
	go.set_script(AnswerButton)  # the wavy orange button
	go.theme_type_variation = &"AnswerButton"
	go.custom_minimum_size = Vector2(258, 34)
	go.text = "Visit %s" % island.get("name", "")
	go.pressed.connect(visit.emit.bind(str(island["id"])))
	rows.add_child(go)
	return card


func _bubble_row(bubble: Dictionary, resident: Dictionary) -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var who := str(resident.get("name", bubble["agent"]))

	var avatar := UI.avatar(resident, 24, 0.75)

	var kind := str(bubble["kind"])
	var look := Protocol.bubble(kind)
	var text := UI.label(look["ask"] % who, 13, true)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	text.tooltip_text = str(bubble.get("text", ""))
	text.mouse_filter = Control.MOUSE_FILTER_PASS
	var tag := UI.label(look["tag"], 11, true)
	tag.add_theme_color_override("font_color", look["color"])

	for part in [avatar, text, tag]:
		part.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(part)
	return row


## The day pill: the real time (or the dev slider's), with a sun or moon.
## No "Day N" here: that's per island, in the island view's top bar.
func _show_time() -> void:
	var night := Clock.is_night()
	%Day.text = Clock.time_text()
	%DayIcon.texture = preload("res://assets/icons/moon.svg") if night else preload("res://assets/icons/sun.svg")
	%DayIcon.modulate = Color("8E9BF0") if night else Color("FDC902")
