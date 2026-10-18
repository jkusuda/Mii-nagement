class_name Game
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
## The day pill follows the Clock autoload (real time, the
## island's founding day); 3D lighting follows it too (scenes/island/day_night.gd).
##
## Click a building (or its name pill) and the camera glides to it while a
## BuildingPanel opens: the Mailbox lists open issues and sends new ones ("Send
## a package" in the top bar opens it too), the other buildings show what's at
## that stage. The Archipelago's Settings button opens SettingsPopup.
##
## The Build button opens build mode (scenes/build/build_mode.gd).

const BuildingLabelScene := preload("res://scenes/ui/building_label.tscn")

const SUN_ICON := preload("res://assets/icons/sun.svg")
const MOON_ICON := preload("res://assets/icons/moon.svg")
const NIGHT_COLOR := Color("8E9BF0")

## Opening a building: zoom to at least this much, with the building this far
## left of the middle (a fraction of the view's width) so it peeks out beside the panel.
const FOCUS_ZOOM := 1.35
const FOCUS_SHIFT := -0.36

var dropdown := IslandDropdown.new()
var building_panel := BuildingPanel.new()
var talk_popup := TalkPopup.new()  # chat with a resident, opened from the building panel
var picker: BuildingPicker
var settings_popup := SettingsPopup.new()
var build_mode := BuildMode.new()
var toast := Toast.new()  # server errors no screen handled itself

## Which screen is showing: "home" (the archipelago), "island_creator",
## "island", "residents", "creator", "insights", or "news".
var screen := "island"
## True while the creator waits for the server to accept a resident.
var _saving_resident := false
## The resident the creator asked to move away, until the server answers.
var _removing_resident := ""
## The resident creator is making a draft for the island creator (not sent yet),
## and which of its residents it replaces (-1 = a new one).
var _drafting := false
var _draft_index := -1
## Residents to move in once the newly founded island's snapshot arrives.
var _move_in_island := ""
var _move_in: Array = []
var _labels := {}  # building type -> BuildingLabel
var _layout_key := ""  # what the 3D island was last built from
var _dev_pill: Control  # the debug-only time slider (hidden while building)

@onready var island: Node3D = %Island3D
@onready var island_builder: IslandBuilder = %Island3D.get_node("Island")
@onready var rig: CameraRig = %Island3D.get_node("CameraRig")
@onready var labels_root: Control = %Labels
@onready var residents_screen: ResidentsScreen = %ResidentsScreen
@onready var creator: ResidentCreator = %ResidentCreator
@onready var insights: InsightsScreen = %InsightsScreen
@onready var news: NewsScreen = %NewsScreen
@onready var home: Home = %Home
@onready var island_creator: IslandCreator = %IslandCreator


