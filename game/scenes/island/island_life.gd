class_name IslandLife
extends Node
## Tomodachi-style island life: what residents do between packages.
##
## Every idle resident the server isn't using lives a little loop of their own:
## wander to a random spot on the paths, linger, then pick the next thing — another
## stroll, a visit to a friend with a short chat, a look at the pond or a tree, a
## muttered thought, or a rest back at their building. Sleeping residents roll
## over now and then. It also reacts to server events with little emotes (a hop on
## `proud`, a slump on `frustrated`, a wave when someone walks past).
##
## Rules:
## - Nothing here is sent to the backend. It is display only (CLAUDE.md): the
##   residents' real state, moods and moves all come from the server.
## - The server always wins. island_residents.gd calls `stop_life()` on a walker
##   the moment the server moves or re-states them, which ends their loop here.
##   A resident whose state stops being `idle` (falls asleep) ends their loop too.
##
## Godot concepts used: a Timer node (ticks), `await` on a signal or a timer
## (coroutines: the function pauses until the walk ends), Tweens (in
## island_resident.gd), and DayNight.night (a static var on the DayNight class).

const TICK := 1.5  # seconds between looks for residents with nothing to do
const START_CHANCE := 0.35  # per tick: a free idle resident sets off (so they don't all start together)
const SHIFT_CHANCE := 0.08  # per tick: a sleeper rolls over
const WAVE_DISTANCE := 2.6  # units: a passer-by this close gets a wave
const WAVE_COOLDOWN := 25.0  # seconds before the same two wave again
const FRIEND_WAIT := 8.0  # seconds a visitor waits for a friend to stop walking

var _residents: IslandResidents
var _timer := Timer.new()
var _active := {}  # resident id -> true while their life loop runs
var _held := {}  # resident id -> true while a visitor is chatting with them (their loop waits)
var _visiting := {}  # resident id -> true while they're on a visit
var _last_wave := {}  # "a|b" -> time of the last wave between them
var _wave_clock := 0.0
var _rng := RandomNumberGenerator.new()


func setup(residents: IslandResidents) -> void:
	_residents = residents


func _ready() -> void:
	_rng.randomize()
	_timer.wait_time = TICK
	_timer.timeout.connect(_tick)
	add_child(_timer)
	_timer.start()
	Net.mood_changed.connect(_on_mood)
	Net.handoff_happened.connect(func(_from, to, _task, _outcome): _emote(to, "hop"))
	Net.task_updated.connect(_on_task)
	Net.agent_state_changed.connect(func(agent, _state, _building): _interrupt(agent))
	Net.agent_moved.connect(func(agent, _from, _to): _interrupt(agent))
	Net.snapshot_received.connect(
		func(_island):
			_active.clear()
			_held.clear()
			_visiting.clear()
	)


## Every tick: idle residents with nothing to do set off; sleepers may roll over.
func _tick() -> void:
	if _residents == null:
		return
	for resident in _free_residents():
		var id := str(resident.get("id", ""))
		if str(resident.get("state", "idle")) == "sleeping":
			if _rng.randf() < SHIFT_CHANCE:
				_emote(id, "shift")
		elif _rng.randf() < START_CHANCE:
			_live(id)


## Residents the server isn't using: idle or asleep, standing still, not already living a loop.
func _free_residents() -> Array:
	var out: Array = []
	for r in Net.island.get("residents", []):
		var id := str(r.get("id", ""))
		var walker: IslandResident = _residents.walker(id)
		if walker == null or walker.is_walking or walker.inside or _active.has(id):
			continue
		if str(r.get("state", "idle")) in ["idle", "sleeping"]:
			out.append(r)
	return out


