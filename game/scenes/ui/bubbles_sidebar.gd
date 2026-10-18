class_name BubblesSidebar
extends PanelContainer
## The Bubbles sidebar: one card per open bubble in Net.island, the "N need you"
## pill, and the Questions / Status tabs.
## Status lists every open task instead of bubbles: a card each with where it is,
## who carries it, and a dot per workflow step. Clicking one opens that building.
##
## Cards are kept by bubble id and only added or removed when bubbles come and
## go, so a reply you're halfway through typing survives other events.
##
## Above the Island mood card: one pill per brain this island's residents use, with
## what was used of it today (GET /usage, every USAGE_EVERY seconds).

signal building_requested(type: String)

const CardScene := preload("res://scenes/ui/bubble_card.tscn")
const LINE := Color("E3E5EE")
const USAGE_EVERY := 20.0  # seconds between GET /usage
const TERMINAL_ICON := preload("res://assets/icons/terminal.svg")
const GLOBE_ICON := preload("res://assets/icons/globe.svg")
## Each brain's pill: [icon, colour].
const BRAIN_LOOKS := {
	"claude-code": [TERMINAL_ICON, Color("D97757")],
	"codex": [TERMINAL_ICON, Color("10A37F")],
	"gemini": [TERMINAL_ICON, Color("4285F4")],
	"openrouter": [GLOBE_ICON, Color("6467F2")],
}
const GREY := Color("8A8FA8")
const INK := Color("292E50")

## tab -> bubble kinds it shows
const TABS := {"questions": ["confused", "struggling", "permission", "ready", "tired"], "status": []}

var _tab := "questions"
var _cards := {}  # bubble id -> BubbleCard
var _popup := AnswerPopup.new()  # one popup, reused for every bubble
var _task_rows := VBoxContainer.new()  # the Status tab's rows, rebuilt on every refresh
var _usage := VBoxContainer.new()  # the usage pills, above the mood card
var _usage_data: Array = []  # the last GET /usage `brains`
var _usage_shown: Array = []  # the brains the pills show now


func _ready() -> void:
	%TabQuestions.pressed.connect(_set_tab.bind("questions"))
	%TabStatus.pressed.connect(_set_tab.bind("status"))
	Net.island_changed.connect(refresh)
	Net.disconnected.connect(refresh)
	add_child(_popup)
	%Cards.add_child(_task_rows)
	_popup.answered.connect(
		func(id: String):
			if _cards.has(id):
				_cards[id].mark_sent()
	)

	# Godot concept: a Timer node fires `timeout` every `wait_time` seconds.
	var timer := Timer.new()
	timer.wait_time = 15.0
	timer.timeout.connect(
		func():
			for card in _cards.values():
				card.refresh_age()
	)
	add_child(timer)
	timer.start()
	refresh()

	# The usage pills go just above the Island mood card.
	var mood: Control = $Rows/MoodCard
	_usage.add_theme_constant_override("separation", 8)
	mood.get_parent().add_child(_usage)
	mood.get_parent().move_child(_usage, mood.get_index())
	var usage_timer := Timer.new()
	usage_timer.wait_time = USAGE_EVERY
	usage_timer.timeout.connect(_load_usage)
	add_child(usage_timer)
	usage_timer.start()
	Net.connected.connect(_load_usage)
	Net.island_changed.connect(_show_usage)  # another island, or a resident switched brains
	_load_usage()


## Open the answer popup of one bubble (a card's Answer, or the Residents screen's).
func open_reply(bubble_id: String) -> void:
	var card: BubbleCard = _cards.get(bubble_id)
	if card:
		_popup.open(card.bubble)


func refresh() -> void:
	var bubbles: Array = Net.island.get("bubbles", [])
	var open_ids := []
	for bubble in bubbles:
		var id := str(bubble["id"])
		open_ids.append(id)
		if not _cards.has(id):
			_cards[id] = CardScene.instantiate()
			%Cards.add_child(_cards[id])
			_cards[id].answer_requested.connect(func(b: Dictionary): open_reply(str(b["id"])))
		var resident := Net.find_resident(str(bubble["agent"]))
		var place: String = Net.find_building(str(resident.get("building", ""))).get("name", "Island")
		_cards[id].setup(bubble, resident, place)
		_cards[id].visible = bubble["kind"] in TABS[_tab]

	for id in _cards.keys():
		if id not in open_ids:
			_cards[id].queue_free()
			_cards.erase(id)

	_show_tasks()
	%NeedLabel.text = "%d need you" % bubbles.size()
	%NeedPill.visible = bubbles.size() > 0
	var shown := _cards.values().filter(func(c): return c.visible).size()
	if _tab == "status":
		shown = _task_rows.get_child_count()
	%EmptyLabel.visible = shown == 0
	if Net.island.is_empty():
		%EmptyLabel.text = "Waiting for the island…" if not Net.is_online else "Loading…"
	elif _tab == "status":
		%EmptyLabel.text = "No open tasks."
	elif bubbles.is_empty():
		%EmptyLabel.text = "All quiet. Nobody needs you right now."
	else:
		%EmptyLabel.text = "Nothing here. Try another tab."


