extends Control
## The main screen: top bar, the 3D island in a rounded frame, the bottom nav,
## and the Bubbles sidebar.
##
## Layout lives in game.tscn (containers, so you can rearrange it in the
## editor) and every color/style lives in ui/theme.tres. This script wires up
## building labels, zoom, the connection pill, screen switching (island,
## Residents, the resident creator), and the bits of the top bar and sidebar
## that come from Net.island. The 3D residents live in island_3d.tscn
## (scenes/island/island_residents.gd); the bubble cards in bubbles_sidebar.gd.
##
## Godot concept: the island is rendered inside a SubViewport, a "screen inside
## the screen". That's what lets the 3D world sit in a rounded frame next to
## normal 2D UI instead of filling the whole window.
##
## TODO(mvp): Build mode, News / Insights screens, and the island picker.
## TODO(mvp): real day/time and weather from the backend.

const BuildingLabelScene := preload("res://scenes/ui/building_label.tscn")

## Building id (backend), default label, icon, color. The node in island_3d.tscn
## is the id in PascalCase ("post_office" -> PostOffice).
const BUILDINGS := [
	["mailbox", "Mailbox", preload("res://assets/icons/mail.svg"), Color("F5708F")],
	["workshop", "Workshop", preload("res://assets/icons/code.svg"), Color("E39537")],
	["lab", "Lab", preload("res://assets/icons/flask-conical.svg"), Color("00B8A0")],
	["post_office", "Post Office", preload("res://assets/icons/git-pull-request.svg"), Color("D9A321")],
	["courthouse", "Courthouse", preload("res://assets/icons/scale.svg"), Color("7553DF")],
]

@onready var island: Node3D = %Island3D
@onready var rig: CameraRig = %Island3D.get_node("CameraRig")
@onready var labels_root: Control = %Labels
@onready var residents_screen: ResidentsScreen = %ResidentsScreen
@onready var creator: ResidentCreator = %ResidentCreator

## Which screen is showing: "island", "residents", or "creator".
var screen := "island"
## True while the creator waits for the server to accept a resident.
var _saving_resident := false
var _labels := {}  # building id -> BuildingLabel


func _ready() -> void:
	for info in BUILDINGS:
		var label: BuildingLabel = BuildingLabelScene.instantiate()
		labels_root.add_child(label)
		label.setup(island.get_node("Buildings/" + info[0].to_pascal_case()), rig.camera, info[1], info[2], info[3], 0)
		_labels[info[0]] = label

	%ZoomIn.pressed.connect(func(): rig.zoom_by(CameraRig.ZOOM_STEP))
	%ZoomOut.pressed.connect(func(): rig.zoom_by(-CameraRig.ZOOM_STEP))
	rig.zoom_changed.connect(func(percent: int): %ZoomLabel.text = "%d%%" % percent)

	# The connection pill is real: it follows the Net autoload.
	Net.connected.connect(_show_connection.bind(true))
	Net.disconnected.connect(_show_connection.bind(false))
	_show_connection(Net.is_online)

	# Screens. They all live in game.tscn; show_screen() hides all but one.
	%IslandNav.pressed.connect(show_screen.bind("island"))
	%Residents.pressed.connect(func(): show_screen("island" if screen == "residents" else "residents"))
	%BackButton.pressed.connect(func(): show_screen("residents" if screen == "creator" else "island"))
	residents_screen.new_resident_requested.connect(_open_creator)
	residents_screen.edit_requested.connect(_open_creator)
	residents_screen.action_requested.connect(_on_card_action)
	creator.cancelled.connect(show_screen.bind("residents"))
	creator.saved.connect(_save_resident)

	# Island data from the backend. Net keeps Net.island current and announces
	# every change; until the first snapshot the Residents screen shows mock data.
	Net.island_changed.connect(_show_island)
	Net.resident_updated.connect(_on_resident_updated)
	Net.server_error.connect(_on_server_error)
	_show_island()
	show_screen("island")


