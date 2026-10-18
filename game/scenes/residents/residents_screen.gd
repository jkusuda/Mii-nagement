class_name ResidentsScreen
extends PanelContainer
## The Residents tab: a card for every resident, filter chips, and an
## "Invite a resident" tile that opens the creator.
##
## It shows the island's residents (none until the backend sends a snapshot).
## Cards are rebuilt from scratch whenever the data changes,
## which keeps this simple (there are only ever a handful).
##
## game.gd calls show_snapshot(Net.island) after every backend event, so the
## cards stay live (state, mood, activity, bubbles).

signal new_resident_requested
signal edit_requested(resident: Dictionary)
## Answer / Review / Tuck in / Visit on a card.
signal action_requested(resident: Dictionary)

const CardScene := preload("res://scenes/residents/resident_card.tscn")

## filter id -> chip label ("%d" gets the count)
const FILTERS := [["all", "All"], ["needs", "Needs you · %d"], ["working", "Working"], ["resting", "Resting"]]

var residents: Array = []
var island_name := ""
var _filter := "all"


func _ready() -> void:
	# Hidden screens skip rebuilding (events can come several times a second);
	# catch up when shown.
	visibility_changed.connect(
		func():
			if visible:
				_rebuild()
	)
	%NewButton.pressed.connect(new_resident_requested.emit)
	%InviteButton.pressed.connect(new_resident_requested.emit)
	_rebuild()


## Show the residents from a protocol snapshot (Net.snapshot_received).
func show_snapshot(island: Dictionary) -> void:
	island_name = str(island.get("name", island_name))
	# Attach each waiting bubble to its resident, so the card can show it.
	var bubbles := {}
	for bubble in island.get("bubbles", []):
		bubbles[bubble["agent"]] = bubble
	residents = []
	for resident in island.get("residents", []):
		var copy: Dictionary = resident.duplicate()
		if bubbles.has(resident["id"]):
			copy["bubble"] = bubbles[resident["id"]]
		var friend := ResidentData.best_friend(str(resident["id"]), island.get("friendships", []))
		if friend:
			friend["name"] = str(Net.find_resident(friend["id"]).get("name", friend["id"]))
			copy["friend"] = friend
		residents.append(copy)
	_rebuild()


## Add a resident made in the creator (or replace one with the same id).
func add_or_update(resident: Dictionary) -> void:
	for i in residents.size():
		if residents[i].get("id") == resident.get("id"):
			residents[i] = residents[i].merged(resident, true)
			_rebuild()
			return
	residents.append(resident)
	_rebuild()


func _rebuild() -> void:
	if not is_node_ready() or not is_visible_in_tree():
		return
	%Subtitle.text = "%d little lives on %s" % [residents.size(), island_name]

	# Remove the old cards (but keep the Invite tile, which always goes last).
	for child in %Grid.get_children():
		if child is ResidentCard:
			child.queue_free()
	for resident in residents:
		var card: ResidentCard = CardScene.instantiate()
		%Grid.add_child(card)
		card.setup(resident)
		card.visible = _filter == "all" or ResidentData.filter_group(resident) == _filter
		card.edit_pressed.connect(edit_requested.emit)
		card.action_pressed.connect(action_requested.emit)
	%Grid.move_child(%InviteButton, -1)
	_build_filters()


func _build_filters() -> void:
	var needs := residents.filter(func(r): return ResidentData.filter_group(r) == "needs").size()
	for child in %Filters.get_children():
		child.queue_free()
	for f in FILTERS:
		var chip := Button.new()
		chip.text = f[1] % needs if "%d" in f[1] else f[1]
		chip.theme_type_variation = &"FilterChipActive" if f[0] == _filter else &"FilterChip"
		chip.pressed.connect(
			func():
				_filter = f[0]
				_rebuild()
		)
		%Filters.add_child(chip)