func _ready() -> void:
	for type in BuildingData.TYPES:
		var info: Dictionary = BuildingData.TYPES[type]
		var label: BuildingLabel = BuildingLabelScene.instantiate()
		labels_root.add_child(label)
		label.setup(island_builder.building_node(type), rig.camera, info["name"], info["icon"], info["color"], 0)
		label.pressed.connect(open_building.bind(type))
		_labels[type] = label

	# Clicking buildings on the island (see building_picker.gd and camera_rig.gd).
	picker = BuildingPicker.new(island_builder.buildings, rig.camera)
	rig.clicked.connect(
		func(at: Vector2):
			if build_mode.is_open:
				return  # build mode handles its own clicks
			var type := BuildingData.type_of_node(picker.building_at(at))
			if type:
				open_building(type)
	)
	rig.hovered.connect(
		func(at: Vector2):
			if build_mode.is_open:
				return
			var container := rig.get_viewport().get_parent() as Control  # the SubViewportContainer
			container.mouse_default_cursor_shape = (
				Control.CURSOR_POINTING_HAND if picker.building_at(at) else Control.CURSOR_ARROW
			)
	)

	%ZoomIn.pressed.connect(func(): rig.zoom_by(CameraRig.ZOOM_STEP))
	%ZoomOut.pressed.connect(func(): rig.zoom_by(-CameraRig.ZOOM_STEP))
	rig.zoom_changed.connect(func(percent: int): %ZoomLabel.text = "%d%%" % percent)

	# The connection pill follows the Net autoload.
	Net.connected.connect(_show_connection.bind(true))
	Net.disconnected.connect(_show_connection.bind(false))
	Launcher.status_changed.connect(func(): _show_connection(Net.is_online))  # the app's own backend
	_show_connection(Net.is_online)

	# Screens. They all live in game.tscn; show_screen() hides all but one.
	%IslandNav.pressed.connect(_open_build_mode)  # the Build button
	%Residents.pressed.connect(func(): show_screen("island" if screen == "residents" else "residents"))
	%Insights.pressed.connect(func(): show_screen("island" if screen == "insights" else "insights"))
	insights.see_residents.connect(show_screen.bind("residents"))
	%News.pressed.connect(func(): show_screen("island" if screen == "news" else "news"))
	news.unread_changed.connect(_show_unread)
	news.notice.connect(toast.show_message)
	news.retry_requested.connect(_retry_package)
	_show_unread(news.unread())
	%BackButton.pressed.connect(_go_back)

	# The archipelago and the island switcher.
	home.visit.connect(_visit_island)
	home.new_island_requested.connect(_open_island_creator)
	home.settings_requested.connect(settings_popup.open)
	add_child(settings_popup)
	island_creator.closed.connect(show_screen.bind("home"))
	island_creator.resident_requested.connect(
		func(resident: Dictionary, index: int):
			_drafting = true
			_draft_index = index
			creator.open(resident, island_creator.island_name(), false)
			show_screen("creator")
	)
	island_creator.founded.connect(_on_island_founded)
	Net.snapshot_received.connect(_move_residents_in)
	add_child(dropdown)
	dropdown.visit.connect(_visit_island)
	dropdown.go_home.connect(show_screen.bind("home"))
	dropdown.new_island.connect(_open_island_creator)
	%SendPackage.pressed.connect(open_building.bind("mailbox", true))
	%IslandSelector.gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				dropdown.open_below(%IslandSelector)
	)
	residents_screen.new_resident_requested.connect(_open_creator)
	residents_screen.edit_requested.connect(_open_creator)
	residents_screen.action_requested.connect(_on_card_action)
	creator.cancelled.connect(func(): show_screen("island_creator" if _drafting else "residents"))
	creator.saved.connect(_save_resident)
	creator.move_away_requested.connect(
		func(id: String):
			if Net.send_delete_resident(id):
				_removing_resident = id
	)
	Net.resident_removed.connect(
		func(id: String):
			if id == _removing_resident:
				_removing_resident = ""
				show_screen("residents")
	)
	dropdown.abandoned.connect(_on_island_abandoned)
	dropdown.failed.connect(toast.show_message)

	# Island data from the backend. Net keeps Net.island current and announces
	# every change; until the first snapshot the screens stay empty.
	Net.island_changed.connect(_show_island)
	Net.resident_updated.connect(_on_resident_updated)
	Net.server_error.connect(_on_server_error)
	_show_island()
	show_screen("home")

	# Added last, so it's drawn over everything else.
	add_child(building_panel)
	building_panel.closed.connect(rig.unfocus)
	building_panel.answer_requested.connect(%Sidebar.open_reply)
	%Sidebar.building_requested.connect(open_building)
	Net.snapshot_received.connect(func(_island): building_panel.close())  # another island
	# Over the building panel, which stays open underneath: closing the chat goes back to it.
	add_child(talk_popup)
	building_panel.talk_requested.connect(talk_popup.open)
	Net.snapshot_received.connect(func(_island): talk_popup.close())

	add_child(toast)

	Clock.ticked.connect(_show_time)
	_show_time()
	_add_build_mode()
	if OS.is_debug_build():
		_add_dev_time_slider()