## One resident's life between packages: action, linger, repeat, until the server
## needs them (stop_life) or they stop being idle.
func _live(id: String) -> void:
	var walker: IslandResident = _residents.walker(id)
	if walker == null:
		return
	_active[id] = true
	walker.life_busy = true
	walker.home_spot = walker.global_position
	while _still(walker) and _is_idle(id):
		await _wait_while_held(walker)
		if not _still(walker) or not _is_idle(id):
			break
		var resident := Net.find_resident(id)
		match _pick_action(resident):
			"stroll":
				await _go(walker, IslandPaths.random_point(_residents.layout(), _rng))
			"visit":
				await _visit(resident, walker)
			"pond":
				await _pond(walker)
			"tree":
				await _tree(walker)
			"rest":
				await _rest(resident, walker)
			"mutter":
				walker.say(LifeLines.mutter(resident, Net.island), 3.5)
		if not _still(walker):
			break
		await _pause(_linger(resident))
	_finish(id)


## Weighted pick, shaped by personality and the time of day.
func _pick_action(resident: Dictionary) -> String:
	var chattiness := float(resident.get("chattiness", 0.5))
	var creativity := float(resident.get("creativity", 0.5))
	var caution := float(resident.get("caution", 0.5))
	var id := str(resident.get("id", ""))
	var weights := {
		"stroll": 3.0 + 2.0 * creativity - caution,
		"visit": (0.6 + 1.6 * chattiness) if not _friends_for(id).is_empty() else 0.0,
		"pond": (0.5 + creativity) if not _residents.layout().pond.is_empty() else 0.0,
		"tree": 0.6 if _residents.plants().get_child_count() > 0 else 0.0,
		"rest": 0.6 + caution if str(resident.get("building", "")) != "" else 0.0,
		"mutter": 0.4 + chattiness,
	}
	if DayNight.night > 0.5:  # evenings are for staying in
		for key in ["stroll", "visit", "pond", "tree"]:
			weights[key] *= 0.4
		weights["rest"] *= 3.0
	var total := 0.0
	for key in weights:
		total += maxf(weights[key], 0.0)
	var roll := _rng.randf() * total
	for key in weights:
		roll -= maxf(weights[key], 0.0)
		if roll < 0.0:
			return key
	return "stroll"


## How long to stand around between actions: careful residents linger, nights are slow.
func _linger(resident: Dictionary) -> float:
	var caution := float(resident.get("caution", 0.5))
	var seconds := _rng.randf_range(1.5, 4.0) * (1.0 + caution)
	return seconds * (2.0 if DayNight.night > 0.5 else 1.0)


## Done (or interrupted): release the resident and let island_residents place them.
func _finish(id: String) -> void:
	_active.erase(id)
	_held.erase(id)
	var walker: IslandResident = _residents.walker(id)
	if walker and walker.life_busy:
		walker.life_busy = false
		_residents.line_up()


func _interrupt(id: String) -> void:
	# The server spoke: stop_life() (called by island_residents) ends the walk; we just forget them.
	_active.erase(id)
	_held.erase(id)
	_visiting.erase(id)


## Still ours? False once the server interrupted (stop_life clears life_busy).
func _still(walker: IslandResident) -> bool:
	return is_instance_valid(walker) and walker.life_busy and _active.has(walker.resident_id)


func _is_idle(id: String) -> bool:
	return str(Net.find_resident(id).get("state", "")) == "idle"


## A visitor is chatting with this resident: stand still until they leave.
func _wait_while_held(walker: IslandResident) -> void:
	while _still(walker) and _held.has(walker.resident_id):
		await _pause(0.3)


## Walk (never run: there's no hurry) along the paths to a point, or the nearest
## clear spot to it. Returns false if the server interrupted us.
func _go(walker: IslandResident, to: Vector3) -> bool:
	to = _residents.free_spot(walker, to)
	var points: Array[Vector3] = IslandPaths.route(_residents.layout(), walker.global_position, to)
	# Already there? walk() would emit `arrived` before we could await it, and we'd wait forever.
	if points.is_empty() or walker.route_length(points) < 0.05:
		return _still(walker)
	walker.walk(points)
	await walker.arrived
	return _still(walker)


