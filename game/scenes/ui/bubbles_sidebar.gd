class_name BubblesSidebar
extends PanelContainer
## The Bubbles sidebar: one card per open bubble in Net.island, the "N need you"
## pill, and the All / Questions / Reviews tabs.
##
## Cards are kept by bubble id and only added or removed when bubbles come and
## go, so a reply you're halfway through typing survives other events.

const CardScene := preload("res://scenes/ui/bubble_card.tscn")

## tab -> bubble kinds it shows ("all" shows everything)
const TABS := {"all": [], "questions": ["confused", "struggling", "permission"], "reviews": ["ready"]}

var _tab := "all"
var _cards := {}  # bubble id -> BubbleCard


func _ready() -> void:
	%TabAll.pressed.connect(_set_tab.bind("all"))
	%TabQuestions.pressed.connect(_set_tab.bind("questions"))
	%TabReviews.pressed.connect(_set_tab.bind("reviews"))
	Net.island_changed.connect(refresh)
	Net.disconnected.connect(refresh)

	# Godot concept: a Timer node fires `timeout` every `wait_time` seconds.
	var timer := Timer.new()
	timer.wait_time = 15.0
	timer.timeout.connect(func():
		for card in _cards.values():
			card.refresh_age())
	add_child(timer)
	timer.start()
	refresh()


## Open the reply box of one bubble (the Residents screen's Answer button).
func open_reply(bubble_id: String) -> void:
	_set_tab("all")
	var card: BubbleCard = _cards.get(bubble_id)
	if card:
		card.open_reply()
		%Scroll.ensure_control_visible(card)


func refresh() -> void:
	var bubbles: Array = Net.island.get("bubbles", [])
	var open_ids := []
	for bubble in bubbles:
		var id := str(bubble["id"])
		open_ids.append(id)
		if not _cards.has(id):
			_cards[id] = CardScene.instantiate()
			%Cards.add_child(_cards[id])
		var resident := Net.find_resident(str(bubble["agent"]))
		var place: String = Net.find_building(str(resident.get("building", ""))).get("name", "Island")
		_cards[id].setup(bubble, resident, place)
		_cards[id].visible = _tab == "all" or bubble["kind"] in TABS[_tab]

	for id in _cards.keys():
		if id not in open_ids:
			_cards[id].queue_free()
			_cards.erase(id)

	%NeedLabel.text = "%d need you" % bubbles.size()
	%NeedPill.visible = bubbles.size() > 0
	var shown := _cards.values().filter(func(c): return c.visible).size()
	%EmptyLabel.visible = shown == 0
	if Net.island.is_empty():
		%EmptyLabel.text = "Waiting for the island…" if not Net.is_online else "Loading…"
	elif bubbles.is_empty():
		%EmptyLabel.text = "All quiet. Nobody needs you right now."
	else:
		%EmptyLabel.text = "Nothing here. Try another tab."


func _set_tab(tab: String) -> void:
	_tab = tab
	for pair in [["all", %TabAll], ["questions", %TabQuestions], ["reviews", %TabReviews]]:
		pair[1].theme_type_variation = &"TabActive" if pair[0] == tab else &"TabInactive"
	refresh()