## Show one screen and hide the others. The top bar and nav always stay; the
## Bubbles sidebar hides for the creator, which needs the whole width.
func show_screen(which: String) -> void:
	if which != "island":
		build_mode.cancel()  # leaving the island throws away unsaved edits
	screen = which
	if which != "creator":
		_drafting = false  # a drafted resident was saved or cancelled
	if which != "island":
		building_panel.close()
		talk_popup.close()
	home.visible = which == "home"
	island_creator.visible = which == "island_creator"
	%Margin.visible = which not in ["home", "island_creator"]
	%IslandFrame.visible = which == "island"
	residents_screen.visible = which == "residents"
	creator.visible = which == "creator"
	insights.visible = which == "insights"
	news.visible = which == "news"
	%Sidebar.visible = which != "creator"
	# A resident drafted for an island that doesn't exist yet: no island to switch or send to.
	%IslandSelector.visible = not _drafting
	%SendPackage.visible = not _drafting
	# Highlight the nav button of the open screen (the creator belongs to Residents).
	_style_build_button()
	%Residents.theme_type_variation = &"NavButtonActive" if which in ["residents", "creator"] else &"NavButton"
	%Insights.theme_type_variation = &"NavButtonActive" if which == "insights" else &"NavButton"
	%News.theme_type_variation = &"NavButtonActive" if which == "news" else &"NavButton"
	Audio.play_place(_music_place())


## Which song plays here (Audio): the archipelago's, the creator's, build mode's, or the island's.
func _music_place() -> String:
	if screen in ["home", "island_creator"]:
		return "home"
	if screen == "creator":
		return "creator"
	return "build" if build_mode.is_open else "island"


## The creator's "Move in" / "Save". Online, the backend creates the resident and
## answers with a `resident` event (or an `error`). Offline, it's only added here,
## so the screens still work without a backend.
func _save_resident(resident: Dictionary) -> void:
	if _drafting:  # for the island creator: hand it back, nothing is sent yet
		_drafting = false
		island_creator.put_resident(resident, _draft_index)
		show_screen("island_creator")
		return
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
	elif _removing_resident:
		_removing_resident = ""
		creator.show_error(message)
	else:
		toast.show_message(message)


## An island was abandoned from the switcher. If we were on it, go to another
## island (or to none, when it was the last) and show the Archipelago.
func _on_island_abandoned(id: String, repo_path: String) -> void:
	var island_name := str(Islands.find(id).get("name", id))
	toast.show_message(
		(
			"%s was abandoned. Its git repository stays on disk (…/%s)." % [island_name, repo_path.get_file()]
			if repo_path
			else "%s was abandoned." % island_name
		)
	)
	if id != str(Net.island.get("id", "")):
		return
	var next := ""
	for island in Islands.islands:
		if str(island["id"]) != id:
			next = str(island["id"])
			break
	Net.visit_island(next)  # "" = no island: the backend keeps the connection open without one
	show_screen("home")


## A card's Answer / Review / Tuck in opens that bubble's reply in the sidebar;
## Visit goes to the island.
func _on_card_action(resident: Dictionary) -> void:
	var bubble = resident.get("bubble")
	if bubble and bubble.has("id"):
		%Sidebar.open_reply(str(bubble["id"]))
		return
	# Visit: back to the island, the camera zoomed all the way in on them.
	show_screen("island")
	var walker: IslandResident = %Island3D.get_node("Residents").walker(str(resident.get("id", "")))
	if walker:
		rig.focus_on(walker.global_position, CameraRig.MAX_ZOOM)


