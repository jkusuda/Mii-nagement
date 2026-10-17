extends Node
## QA walkthrough of the whole game against a test backend (MII_SERVER).
## Prints PASS / FAIL / NOTE lines and saves a screenshot per step.

var out := ""
var game
var n := 0
var fails := 0


func _ready() -> void:
	out = OS.get_cmdline_user_args()[0]
	game = load("res://scenes/game.tscn").instantiate()
	add_child(game)
	await wait(3.0)
	await step_home()
	await step_settings()
	await step_island_creator()
	await step_island_view()
	await step_buildings()
	await step_task_flow()
	await step_residents()
	await step_build_mode()
	await step_news_insights()
	await step_night()
	await step_switcher_and_abandon()
	print("QA DONE: %d failures" % fails)
	get_tree().quit()


# --- helpers ------------------------------------------------------------------

func wait(t: float) -> void:
	await get_tree().create_timer(t).timeout


func shot(name: String) -> void:
	n += 1
	get_viewport().get_texture().get_image().save_png("%s/%02d_%s.png" % [out, n, name])


func check(what: String, ok: bool, detail := "") -> void:
	if not ok:
		fails += 1
	print("%s  %s %s" % ["PASS" if ok else "FAIL", what, ("(" + detail + ")") if detail else ""])


func note(text: String) -> void:
	print("NOTE  " + text)


## Every visible Button whose text is `text`, anywhere in the game.
func buttons_with_text(text: String) -> Array:
	var found := []
	for b in get_tree().root.find_children("*", "Button", true, false):
		if b.text == text and b.is_visible_in_tree():
			found.append(b)
	return found


func press(text: String) -> bool:
	var found := buttons_with_text(text)
	if found.is_empty():
		return false
	found[-1].pressed.emit()
	return true


func resident_named(name: String) -> Dictionary:
	for r in Net.island.get("residents", []):
		if r.get("name") == name:
			return r
	return {}


func wait_for(cond: Callable, timeout: float) -> bool:
	var t := 0.0
	while t < timeout:
		if cond.call():
			return true
		await wait(0.25)
		t += 0.25
	return false


# --- steps --------------------------------------------------------------------

func step_home() -> void:
	print("== Home")
	check("connected to test backend", Net.is_online)
	check("islands listed", Islands._order.size() >= 1, str(Islands._order))
	shot("home")
	game.dropdown.open_below(game.get_node("%IslandSelector"))
	await wait(0.4)
	shot("island_dropdown")
	game.dropdown.hide()


func step_settings() -> void:
	print("== Settings")
	game.settings_popup.open()
	await wait(1.0)
	check("settings popup opens", game.settings_popup.visible)
	shot("settings")
	game.settings_popup.hide()


func step_island_creator() -> void:
	print("== Island creator")
	game._open_island_creator()
	await wait(0.6)
	var ic = game.island_creator
	check("island creator visible", ic.visible)
	shot("island_creator_1")
	ic.get_node("%NameEdit").text = "QA Isle"
	ic.get_node("%NameEdit").text_changed.emit("QA Isle")
	ic.get_node("%Next").pressed.emit()
	await wait(0.5)
	shot("island_creator_2")
	# Draft a resident through the resident creator (drafting path).
	ic.resident_requested.emit({}, -1)
	await wait(0.6)
	var c = game.creator
	check("creator opens for a draft resident", c.visible)
	c.get_node("%NameEdit").text = "Draftee"
	c.get_node("%NameEdit").text_changed.emit("Draftee")
	c._show_page(true)
	c._part_tiles["eyes"][12].button_pressed = true
	c._part_tiles["hair"][5].button_pressed = true
	var draft_look: Dictionary = c._data["appearance"].duplicate()
	shot("draft_creator_look")
	c.get_node("%MoveInButton").pressed.emit()
	await wait(0.5)
	check("back on island creator after drafting", ic.visible)
	check("draft resident kept", ic._residents.size() == 1 and ic._residents[0].get("name") == "Draftee")
	shot("island_creator_with_draft")
	ic.get_node("%Next").pressed.emit()  # Found
	var founded := await wait_for(func(): return Net.island.get("name") == "QA Isle", 15.0)
	check("island founded and visited", founded, str(Net.island.get("name")))
	var moved_in := await wait_for(func(): return not resident_named("Draftee").is_empty(), 10.0)
	check("drafted resident moved in", moved_in)
	if moved_in:
		var got: Dictionary = resident_named("Draftee").get("appearance", {})
		var same := true
		for k in draft_look:
			if str(got.get(k)) != str(draft_look[k]) and float(str(got.get(k, -99))) != float(str(draft_look[k])):
				same = false
		check("drafted resident keeps the look picked in the creator", same, "%s vs %s" % [got, draft_look])
	game.show_screen("island")
	await wait(1.5)
	shot("qa_isle")