func _set_tab(tab: String) -> void:
	_tab = tab
	for pair in [["questions", %TabQuestions], ["status", %TabStatus]]:
		pair[1].theme_type_variation = &"TabActive" if pair[0] == tab else &"TabInactive"
	refresh()


## Ask the backend what each brain was used for today and redraw the pills.
func _load_usage() -> void:
	if not Net.is_online:
		return
	Net.request_json(
		"/usage",
		func(ok: bool, data: Dictionary):
			_usage_data = data.get("brains", []) if ok else []
			_show_usage(true)  # redraw: the numbers may have changed
	)


## Pills only for the brains this island's residents use (Claude-only island: just Claude).
## Without `force`, only redraws when that set of brains changed.
func _show_usage(force := false) -> void:
	var on_island := {}
	for r in Net.island.get("residents", []):
		on_island[str(r.get("brain", ""))] = true
	var entries := _usage_data.filter(func(e): return on_island.has(str(e.get("brain", ""))))
	var brains := entries.map(func(e): return str(e["brain"]))
	if brains == _usage_shown and not force:
		return  # same pills already showing
	_usage_shown = brains
	for pill in _usage.get_children():
		pill.queue_free()
	for entry in entries:
		_usage.add_child(_usage_pill(entry))


## "[icon] Claude Code   182k tokens · $1.24", like the Island mood card.
func _usage_pill(entry: Dictionary) -> Control:
	var brain := str(entry.get("brain", ""))
	var look: Array = BRAIN_LOOKS.get(brain, [TERMINAL_ICON, GREY])
	var turns := int(entry.get("turns", 0))
	var tokens := int(entry.get("tokens", 0))
	var cost := float(entry.get("cost", 0.0))
	var pill := PanelContainer.new()
	pill.theme_type_variation = &"MoodCard"
	pill.tooltip_text = (
		"What your residents used of %s today: %d turn%s, %s tokens, $%.2f.\nAs the agent reports it, not your whole account."
		% [ResidentData.BRAINS.get(brain, brain), turns, "" if turns == 1 else "s", _tokens_text(tokens), cost]
	)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var icon := TextureRect.new()
	icon.texture = look[0]
	icon.modulate = look[1]
	icon.custom_minimum_size = Vector2(18, 18)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(icon)
	var title := UI.label(ResidentData.BRAINS.get(brain, brain.capitalize()), 14, true)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)
	var parts: Array[String] = []
	if tokens > 0:
		parts.append("%s tokens" % _tokens_text(tokens))
	if cost > 0.0:
		parts.append("$%.2f" % cost)
	if parts.is_empty() and turns > 0:
		parts.append("%d turn%s" % [turns, "" if turns == 1 else "s"])
	var summary := UI.label(" · ".join(parts) if parts else "not used today", 14, not parts.is_empty())
	row.add_child(summary)
	pill.add_child(row)
	return pill


## 950, 12.4k, 182k, 1.3M.
static func _tokens_text(tokens: int) -> String:
	if tokens < 1000:
		return str(tokens)
	if tokens < 100000:
		return "%.1fk" % (tokens / 1000.0)
	if tokens < 1000000:
		return "%dk" % roundi(tokens / 1000.0)
	return "%.1fM" % (tokens / 1000000.0)


## Status tab: one row per open task with its status and where it is now.
func _show_tasks() -> void:
	for row in _task_rows.get_children():
		_task_rows.remove_child(row)  # queue_free alone leaves it counted until the frame ends
		row.queue_free()
	_task_rows.visible = _tab == "status"
	if _tab != "status":
		return
	_task_rows.add_theme_constant_override("separation", 10)
	for task in Net.island.get("tasks", []):
		_task_rows.add_child(_task_card(task))


