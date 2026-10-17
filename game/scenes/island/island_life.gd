class_name IslandLife
extends Node
## Tomodachi-style island life: what residents do between packages.
##
## Every few seconds this picks an idle (or sleeping) resident the server isn't
## using and gives them something small to do: a stroll along the paths, a visit
## to a friend with a short chat, a look at the pond or a tree, a muttered thought,
## a roll-over in their sleep. It also reacts to server events with little emotes
## (a hop on `proud`, a slump on `frustrated`, a wave when someone walks past).
##
## Rules:
## - Nothing here is sent to the backend. It is display only (CLAUDE.md): the
##   residents' real state, moods and moves all come from the server.
## - The server always wins. island_residents.gd calls `stop_life()` on a walker
##   the moment the server moves or re-states them, which ends the action here.
## - At most MAX_ACTIVE residents are in an action at once, one action each.
##
## Godot concepts used: a Timer node (ticks), `await` on a signal or a timer
## (coroutines: the function pauses until the walk ends), Tweens (in
## island_resident.gd), and the DayNight autoload's `night` value.

const ENABLED := true
const TICK_MIN := 6.0
const TICK_MAX := 15.0
const MAX_ACTIVE := 2
const WAVE_DISTANCE := 2.6   # units: a passer-by this close gets a wave
const WAVE_COOLDOWN := 25.0  # seconds before the same two wave again

var _residents: Node  # island_residents.gd
var _timer := Timer.new()
var _active := {}     # resident id -> true while they're in an ambient action
var _last_wave := {}  # "a|b" -> time of the last wave between them
var _wave_clock := 0.0
var _rng := RandomNumberGenerator.new()


func setup(residents: Node) -> void:
	_residents = residents


func _ready() -> void:
	_rng.randomize()
	_timer.one_shot = true
	_timer.timeout.connect(_tick)
	add_child(_timer)
	_schedule()
	Net.mood_changed.connect(_on_mood)
	Net.handoff_happened.connect(func(_from, to, _task, _outcome): _emote(to, "hop"))
	Net.task_updated.connect(_on_task)
	Net.agent_state_changed.connect(func(agent, _state, _building): _interrupt(agent))
	Net.agent_moved.connect(func(agent, _from, _to): _interrupt(agent))
	Net.snapshot_received.connect(func(_island): _active.clear())


func _schedule() -> void:
	_timer.start(_rng.randf_range(TICK_MIN, TICK_MAX))


## Every tick: maybe start one ambient action for one free resident.
func _tick() -> void:
	_schedule()
	if not ENABLED or _residents == null or _active.size() >= MAX_ACTIVE:
		return
	var free := _free_residents()
	if free.is_empty():
		return
	var resident: Dictionary = free[_rng.randi() % free.size()]
	var action := _pick_action(resident, free)
	if action != "":
		_run(resident, action)


## Residents the server isn't using: idle or asleep, standing still, not already busy.
func _free_residents() -> Array:
	var out: Array = []
	for r in Net.island.get("residents", []):
		var id := str(r.get("id", ""))
		var walker: IslandResident = _residents.walker(id)
		if walker == null or walker.is_walking or _active.has(id):
			continue
		if str(r.get("state", "idle")) in ["idle", "sleeping"]:
			out.append(r)
	return out


## Weighted pick, shaped by personality and the time of day.
func _pick_action(resident: Dictionary, free: Array) -> String:
	if str(resident.get("state", "idle")) == "sleeping":
		return "shift" if _rng.randf() < 0.6 else ""
	var chattiness := float(resident.get("chattiness", 0.5))
	var creativity := float(resident.get("creativity", 0.5))
	var caution := float(resident.get("caution", 0.5))
	var others := free.filter(func(r): return r.get("id") != resident.get("id") and r.get("state") == "idle")
	var weights := {
		"stroll": 1.5 + 2.0 * creativity - caution,
		"visit": (1.0 + 2.5 * chattiness) if not others.is_empty() else 0.0,
		"pond": (0.8 + creativity) if not _residents.layout().pond.is_empty() else 0.0,
		"tree": 0.8 if _residents.plants().get_child_count() > 0 else 0.0,
		"mutter": 0.8 + 2.0 * chattiness,
		"nothing": 2.0,
	}
	if DayNight.night > 0.5:
		for key in ["stroll", "visit", "pond", "tree"]:
			weights[key] *= 0.4
	var total := 0.0
	for key in weights:
		total += maxf(weights[key], 0.0)
	var roll := _rng.randf() * total
	for key in weights:
		roll -= maxf(weights[key], 0.0)
		if roll < 0.0:
			return "" if key == "nothing" else key
	return ""


func _run(resident: Dictionary, action: String) -> void:
	var id := str(resident.get("id", ""))
	var walker: IslandResident = _residents.walker(id)
	if walker == null:
		return
	_active[id] = true
	walker.life_busy = true
	walker.home_spot = walker.global_position
	match action:
		"stroll": await _stroll(walker)
		"visit": await _visit(resident, walker)
		"pond": await _pond(walker)
		"tree": await _tree(walker)
		"mutter": walker.say(LifeLines.mutter(resident, Net.island), 3.5)
		"shift": walker.emote("shift")
	_finish(id)


## Done (or interrupted): release the resident and let island_residents place them.
func _finish(id: String) -> void:
	if not _active.has(id):
		return
	_active.erase(id)
	var walker: IslandResident = _residents.walker(id)
	if walker and walker.life_busy:
		walker.life_busy = false
		_residents.line_up()


func _interrupt(id: String) -> void:
	# The server spoke: stop_life() (called by island_residents) ends the walk; we just forget them.
	_active.erase(id)