func step_island_view() -> void:
	print("== Island view (sunny)")
	game._visit_island("sunny")
	game.show_screen("island")
	await wait_for(func(): return Net.island.get("id") == "sunny", 8.0)
	await wait(1.5)
	check("on sunny", Net.island.get("id") == "sunny")
	var walkers: Dictionary = game.find_child("Residents", true, false)._walkers if game.find_child("Residents", true, false) and "_walkers" in game.find_child("Residents", true, false) else {}
	note("residents in snapshot: %d" % Net.island.get("residents", []).size())
	shot("island_view")
	game.rig.zoom_by(CameraRig.ZOOM_STEP * 3)
	await wait(0.6)
	shot("island_zoomed")
	game.rig.zoom_by(-CameraRig.ZOOM_STEP * 3)


func step_buildings() -> void:
	print("== Building panels")
	for type in ["mailbox", "workshop", "lab", "courthouse", "post_office"]:
		game.open_building(type)
		await wait(0.6)
		check("panel opens: " + type, game.building_panel.visible)
		shot("panel_" + type)
		game.building_panel.close()
		await wait(0.3)


func step_task_flow() -> void:
	print("== Task flow (mock)")
	game.open_building("mailbox", true)
	await wait(0.6)
	shot("package_form")
	var form = game.building_panel._form
	form._title.text = "QA: fix the login typo"
	form._details.text = "Testing the whole road."
	var before: int = Net.island.get("tasks", []).size()
	form.submit()
	var made := await wait_for(func(): return Net.island.get("tasks", []).size() > before, 8.0)
	check("task created from the package form", made)
	game.building_panel.close()
	await wait(0.4)
	# Watch it travel; answer any bubble that shows up.
	var answered := 0
	var t := 0.0
	var last_status := ""
	while t < 60.0:
		var task: Dictionary = {}
		for x in Net.island.get("tasks", []):
			if x.get("title") == "QA: fix the login typo":
				task = x
		var status := str(task.get("status", ""))
		if status != last_status:
			note("task status: %s at %s (t=%.1f)" % [status, task.get("building"), t])
			last_status = status
		if status in ["done", "failed", "cancelled"]:
			break
		for b in Net.island.get("bubbles", []):
			if answered < 4:
				game.get_node("%Sidebar").open_reply(str(b["id"]))
				await wait(0.5)
				if answered == 0:
					shot("answer_popup")
				Net.send_answer(str(b["id"]), "yes, go ahead")
				answered += 1
				note("answered bubble kind=%s" % b.get("kind"))
				await wait(0.5)
		if int(t) == 6:
			shot("task_travelling")
		await wait(0.5)
		t += 0.5
	check("task finished (done)", last_status == "done", last_status)
	shot("after_task")
	# A second task, taken back while it's running.
	game.open_building("mailbox", true)
	await wait(0.4)
	form._title.text = "QA: take me back"
	form.submit()
	await wait(2.0)
	game.building_panel.close()
	var tb := {}
	for x in Net.island.get("tasks", []):
		if x.get("title") == "QA: take me back":
			tb = x
	if tb:
		Net.send_cancel_task(str(tb["id"]))
		var gone := await wait_for(func():
			for x in Net.island.get("tasks", []):
				if x.get("id") == tb["id"]:
					return x.get("status") == "cancelled"
			return false, 8.0)
		check("take back (cancel) a running task", gone)
	else:
		check("second task created", false)


func step_residents() -> void:
	print("== Residents")
	game.show_screen("residents")
	await wait(1.5)
	shot("residents")
	var cards: Array = game.residents_screen.find_children("*", "ResidentCard", true, false)
	check("a card per resident", cards.size() == Net.island.get("residents", []).size(), "%d cards" % cards.size())
	var blank := 0
	for card in cards:
		var tex: Texture2D = card.get_node("%Portrait").texture
		if tex == null or tex.get_image() == null or tex.get_image().is_invisible():
			blank += 1
	check("every card has a headshot", blank == 0, "%d blank" % blank)
	# Create
	game._open_creator({})
	await wait(0.5)
	var c = game.creator
	c.get_node("%NameEdit").text = "Qwen"
	c.get_node("%NameEdit").text_changed.emit("Qwen")
	c._show_page(true)
	c._part_tiles["mouth"][9].button_pressed = true
	c.get_node("%MoveInButton").pressed.emit()
	var created := await wait_for(func(): return not resident_named("Qwen").is_empty(), 6.0)
	check("create resident", created)
	check("returns to Residents after Move in", game.residents_screen.visible)
	# Edit look + name + job
	var q := resident_named("Qwen")
	game._open_creator(q)
	await wait(0.5)
	check("edit title", c.get_node("%Title").text == "Edit Qwen", c.get_node("%Title").text)
	c._show_page(true)
	c._part_tiles["hair"][30].button_pressed = true
	c._show_page(false)
	c.get_node("%NameEdit").text = "Qwenny"
	c.get_node("%NameEdit").text_changed.emit("Qwenny")
	shot("creator_edit_about")
	c.get_node("%MoveInButton").pressed.emit()
	var edited := await wait_for(func(): return not resident_named("Qwenny").is_empty(), 6.0)
	check("edit name saved", edited)
	check("edit look saved", int(resident_named("Qwenny").get("appearance", {}).get("hair", -9)) == 29)
	await wait(1.0)
	shot("residents_after_edit")
	# Works at picker
	game._open_creator(resident_named("Qwenny"))
	await wait(0.5)
	check("works-at picker shown", c._works_at_box.visible)
	shot("creator_works_at")
	c.get_node("%CancelButton").pressed.emit()
	await wait(0.3)
	# Move away
	game._open_creator(resident_named("Qwenny"))
	await wait(0.5)
	check("move away button shown", c._move_away.visible)
	c._move_away.pressed.emit()
	await wait(0.4)
	shot("move_away_confirm")
	var pressed := press("Say goodbye")
	check("move-away confirm popup", pressed)
	var removed := await wait_for(func(): return resident_named("Qwenny").is_empty(), 6.0)
	check("resident moved away", removed)
	await wait(0.5)
	# Validation: empty name
	game._open_creator({})
	await wait(0.3)
	c.get_node("%MoveInButton").pressed.emit()
	await wait(0.3)
	check("empty name blocked", c.visible and c.get_node("%NameEdit").placeholder_text.begins_with("Everyone"))
	c.get_node("%CancelButton").pressed.emit()
	await wait(0.3)


