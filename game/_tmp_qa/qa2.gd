extends Node
## Second QA pass: plants, live look updates, bald/hat looks, headshot refresh.

var game
var fails := 0


func _ready() -> void:
	game = load("res://scenes/game.tscn").instantiate()
	add_child(game)
	await wait(3.0)
	game._visit_island("sunny")
	game.show_screen("island")
	await wait(2.5)
	await plants()
	await live_look()
	await bald_and_hat()
	print("QA2 DONE: %d failures" % fails)
	get_tree().quit()


func wait(t: float) -> void:
	await get_tree().create_timer(t).timeout


func check(what: String, ok: bool, detail := "") -> void:
	if not ok:
		fails += 1
	print("%s  %s %s" % ["PASS" if ok else "FAIL", what, ("(" + detail + ")") if detail else ""])


func walkers() -> Dictionary:
	for node in get_tree().root.find_children("*", "Node3D", true, false):
		if "_walkers" in node:
			return node._walkers
	return {}


func plants() -> void:
	print("== Plants")
	game._open_build_mode()
	await wait(0.4)
	var b = game.build_mode
	b._select_tool(BuildMode.Tool.PLANTS)
	b._set_plant_kind("bush")
	var placed := 0
	var tried := 0
	for y in range(1, 13):
		for x in range(1, 19):
			if placed >= 3:
				break
			tried += 1
			var before: int = b._current_plants().size()
			b._plant_at(Vector2(x + 0.5, y + 0.5))
			if b._current_plants().size() > before:
				placed += 1
	check("bushes can be planted somewhere", placed == 3, "placed %d after %d tries" % [placed, tried])
	b._set_plant_kind("tree")
	var trees := 0
	for y in range(1, 13):
		for x in range(1, 19):
			var before: int = b._current_plants().size()
			b._plant_at(Vector2(x + 0.5, y + 0.5))
			if b._current_plants().size() > before:
				trees += 1
				break
		if trees:
			break
	check("a tree can be planted somewhere", trees == 1)
	b.cancel()
	await wait(0.3)


func live_look() -> void:
	print("== Live look update")
	var r: Dictionary = Net.island["residents"][0]
	var id := str(r["id"])
	var w: Dictionary = walkers()
	check("walker exists for " + id, w.has(id))
	var look: Dictionary = MiiParts.for_resident(r).duplicate()
	look["hair"] = 33
	look["eyes"] = 44
	var updated := r.duplicate(true)
	updated["appearance"] = look
	var tex_before: Texture2D = Portraits.of(r)
	Net.send_resident(updated)
	await wait(2.0)
	if w.has(id):
		var mii: Mii = w[id].mii
		check("island Mii shows the new look live", int(mii.appearance["hair"]) == 33 and int(mii.appearance["eyes"]) == 44, str(mii.appearance))
	game.show_screen("residents")
	await wait(1.5)
	var card_tex: Texture2D = null
	for card in game.residents_screen.find_children("*", "ResidentCard", true, false):
		if card.resident.get("id") == id:
			card_tex = card.get_node("%Portrait").texture
	check("headshot changes after the edit", card_tex != null and card_tex != tex_before)
	game.show_screen("island")


func bald_and_hat() -> void:
	print("== Bald and hat")
	var r: Dictionary = Net.island["residents"][1]
	var id := str(r["id"])
	for style in [MiiParts.NO_HAIR, MiiParts.HAIR_COUNT + 1]:
		var updated := r.duplicate(true)
		updated["appearance"] = MiiParts.for_resident(r).duplicate()
		updated["appearance"]["hair"] = style
		Net.send_resident(updated)
		await wait(1.5)
		var saved: Dictionary = {}
		for x in Net.island["residents"]:
			if x["id"] == id:
				saved = x.get("appearance", {})
		check("hair %d saved" % style, int(saved.get("hair", -99)) == style, str(saved))
		var w: Dictionary = walkers()
		if w.has(id):
			var hair: MeshInstance3D = w[id].mii.find_child("Hair", true, false)
			check("island Mii hair mesh for %d" % style, (hair.mesh == null) == (style == MiiParts.NO_HAIR))