## One open task: the building's icon, the title, "#42 · At the Workshop", the steps
## so far as dots, and who carries it with a pill saying what they're doing.
func _task_card(task: Dictionary) -> Control:
	var building := Net.find_building(str(task.get("building", "")))
	var type := str(building.get("type", ""))
	var queued: bool = task.get("status") == "queued" or not BuildingData.TYPES.has(type)
	var info: Dictionary = BuildingData.TYPES.get(type, BuildingData.TYPES["mailbox"])
	var color: Color = GREY if queued else info["color"]

	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", _box(Color.WHITE, 16, LINE, 2, Vector4(12, 10, 12, 10)))
	card.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	card.tooltip_text = "Open the %s" % building.get("name", "Mailbox")
	var go_to := "mailbox" if queued else type
	card.gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				building_requested.emit(go_to)
	)
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 8)
	card.add_child(rows)

	# The building's icon on a tinted tile, the title, and where it is.
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 10)
	var tile := PanelContainer.new()
	tile.custom_minimum_size = Vector2(36, 36)
	tile.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	tile.add_theme_stylebox_override("panel", _box(color.lerp(Color.WHITE, 0.8), 10))
	var icon := TextureRect.new()
	icon.texture = info["icon"]
	icon.modulate = color.darkened(0.1)
	icon.custom_minimum_size = Vector2(18, 18)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	tile.add_child(icon)
	top.add_child(tile)
	var texts := VBoxContainer.new()
	texts.add_theme_constant_override("separation", 0)
	texts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var title := UI.label(str(task.get("title", "Task")), 15, true)
	title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	title.custom_minimum_size.x = 1  # wrap to the sidebar, don't widen it
	texts.add_child(title)
	var where := "Waiting in line" if queued else "At the %s" % building.get("name", info["name"])
	var issue = task.get("issue")
	texts.add_child(UI.label(("#%s · " % issue if issue else "") + where, 12, false))
	top.add_child(texts)
	rows.add_child(top)

	rows.add_child(_steps(-1 if queued else Protocol.STEP_ORDER.find(type)))

	# Who carries it, and what they're doing there.
	var bottom := HBoxContainer.new()
	bottom.add_theme_constant_override("separation", 8)
	var resident := Net.find_resident(str(task.get("agent", "")))
	if not resident.is_empty():
		bottom.add_child(UI.avatar(resident, 24))
	var who := UI.label(str(resident.get("name", "Nobody yet")), 13, not resident.is_empty())
	who.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bottom.add_child(who)
	bottom.add_child(_pill("In line" if queued else str(info["stage"]), color))
	rows.add_child(bottom)
	return card


## A dot per workflow step on this island, joined by a line: steps done filled in,
## the current one big in its building's colour, the rest hollow. `current` is an
## index into Protocol.STEP_ORDER (-1: not started).
func _steps(current: int) -> Control:
	var have := {}
	for b in Net.island.get("buildings", []):
		have[str(b.get("type", ""))] = true
	var steps: Array = Protocol.STEP_ORDER.filter(func(t): return have.has(t))
	var at := steps.find(Protocol.STEP_ORDER[current]) if current >= 0 else -1
	var track := Control.new()
	track.custom_minimum_size = Vector2(0, 16)
	track.mouse_filter = Control.MOUSE_FILTER_PASS
	track.tooltip_text = " → ".join(steps.map(func(t): return Protocol.STEP_NAMES[t]))
	# Godot concept: the `draw` signal fires when the control is drawn; draw_* calls
	# inside it paint straight onto the control.
	track.draw.connect(
		func():
			if steps.size() < 2:
				return
			var y := track.size.y / 2.0
			var gap := (track.size.x - 16.0) / (steps.size() - 1)
			for i in steps.size() - 1:
				var done := i < at
				track.draw_line(
					Vector2(8 + i * gap, y), Vector2(8 + (i + 1) * gap, y), INK if done else LINE, 3.0, true
				)
			for i in steps.size():
				var c := Vector2(8 + i * gap, y)
				var step_color: Color = BuildingData.TYPES[steps[i]]["color"]
				if i == at:
					track.draw_circle(c, 7.0, step_color)
					track.draw_circle(c, 3.0, Color.WHITE)
				elif i < at:
					track.draw_circle(c, 5.0, step_color)
				else:
					track.draw_circle(c, 5.0, Color.WHITE)
					track.draw_arc(c, 4.0, 0, TAU, 16, LINE, 2.0, true)
	)
	return track


## "Coding" on a tinted pill.
func _pill(text: String, color: Color) -> Control:
	var pill := PanelContainer.new()
	pill.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	pill.add_theme_stylebox_override(
		"panel", _box(color.lerp(Color.WHITE, 0.8), 12, Color.TRANSPARENT, 0, Vector4(10, 3, 10, 3))
	)
	var label := UI.label(text, 12, true)
	label.add_theme_color_override("font_color", color.darkened(0.25))
	pill.add_child(label)
	return pill


func _box(bg: Color, radius: int, border := Color.TRANSPARENT, width := 0, pad := Vector4.ZERO) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = bg
	style.set_corner_radius_all(radius)
	style.border_color = border
	style.set_border_width_all(width)
	style.content_margin_left = pad.x
	style.content_margin_top = pad.y
	style.content_margin_right = pad.z
	style.content_margin_bottom = pad.w
	style.anti_aliasing = true
	return style