## Still ours? False once the server interrupted (stop_life clears life_busy).
func _still(walker: IslandResident) -> bool:
	return walker.life_busy and _active.has(walker.resident_id)


## Walk along the paths to a point. Returns false if the server interrupted us.
func _go(walker: IslandResident, to: Vector3) -> bool:
	var points: Array[Vector3] = IslandPaths.route(_residents.layout(), walker.global_position, to)
	if points.is_empty():
		return _still(walker)
	walker.walk(points)
	await walker.arrived
	return _still(walker)


func _pause(seconds: float) -> void:
	await get_tree().create_timer(seconds).timeout


func _random_path_point() -> Vector3:
	var tiles: Array = _residents.layout().path_tiles.keys()
	if tiles.is_empty():
		return Vector3.ZERO
	return IslandLayout.tile_center(tiles[_rng.randi() % tiles.size()])


# --- The actions ---------------------------------------------------------------------


func _stroll(walker: IslandResident) -> void:
	if not await _go(walker, _random_path_point()):
		return
	await _pause(_rng.randf_range(1.5, 3.0))
	if not _still(walker):
		return
	await _go(walker, walker.home_spot)


func _pond(walker: IslandResident) -> void:
	var pond: Dictionary = _residents.layout().pond
	var center := IslandLayout.tile_to_world(Vector2(pond["x"] + pond["w"] / 2.0, pond["y"] + pond["h"] / 2.0))
	if not await _go(walker, IslandPaths.nearest(_residents.layout(), center)):
		return
	walker.look_toward(center)
	if _rng.randf() < 0.6:
		walker.say(LifeLines.by_the_pond(), 3.0)
	await _pause(_rng.randf_range(3.0, 5.0))
	if not _still(walker):
		return
	await _go(walker, walker.home_spot)


func _tree(walker: IslandResident) -> void:
	var plants: Node3D = _residents.plants()
	var tree: Node3D = plants.get_child(_rng.randi() % plants.get_child_count())
	if not await _go(walker, IslandPaths.nearest(_residents.layout(), tree.global_position)):
		return
	walker.look_toward(tree.global_position)
	if _rng.randf() < 0.5:
		walker.say(LifeLines.by_a_tree(), 3.0)
	await _pause(_rng.randf_range(2.5, 4.0))
	if not _still(walker):
		return
	await _go(walker, walker.home_spot)


## Walk over to a friend (the best friend when they're free, else anyone idle) and chat.
func _visit(resident: Dictionary, walker: IslandResident) -> void:
	var id := str(resident.get("id", ""))
	var free := _free_residents().filter(func(r): return r.get("id") != id and r.get("state") == "idle")
	if free.is_empty():
		return
	var friend: Dictionary = {}
	var best := ResidentData.best_friend(id, Net.island.get("friendships", []))
	for r in free:
		if not best.is_empty() and r.get("id") == best["id"] and _rng.randf() < 0.75:
			friend = r
	if friend.is_empty():
		friend = free[_rng.randi() % free.size()]
	var friend_id := str(friend.get("id", ""))
	var other: IslandResident = _residents.walker(friend_id)
	if other == null:
		return
	# Borrow the friend too, so they don't wander off mid-sentence.
	_active[friend_id] = true
	other.life_busy = true
	other.home_spot = other.global_position
	var beside := IslandPaths.nearest(_residents.layout(), other.global_position + Vector3(1.6, 0, 0.6))
	if await _go(walker, beside) and _still(other):
		walker.look_toward(other.global_position)
		other.look_toward(walker.global_position)
		for pair in LifeLines.chat(resident, friend):
			if not _still(walker) or not _still(other):
				break
			var speaker: IslandResident = walker if pair[0].get("id") == id else other
			speaker.say(pair[1], 3.0)
			await _pause(2.6)
	_finish(friend_id)
	if _still(walker):
		await _go(walker, walker.home_spot)


# --- Reactions to the server's events ---------------------------------------------


func _emote(id: String, kind: String) -> void:
	var walker: IslandResident = _residents.walker(id) if _residents else null
	if walker:
		walker.emote(kind)


func _on_mood(agent: String, mood: String) -> void:
	if mood in ["proud", "happy", "frustrated", "tired", "confused"]:
		_emote(agent, mood)


func _on_task(task: Dictionary) -> void:
	if task.get("status") != "done":
		return
	for r in Net.island.get("residents", []):  # everyone at the Post Office cheers
		if ResidentData.runs_building_type(str(r.get("id", "")), "post_office", Net.island):
			_emote(str(r.get("id", "")), "proud")


## Waves: a resident the server is walking past an idle one (checked twice a second).
func _process(delta: float) -> void:
	_wave_clock += delta
	if _wave_clock < 0.5 or _residents == null:
		return
	_wave_clock = 0.0
	var all: Dictionary = _residents.walkers()
	for id in all:
		var mover: IslandResident = all[id]
		if not mover.is_walking or mover.life_busy:
			continue
		for other_id in all:
			if other_id == id:
				continue
			var bystander: IslandResident = all[other_id]
			if bystander.is_walking or bystander.state == "sleeping" or bystander.state == "working":
				continue
			if mover.global_position.distance_to(bystander.global_position) > WAVE_DISTANCE:
				continue
			var key := "%s|%s" % ([id, other_id] if id < other_id else [other_id, id])
			var now := Time.get_unix_time_from_system()
			if now - float(_last_wave.get(key, 0.0)) < WAVE_COOLDOWN:
				continue
			_last_wave[key] = now
			bystander.look_toward(mover.global_position)
			bystander.emote("wave")
