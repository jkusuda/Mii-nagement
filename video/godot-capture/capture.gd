extends Node
# Drives the game for a Movie Maker recording (video/README.md).
# CAPTURE_MODE = flyover | residents | workflow | talk. The 3D modes show the island
# full screen without the UI, with the camera driven from here.

const CAST := ["jun", "carlos", "sydney"]

var game: Node
var t := 0.0
var mode := OS.get_environment("CAPTURE_MODE")
var steps_done := {}
var cam: Camera3D
var residents: Node


func _ready() -> void:
	game = load("res://scenes/game.tscn").instantiate()
	add_child(game)
	Clock.dev_hour = 10.5


func _once(key: String, at: float) -> bool:
	if t >= at and not steps_done.has(key):
		steps_done[key] = true
		return true
	return false


func _walker(prefix: String) -> Node:
	for id in residents.walkers():
		if str(id).begins_with(prefix):
			return residents.walkers()[id]
	return null


# Show only the 3D island, full window, and take the camera over.
func _cinematic() -> void:
	var container: Control = game.rig.get_viewport().get_parent()
	container.reparent(game)
	container.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	game.get_node("Margin").visible = false
	game.labels_root.visible = false
	game.rig.set_process(false)
	game.rig.set_process_unhandled_input(false)
	cam = game.rig.camera


func _look(from: Vector3, at: Vector3, fov := 30.0) -> void:
	cam.global_position = from
	cam.fov = fov
	cam.look_at(at, Vector3.UP)


func _ease(x: float) -> float:
	x = clampf(x, 0.0, 1.0)
	return x * x * (3.0 - 2.0 * x)


func _process(delta: float) -> void:
	t += delta
	if _once("visit", 0.3):
		game._visit_island("sunny_isle")
	if _once("island", 1.0):
		game.show_screen("island")
		if game._dev_pill:
			game._dev_pill.visible = false
		residents = game.island.get_node("Residents")
		if mode != "talk":
			_cinematic()
	if t < 1.0:
		return
	match mode:
		"flyover":
			_flyover()
		"residents":
			_residents()
		"workflow":
			_workflow(delta)
		"talk":
			_talk()


# Stop server events from moving the residents (they'd go back to sleep mid-shot).
func _deafen(node: Node) -> void:
	var cut := 0
	for source: Object in [Net, node.island] + node.walkers().values():
		for sig in source.get_signal_list():
			for c in source.get_signal_connection_list(sig["name"]):
				var target: Object = c["callable"].get_object()
				if target == node or (target is Node and node.is_ancestor_of(target)):
					source.disconnect(sig["name"], c["callable"])
					cut += 1
	print("deafened ", cut)


func _flyover() -> void:
	var p := _ease((t - 1.0) / 20.0)
	var yaw := lerpf(-0.65, 0.4, p)
	var dist := lerpf(78.0, 40.0, p)
	var el := deg_to_rad(lerpf(52.0, 33.0, p))
	var c := Vector3(0, 0, -1.5)
	_look(c + Vector3(sin(yaw) * cos(el), sin(el), cos(yaw) * cos(el)) * dist, c, 30.0)


func _residents() -> void:
	if _once("quiet", 1.5):
		_deafen(residents)
		residents._life.queue_free()  # its pending strolls would steer them too
	if _once("cast", 2.2):
		# Bushes between the camera and the path would hide the residents.
		for plant: Node3D in residents.plants().get_children():
			if plant.global_position.z > 6.0 and absf(plant.global_position.x) < 12.0:
				plant.visible = false
		for id in residents.walkers():
			var w: Node = residents.walkers()[id]
			w.stop_life()
			var i := CAST.find(str(id).split("_")[0])
			if i < 0:
				w.visible = false
				continue
			w.place(Vector3(-17.0 + i * 3.2, 0, 5.0))
	if _once("walk_in", 2.8):
		for i in CAST.size():
			var w: Node = _walker(CAST[i])
			var to := Vector3(-1.8 + i * 3.3, 0, 5.0)
			w.walk([to] as Array[Vector3])
			w.arrived.connect(func(): w.look_toward(cam.global_position), CONNECT_ONE_SHOT)
	if _once("check", 9.0):
		for c in CAST:
			var w: Node = _walker(c)
			print("cast ", c, " ", w.global_position, " inside ", w.inside, " shown ", w.mii.visible, " state ", w.state)
	var p := _ease((t - 1.0) / 16.0)
	_look(Vector3(lerpf(0.0, 3.0, p), 2.5, lerpf(16.0, 14.5, p)), Vector3(1.5, 0.9, 5.0), 32.0)


func _workflow(_delta: float) -> void:
	# A slow drift along the row of workflow buildings, close enough to read the bubbles.
	var p := _ease((t - 1.0) / 30.0)
	var at := Vector3(lerpf(-6.0, 5.0, p), 0, -3.0)
	var yaw := lerpf(-0.18, 0.18, p)
	_look(at + Vector3(sin(yaw), 1.0, cos(yaw)) * 19.0, at + Vector3(0, 0, -1.5), 34.0)


var QUESTION := OS.get_environment("CAPTURE_QUESTION") if OS.has_environment("CAPTURE_QUESTION") else "What are you working on?"
var _talk_at := {}  # moment -> t, printed for the edit


func _mark(what: String) -> void:
	if not _talk_at.has(what):
		_talk_at[what] = t
		print("talk ", what, " at ", snappedf(t, 0.01))


# The building panel, then Talk with Carlos, then type and send a question, then
# wait for the real reply to finish typing in.
func _talk() -> void:
	var popup: Node = game.talk_popup
	var carlos := ""
	var at_work := false
	for r in Net.island.get("residents", []):
		if str(r.get("id", "")).begins_with("carlos"):
			carlos = str(r["id"])
			at_work = r.get("state") == "working" and r.get("building") == "sunny_isle_workshop"
	# Wait (this part gets cut) until Carlos is inside the Workshop on the real task.
	if not _talk_at.has("at_work"):
		if t > 2.0 and at_work:
			_mark("at_work")
		elif t > 400.0:
			print("talk: carlos never got to work")
			get_tree().quit()
		return
	var go: float = _talk_at["at_work"] + 1.0
	if _once("panel", go):
		game.open_building("workshop")
	if _once("talk", go + 3.5):
		game.building_panel.talk_requested.emit(carlos, "sunny_isle_workshop")
		_mark("popup")
	if t < go + 5.5 or not popup.get("_ready"):  # popup._ready would be Node._ready()
		return
	_mark("ready")
	var start: float = _talk_at["ready"] + 0.5
	var typed := clampi(int((t - start) / 0.075), 0, QUESTION.length())
	if not _talk_at.has("sent"):
		if t >= start:
			_mark("typing")
			var line: LineEdit = popup.get("_input")  # popup._input would be Node._input()
			line.text = QUESTION.left(typed)
			line.caret_column = typed
		if typed == QUESTION.length() and t > start + QUESTION.length() * 0.075 + 0.7:
			popup._send_text(QUESTION)
			_mark("sent")
		return
	var turns: Array = popup.get("_turns")
	if popup.get("_partial") != "":
		_mark("reply_starts")
	if popup.get("_pending") == "" and turns.size() >= 2:
		if not _talk_at.has("reply_done"):
			_mark("reply_done")
			print("reply: ", turns[-1]["text"])
		if t > _talk_at["reply_done"] + 7.0:
			get_tree().quit()
	if t > go + 240.0:
		print("talk timed out")
		get_tree().quit()