func step_build_mode() -> void:
	print("== Build mode")
	game.show_screen("island")
	await wait(0.5)
	game._open_build_mode()
	await wait(0.5)
	var b = game.build_mode
	check("build mode opens", b.is_open)
	shot("build_move")
	# Move a building
	var lab := {}
	for x in b.draft.buildings:
		if x["type"] == "lab":
			lab = x
	var start := Vector2i(lab["x"], lab["y"])
	b._start_drag(start)
	b._drag_to(start + Vector2i(0, 1))
	b._drop()
	await wait(0.3)
	var moved := false
	for x in b.draft.buildings:
		if x["type"] == "lab":
			moved = Vector2i(x["x"], x["y"]) != start
	note("move lab one tile down: moved=%s status='%s'" % [moved, b._status.text])
	b._turn_last()
	# Plants
	b._select_tool(BuildMode.Tool.PLANTS)
	var plants_before: int = b._current_plants().size()
	b._plant_at(Vector2(3.5, 10.5))
	note("plant tree: %d -> %d (%s)" % [plants_before, b._current_plants().size(), b._status.text])
	b._set_plant_kind("remove")
	b._plant_at(Vector2(3.5, 10.5))
	b._auto_arrange()
	shot("build_plants")
	# Buildings toggle
	b._select_tool(BuildMode.Tool.BUILDINGS)
	b._toggle_step("lab")
	check("remove optional step (lab)", not b.draft.has_building("lab"))
	b._toggle_step("lab")
	check("add it back", b.draft.has_building("lab"))
	shot("build_buildings")
	# Pond + season
	b._select_tool(BuildMode.Tool.POND)
	b._set_pond_size(Vector2i(5, 2))
	check("pond large", b.draft.pond.get("w") == 5)
	b._set_season("winter")
	await wait(0.5)
	shot("build_winter")
	b._set_season("summer")
	# Land select
	b._select_tool(BuildMode.Tool.LAND)
	b._select_before = {}
	b._select_from = Vector2i(17, 10)
	b._select_to(Vector2i(18, 12))
	check("land selection", b._selected.size() == 6)
	shot("build_land_select")
	b._change_land(false)
	b._select_tool(BuildMode.Tool.MOVE)
	b._on_save_pressed()
	var closed := await wait_for(func(): return not b.is_open, 6.0)
	check("save layout", closed, b._status.text)
	await wait(1.0)
	shot("after_build_save")
	# Cancel restores
	game._open_build_mode()
	await wait(0.3)
	b._set_season("autumn")
	b.cancel()
	await wait(0.5)
	check("cancel restores season", game.island_builder.layout.season != "autumn", game.island_builder.layout.season)


func step_news_insights() -> void:
	print("== News + Insights")
	game.show_screen("news")
	await wait(1.5)
	check("news visible", game.news.visible)
	shot("news")
	game.show_screen("insights")
	await wait(1.5)
	check("insights visible", game.insights.visible)
	shot("insights")
	game.show_screen("island")


func step_night() -> void:
	print("== Night")
	Clock.dev_hour = 22.0
	await wait(1.5)
	shot("island_night")


func step_switcher_and_abandon() -> void:
	print("== Switcher + abandon")
	game.dropdown.open_below(game.get_node("%IslandSelector"))
	await wait(0.5)
	shot("switcher")
	var qa := ""
	for id in Islands._order:
		if Islands.find(id).get("name") == "QA Isle":
			qa = id
	game.dropdown._abandon(Islands.find(qa) if qa else {})
	await wait(0.5)
	shot("abandon_confirm")
	var pressed := press("Abandon island")
	check("abandon confirm popup", pressed)
	var gone := await wait_for(func(): return not Islands._order.has(qa), 8.0)
	check("QA Isle abandoned", gone)
	game.show_screen("home")
	await wait(1.0)
	shot("home_end")