## Redraw everything that comes from Net.island (after any backend event).
func _show_island() -> void:
	var data := Net.island
	if data.is_empty():
		return  # no snapshot yet: keep the placeholders
	%IslandName.text = str(data.get("name", ""))
	_show_time()
	%Repo.text = str(data["repo"]) if data.get("repo") else "no repo linked"
	# Long names get "…" (the top bar has a fixed width); the tooltip has them in full.
	%IslandSelector.tooltip_text = "%s\n%s\nSwitch island" % [%IslandName.text, %Repo.text]
	residents_screen.show_snapshot(data)
	_show_resident_summary(data.get("residents", []))

	# Rebuild the 3D island when its layout or buildings changed (build mode, or
	# another island). Other events (moods, tasks...) leave it alone.
	var key := JSON.stringify(
		[
			data.get("id"),
			data.get("map"),
			data.get("buildings", []).map(func(b): return [b["type"], b.get("x"), b.get("y"), b.get("facing")])
		]
	)
	if key != _layout_key:
		_layout_key = key
		island_builder.build(IslandLayout.from_snapshot(data))

	# Each label shows how many residents are at its building right now, 0
	# included (walking residents count once they arrive). Labels hide by
	# themselves with buildings the island doesn't have.
	var by_type := {}
	for b in data.get("buildings", []):
		by_type[str(b["type"])] = b
	for type in _labels:
		var building: Dictionary = by_type.get(type, {})
		var here := 0
		for r in data.get("residents", []):
			if ResidentData.inside(r, str(building.get("id", ""))):  # working inside, not idling nearby
				here += 1
		_labels[type].show_count(here, str(building.get("name", "")))
		_labels[type].show_working(here > 0)


## "5 residents" on the island view, and "4 happy · 1 tired" in the sidebar.
func _show_resident_summary(residents: Array) -> void:
	%ResidentCount.text = "%d resident%s" % [residents.size(), "" if residents.size() == 1 else "s"]
	%MoodSummary.text = ResidentData.mood_summary(residents)


## The back button: creator -> Residents, island -> archipelago, others -> island.
func _go_back() -> void:
	match screen:
		"creator":
			show_screen("island_creator" if _drafting else "residents")
		"island":
			show_screen("home")
		_:
			show_screen("island")


## News' "Try again" on a failed package: its issue, back in the Mailbox's form.
func _retry_package(task_id: String) -> void:
	if Net.island.is_empty():
		return
	Net.request_json(
		"/islands/%s/tasks/%s" % [str(Net.island["id"]).uri_encode(), task_id.uri_encode()],
		func(ok: bool, data: Dictionary):
			if not ok:
				toast.show_message("Couldn't open that package: %s" % data["error"])
				return
			open_building("mailbox", true)
			building_panel.fill_form(str(data.get("title", "")), str(data.get("description", "")), data.get("issue"))
	)


## Open a building's panel ("mailbox", "lab", ...) and glide the camera to it.
## `compose`: open the Mailbox on its "Send a new issue" form.
func open_building(type: String, compose := false) -> void:
	if Net.island.is_empty():
		return
	if screen != "island":
		show_screen("island")
	if build_mode.is_open:
		build_mode.cancel()  # a building's panel and build mode don't mix
	rig.focus_on(island_builder.building_node(type).global_position, maxf(rig.zoom, FOCUS_ZOOM), FOCUS_SHIFT)
	building_panel.open(type, compose)


func _open_island_creator() -> void:
	island_creator.open()
	show_screen("island_creator")


## A new island exists: go there, and move its residents in when it's ready.
func _on_island_founded(island: Dictionary, residents: Array) -> void:
	Audio.sfx("island_created")
	_move_in_island = str(island["id"])
	_move_in = residents
	Islands.refresh()
	_visit_island(_move_in_island)


## Send the founded island's residents, once we're connected to it (the server
## creates residents on the island this connection watches).
func _move_residents_in(island: Dictionary) -> void:
	if island.get("id") != _move_in_island:
		return
	for resident in _move_in:
		Net.send_resident(resident)
	_move_in = []
	_move_in_island = ""


## Go to an island (from the archipelago or the switcher). Net reconnects to it;
## every screen redraws when its snapshot arrives.
func _visit_island(id: String) -> void:
	Net.visit_island(id)
	show_screen("island")


