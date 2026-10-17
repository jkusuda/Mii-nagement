class_name Home
extends Control
## The Archipelago: the home screen. Every island on the backend as a little
## picture of its 3D model with its status, plus "Waiting on you": the open
## bubbles of *every* island, grouped by island. Click an island (or "Visit")
## to go there.
##
## Everything comes from the Islands autoload (GET /islands + each island's
## snapshot). New island / Caretaker / Settings aren't built yet.

signal visit(island_id: String)

const AnswerButtonScript := preload("res://scenes/ui/answer_button.gd")

## bubble kind -> how the sidebar says it
const ASKS := {
	"confused": "%s has a question", "struggling": "%s is stuck", "permission": "%s wants a key",
	"ready": "%s finished something", "tired": "%s needs a nap",
}

var _cards := {}  # island id -> IslandCard


func _ready() -> void:
	Islands.changed.connect(func(): if is_visible_in_tree(): _show())
	Net.connected.connect(_show_connection)
	Net.disconnected.connect(_show_connection)
	visibility_changed.connect(func():
		if visible:
			Islands.refresh()
			_show())
	_show()


func _show() -> void:
	_show_connection()
	var islands := Islands.islands
	%Count.text = "%d island%s" % [islands.size(), "" if islands.size() == 1 else "s"]
	_show_cards(islands)
	_show_waiting(islands)


func _show_connection() -> void:
	var online := Net.is_online
	%StatusLabel.text = "Connected" if online else "Reconnecting…"
	var count := Islands.islands.size()
	%SyncedLabel.text = ("%d island%s synced" % [count, "" if count == 1 else "s"]) if online else "backend offline"
	var dot: StyleBoxFlat = get_theme_stylebox("panel", "StatusDot").duplicate()
	dot.bg_color = Color("39C16F") if online else Color("F2994A")
	%StatusDot.add_theme_stylebox_override("panel", dot)


func _show_cards(islands: Array) -> void:
	var ids := islands.map(func(i): return str(i["id"]))
	for id in _cards.keys():
		if id not in ids:
			_cards[id].queue_free()
			_cards.erase(id)
	for island in islands:
		var id := str(island["id"])
		if not _cards.has(id):
			var card := IslandCard.new()
			card.visit.connect(visit.emit)
			%Grid.add_child(card)
			_cards[id] = card
		_cards[id].setup(island)
		%Grid.move_child(_cards[id], ids.find(id))
	%Grid.move_child(%NewIsland, -1)


## "Waiting on you": one card per island with open bubbles.
func _show_waiting(islands: Array) -> void:
	for child in %Groups.get_children():
		child.queue_free()
	var waiting := islands.filter(func(i): return not i.get("bubbles", []).is_empty())
	var total := Islands.all_bubbles().size()
	%WaitingSummary.text = "%d bubble%s across %d island%s" % [total, "" if total == 1 else "s",
		waiting.size(), "" if waiting.size() == 1 else "s"] if total else "Nobody's waiting. Enjoy the quiet!"
	for island in waiting:
		%Groups.add_child(_group(island))


func _group(island: Dictionary) -> Control:
	var card := PanelContainer.new()
	card.theme_type_variation = &"CardPanel"
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 8)
	card.add_child(rows)

	var header := HBoxContainer.new()
	var title := _label(str(island.get("name", "")), 16, true)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var pill := PanelContainer.new()
	pill.theme_type_variation = &"NeedPill"
	var count := _label(str(island["bubbles"].size()), 12, true)
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
	go.set_script(AnswerButtonScript)  # the wavy orange button
	go.theme_type_variation = &"AnswerButton"
	go.custom_minimum_size = Vector2(258, 34)
	go.text = "Visit %s" % island.get("name", "")
	go.pressed.connect(visit.emit.bind(str(island["id"])))
	rows.add_child(go)
	return card


func _bubble_row(bubble: Dictionary, resident: Dictionary) -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var color := Color(str(resident.get("color", "#8A8FA8")))
	var who := str(resident.get("name", bubble["agent"]))

	var avatar := PanelContainer.new()
	avatar.custom_minimum_size = Vector2(24, 24)
	var circle: StyleBoxFlat = get_theme_stylebox("panel", "AvatarCircle").duplicate()
	circle.bg_color = color.lerp(Color.WHITE, 0.75)
	circle.set_corner_radius_all(12)
	avatar.add_theme_stylebox_override("panel", circle)
	var initial := _label(who.left(1), 12, true)
	initial.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	initial.add_theme_color_override("font_color", color.darkened(0.2))
	avatar.add_child(initial)

	var kind := str(bubble["kind"])
	var text := _label(ASKS.get(kind, "%s needs you") % who, 13, true)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	text.tooltip_text = str(bubble.get("text", ""))
	text.mouse_filter = Control.MOUSE_FILTER_PASS
	var tag := _label(ResidentData.BUBBLE_TAGS.get(kind, ["Needs you"])[0], 11, true)
	tag.add_theme_color_override("font_color", BubbleCard.KIND_COLORS.get(kind, Color("7553DF")))

	for part in [avatar, text, tag]:
		part.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(part)
	return row


func _label(text: String, size: int, strong: bool) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"LabelStrong" if strong else &"LabelMuted"
	label.add_theme_font_size_override("font_size", size)
	return label