## Show one screen and hide the others. The top bar and nav always stay; the
## Bubbles sidebar hides for the creator, which needs the whole width.
func show_screen(which: String) -> void:
	screen = which
	%IslandFrame.visible = which == "island"
	residents_screen.visible = which == "residents"
	creator.visible = which == "creator"
	%Sidebar.visible = which != "creator"
	# Highlight the nav button of the open screen (the creator belongs to Residents).
	%IslandNav.theme_type_variation = &"NavButtonActive" if which == "island" else &"NavButton"
	%Residents.theme_type_variation = &"NavButtonActive" if which in ["residents", "creator"] else &"NavButton"


## The creator's "Move in" / "Save". Online, the backend creates the resident and
## answers with a `resident` event (or an `error`). Offline, it's only added here,
## so the screens still work without a backend.
func _save_resident(resident: Dictionary) -> void:
	if Net.send_resident(resident):
		_saving_resident = true
		return
	if not resident.has("id"):
		resident["id"] = "local_%d" % Time.get_ticks_msec()
		resident["building"] = ResidentData.JOB_HOMES[resident["job"]].to_snake_case()
	_on_resident_updated(resident)
	show_screen("residents")


func _on_resident_updated(resident: Dictionary) -> void:
	if not Net.is_online:
		residents_screen.add_or_update(resident)  # offline: Net.island doesn't have it
	if _saving_resident:
		_saving_resident = false
		show_screen("residents")


func _on_server_error(message: String) -> void:
	if _saving_resident:
		_saving_resident = false
		creator.show_error(message)


## A card's Answer / Review / Tuck in opens that bubble's reply in the sidebar;
## Visit goes to the island.
func _on_card_action(resident: Dictionary) -> void:
	var bubble = resident.get("bubble")
	if bubble and bubble.has("id"):
		%Sidebar.open_reply(str(bubble["id"]))
	else:
		show_screen("island")


## Redraw everything that comes from Net.island (after any backend event).
func _show_island() -> void:
	var data := Net.island
	if data.is_empty():
		return  # no snapshot yet: keep the placeholders
	%IslandName.text = str(data.get("name", ""))
	%Repo.text = str(data["repo"]) if data.get("repo") else "no repo linked"
	residents_screen.show_snapshot(data)
	_show_resident_summary(data.get("residents", []))

	# Station badges: how many residents are at each building right now, 0 included
	# (walking residents count for nowhere until they arrive).
	for id in _labels:
		var here := 0
		for r in data.get("residents", []):
			if r.get("building") == id and r.get("state") != "walking":
				here += 1
		var building := Net.find_building(id)
		_labels[id].show_count(here, str(building.get("name", "")))


## "5 residents" on the island view, and "4 happy · 1 tired" in the sidebar.
func _show_resident_summary(residents: Array) -> void:
	%ResidentCount.text = "%d resident%s" % [residents.size(), "" if residents.size() == 1 else "s"]
	var moods := {}
	for r in residents:
		var mood := "sleepy" if r.get("state") == "sleeping" else str(r.get("mood", "happy"))
		moods[mood] = moods.get(mood, 0) + 1
	var common: Array = moods.keys()
	common.sort_custom(func(a, b): return moods[a] > moods[b])
	var parts := []
	for mood in common.slice(0, 2):
		parts.append("%d %s" % [moods[mood], mood])
	%MoodSummary.text = " · ".join(parts) if parts else "nobody home"


func _open_creator(resident: Dictionary = {}) -> void:
	creator.open(resident, residents_screen.island_name)
	show_screen("creator")


func _show_connection(online: bool) -> void:
	%StatusLabel.text = "Connected" if online else "Reconnecting…"
	%SyncedLabel.text = "synced just now" if online else "backend offline"
	var dot: StyleBoxFlat = get_theme_stylebox("panel", "StatusDot").duplicate()
	dot.bg_color = Color("39C16F") if online else Color("F2994A")
	%StatusDot.add_theme_stylebox_override("panel", dot)