## The News button's badge: stories you haven't seen yet.
func _show_unread(count: int) -> void:
	%NewsBadge.visible = count > 0
	%NewsCount.text = str(count) if count < 10 else "9+"


func _open_creator(resident: Dictionary = {}) -> void:
	creator.open(resident, residents_screen.island_name)
	show_screen("creator")


## The connection pill only shows when the backend drops: "Reconnecting… backend offline".
func _show_connection(online: bool) -> void:
	%ConnectedPill.visible = not online
	%StatusLabel.text = "Reconnecting…"
	%SyncedLabel.text = Launcher.status if Launcher.status != "" else "backend offline"
	%SendPackage.disabled = not online
	var dot: StyleBoxFlat = get_theme_stylebox("panel", "StatusDot").duplicate()
	dot.bg_color = Color("F2994A")
	%StatusDot.add_theme_stylebox_override("panel", dot)


## The top bar's "Day 12 · 10:42".
func _show_time() -> void:
	var night := Clock.is_night()
	var time := Clock.time_text()
	if Net.island.is_empty():
		%Day.text = time
	else:
		%Day.text = "Day %d · %s" % [Clock.day_number(str(Net.island.get("created_at", ""))), time]
	%DayIcon.texture = MOON_ICON if night else SUN_ICON
	%DayIcon.modulate = NIGHT_COLOR if night else Color("FDC902")


## Build mode (scenes/build/build_mode.gd): its node goes in the 3D world, after
## the camera rig so it sees clicks first; the Build button opens it.
func _add_build_mode() -> void:
	island.add_child(build_mode)
	build_mode.setup(island_builder, rig, %IslandFrame)
	build_mode.opened.connect(_on_build_mode_toggled.bind(true))
	build_mode.closed.connect(_on_build_mode_toggled.bind(false))


## The Build button (bottom nav): go to the island and start building there.
func _open_build_mode() -> void:
	if screen != "island":
		show_screen("island")
	build_mode.open()


## While building, the building name pills hide, for a cleaner view.
func _on_build_mode_toggled(building: bool) -> void:
	Audio.play_place(_music_place())
	if building:
		building_panel.close()
		talk_popup.close()
	%Labels.visible = not building
	if _dev_pill:
		_dev_pill.visible = not building
	_style_build_button()


func _style_build_button() -> void:
	%IslandNav.theme_type_variation = &"NavButtonActive" if build_mode.is_open else &"NavButton"


## Development only (debug builds): a small slider above the zoom pill to
## scrub the time of day. "Live" goes back to the real clock.
## Godot concept: nodes can be built in code, not just in .tscn files. This
## one is throwaway, so it lives here instead of in game.tscn.
func _add_dev_time_slider() -> void:
	var pill := PanelContainer.new()
	_dev_pill = pill
	pill.theme_type_variation = &"PillWhite"
	%IslandFrame.add_child(pill)
	# Pin it to the bottom-right corner, above the zoom pill, growing up and left.
	pill.set_anchors_preset(Control.PRESET_BOTTOM_RIGHT)
	pill.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	pill.grow_vertical = Control.GROW_DIRECTION_BEGIN
	pill.offset_right = -14
	pill.offset_bottom = -58

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	pill.add_child(row)
	var label := Label.new()
	label.theme_type_variation = &"LabelMuted"
	label.text = "Dev time"
	row.add_child(label)
	var slider := HSlider.new()
	slider.custom_minimum_size = Vector2(110, 0)
	slider.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	slider.min_value = 0.0
	slider.max_value = 23.99
	slider.step = 0.05
	slider.value = Clock.hour()
	row.add_child(slider)
	var live := Button.new()
	live.theme_type_variation = &"FlatIconButton"
	live.text = "Live"
	row.add_child(live)

	slider.value_changed.connect(func(value: float): Clock.dev_hour = value)
	live.pressed.connect(
		func():
			Clock.dev_hour = -1.0
			slider.set_value_no_signal(Clock.hour())
	)