func _pause(seconds: float) -> void:
	await get_tree().create_timer(seconds).timeout


# --- The actions ---------------------------------------------------------------------


func _pond(walker: IslandResident) -> void:
	var pond: Dictionary = _residents.layout().pond
	var center := IslandLayout.tile_to_world(Vector2(pond["x"] + pond["w"] / 2.0, pond["y"] + pond["h"] / 2.0))
	if not await _go(walker, IslandPaths.nearest(_residents.layout(), center)):
		return
	walker.look_toward(center)
	if _rng.randf() < 0.6:
		walker.say(LifeLines.by_the_pond(), 3.0)
	await _pause(_rng.randf_range(3.0, 5.0))


func _tree(walker: IslandResident) -> void:
	var plants: Node3D = _residents.plants()
	var tree: Node3D = plants.get_child(_rng.randi() % plants.get_child_count())
	if not await _go(walker, IslandPaths.nearest(_residents.layout(), tree.global_position)):
		return
	walker.look_toward(tree.global_position)
	if _rng.randf() < 0.5:
		walker.say(LifeLines.by_a_tree(), 3.0)
	await _pause(_rng.randf_range(2.5, 4.0))


## Back to their own building for a breather, facing the camera.
func _rest(resident: Dictionary, walker: IslandResident) -> void:
	if not await _go(walker, _residents.home_spot(resident)):
		return
	walker.look_toward(walker.global_position + Vector3(0, 0, 1))
	await _pause(_rng.randf_range(4.0, 8.0))


## Idle neighbours someone could visit: living their own loop, not busy with another visit.
func _friends_for(id: String) -> Array:
	return Net.island.get("residents", []).filter(
		func(r):
			var other := str(r.get("id", ""))
			return (
				other != id
				and r.get("state") == "idle"
				and _active.has(other)
				and not _held.has(other)
				and not _visiting.has(other)
				and _residents.walker(other) != null
			)
	)


## Walk over to a friend (the best friend when they're around, else anyone idle) and chat.
func _visit(resident: Dictionary, walker: IslandResident) -> void:
	var id := str(resident.get("id", ""))
	var free := _friends_for(id)
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
	# Hold the friend: their own loop waits once their current walk ends.
	_held[friend_id] = true
	_visiting[id] = true
	var waited := 0.0
	while is_instance_valid(other) and other.is_walking and waited < FRIEND_WAIT and _still(walker):
		await _pause(0.25)
		waited += 0.25
	if is_instance_valid(other) and _still(other) and not other.is_walking:
		# Just beside them, on the side we come from (_go keeps us off their toes).
		var side := (walker.global_position - other.global_position) * Vector3(1, 0, 1)
		var beside: Vector3 = other.global_position + side.normalized() * 1.2
		if await _go(walker, beside) and _still(other):
			walker.look_toward(other.global_position)
			other.look_toward(walker.global_position)
			for pair in LifeLines.chat(resident, friend):
				if not _still(walker) or not _still(other):
					break
				var speaker: IslandResident = walker if pair[0].get("id") == id else other
				speaker.say(pair[1], 3.0)
				await _pause(2.6)
	_held.erase(friend_id)
	_visiting.erase(id)


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


## Waves: someone walking past a resident who is standing about (checked twice a second).
func _process(delta: float) -> void:
	_wave_clock += delta
	if _wave_clock < 0.5 or _residents == null:
		return
	_wave_clock = 0.0
	var all: Dictionary = _residents.walkers()
	for id in all:
		var mover: IslandResident = all[id]
		if not mover.is_walking or mover.inside:
			continue
		for other_id in all:
			if other_id == id:
				continue
			var bystander: IslandResident = all[other_id]
			if bystander.is_walking or bystander.inside or bystander.state in ["sleeping", "working"]:
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
