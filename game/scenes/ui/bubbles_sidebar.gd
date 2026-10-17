class_name BubblesSidebar
extends PanelContainer
## The Bubbles sidebar: one card per open bubble in Net.island, the "N need you"
## pill, and the All / Questions / Status tabs.
## Status lists every open task and where it is, instead of bubbles.
##
## Cards are kept by bubble id and only added or removed when bubbles come and
## go, so a reply you're halfway through typing survives other events.

const CardScene := preload("res://scenes/ui/bubble_card.tscn")

## tab -> bubble kinds it shows ("all" shows everything)
const TABS := {"all": [], "questions": ["confused", "struggling", "permission"], "status": []}

var _tab := "all"
var _cards := {}  # bubble id -> BubbleCard
var _popup := AnswerPopup.new()  # one popup, reused for every bubble
var _task_rows := VBoxContainer.new()  # the Status tab's rows, rebuilt on every refresh


func _ready() -> void:
	%TabAll.pressed.connect(_set_tab.bind("all"))
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
		_cards[id].visible = _tab == "all" or bubble["kind"] in TABS[_tab]

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
	for pair in [["all", %TabAll], ["questions", %TabQuestions], ["status", %TabStatus]]:
		pair[1].theme_type_variation = &"TabActive" if pair[0] == tab else &"TabInactive"
	refresh()


## Status tab: one row per open task with its status and where it is now.
func _show_tasks() -> void:
	for row in _task_rows.get_children():
		row.queue_free()
	_task_rows.visible = _tab == "status"
	if _tab != "status":
		return
	for task in Net.island.get("tasks", []):
		var where: String = Net.find_building(str(task.get("building", ""))).get("name", "")
		var who: String = Net.find_resident(str(task.get("agent", ""))).get("name", "")
		var status := "Waiting in line" if task["status"] == "queued" else "At " + where
		if who != "":
			status += " · " + who
		var card := PanelContainer.new()
		var rows := VBoxContainer.new()
		card.add_child(rows)
		rows.add_child(UI.label(str(task.get("title", "Task")), 14, true))
		rows.add_child(UI.label(status, 12, false))
		_task_rows.add_child(card)
