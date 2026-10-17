class_name IslandResidents
extends Node3D
## Puts every resident from the backend on the 3D island and keeps them in step
## with its events. Lives in island_3d.tscn as `Residents`.
##
## - snapshot           -> everyone appears at their building, in their state
## - move (from -> to)  -> walk there, the shortest way along the sand paths
## - agent_state        -> working (hop, facing the building), idle, sleeping (z Z)
## - bubble             -> an icon over their head until it's answered
##
## Where a resident stands: each building has a *stop* on the sand path, the
## tile just outside its door. The camera looks from the south, so a door that
## faces away hides anyone standing there: those stops slide along the path
## until they're clear of the building. Residents at the same building line up
## along the path from the stop.
##
## The paths come from the island's layout (IslandBuilder.layout); when
## it changes (build mode), everyone is placed again.
##
## Between packages, island_life.gd (the `Life` child) borrows idle residents
## for strolls, visits and chatter. The server always wins: any event for a
## resident ends their ambient action (stop_life) before we place them.

const ResidentScene := preload("res://scenes/island/island_resident.tscn")
const SEARCH_TILES := 12  # how far along the path a hidden stop may slide

var _walkers := {}  # resident id -> IslandResident
var _stops := {}  # building id -> Vector3, worked out once per building (per island)
var _life: IslandLife

@onready var island: IslandBuilder = get_node("../Island")


func _ready() -> void:
	Net.snapshot_received.connect(func(_island): _rebuild.call_deferred())  # after the island is rebuilt
	Net.agent_moved.connect(_on_moved)
	Net.island_changed.connect(_sync)
	Net.chat_said.connect(_on_chat)
	island.built.connect(_rebuild)
	_life = IslandLife.new()
	_life.name = "Life"
	add_child(_life)
	_life.setup(self)


## --- What island_life.gd needs ----------------------------------------------------


func walker(resident_id: String) -> IslandResident:
	return _walkers.get(resident_id)


func walkers() -> Dictionary:
	return _walkers


func layout() -> IslandLayout:
	return island.layout


func plants() -> Node3D:
	return island.plants


## Put everyone who isn't busy back at their slot (after an ambient action).
func line_up() -> void:
	_line_up(false)


## A resident said something: a speech line over their head, and the listener turns.
func _on_chat(from_agent: String, to_agent: String, _task: String, text: String) -> void:
	var speaker: IslandResident = _walkers.get(from_agent)
	var listener: IslandResident = _walkers.get(to_agent)
	if speaker:
		speaker.say(text, clamp(2.5 + text.length() * 0.05, 3.0, 8.0))
		if listener:
			speaker.look_toward(listener.global_position)
	if listener and speaker:
		listener.look_toward(speaker.global_position)


## Throw everything away and place everyone exactly where the snapshot says.
func _rebuild() -> void:
	_stops.clear()  # maybe another island
	for walker in _walkers.values():
		walker.queue_free()
	_walkers.clear()
	_sync(true)


## Bring every walker in line with Net.island. `snap` = jump, don't walk.
func _sync(snap := false) -> void:
	var bubbles := {}
	for b in Net.island.get("bubbles", []):
		bubbles[b["agent"]] = b

	for resident in Net.island.get("residents", []):
		var id := str(resident["id"])
		if not _walkers.has(id):
			var walker: IslandResident = ResidentScene.instantiate()
			walker.resident_id = id
			add_child(walker)
			walker.arrived.connect(_sync)  # once they arrive, line up with the others
			walker.place(_stop(str(resident.get("building", ""))))
			_walkers[id] = walker
		_walkers[id].show_resident(resident, bubbles.get(id, {}))

	# Anyone who moved away (`resident_removed`) leaves the island.
	for id in _walkers.keys():
		if Net.find_resident(id).is_empty():
			_walkers[id].queue_free()
			_walkers.erase(id)

	_line_up(snap)


## Put everyone who isn't walking at their slot, in their state.
func _line_up(snap: bool) -> void:
	var at_building := {}  # building id -> [resident, ...]
	for resident in Net.island.get("residents", []):
		var walker: IslandResident = _walkers.get(str(resident["id"]))
		if walker == null:
			continue
		if walker.life_busy:
			if str(resident.get("state", "idle")) in ["idle", "sleeping"]:
				continue  # off on an ambient stroll; island_life brings them back
			walker.stop_life()  # the server put them to work: ambient life ends now
		if walker.is_walking:
			continue
		var building := str(resident.get("building", ""))
		if resident.get("state") == "walking" and walker.global_position.distance_to(_stop(building)) > 1.5:
			# Walking, but no `move` came (e.g. they had no building before): go there.
			walker.walk(IslandPaths.route(island.layout, walker.global_position, _stop(building)))
			continue
		# (Arrived before the server's walk timer ran out: wait at the building.)
		at_building.get_or_add(building, []).append(resident)

	for building in at_building:
		var group: Array = at_building[building]
		group.sort_custom(func(a, b): return str(a["id"]) < str(b["id"]))
		for i in group.size():
			var walker: IslandResident = _walkers[str(group[i]["id"])]
			var spot := _slot(building, i)
			if snap:
				walker.place(spot)
			var state := str(group[i].get("state", "idle"))
			var route := IslandPaths.route(island.layout, walker.global_position, spot)
			walker.settle(route, "idle" if state == "walking" else state, _building_position(building))


## Walk to the new building the shortest way along the sand paths (they join
## every door, so no need to pass the buildings in between).
func _on_moved(agent: String, _from_building: String, to_building: String) -> void:
	var walker: IslandResident = _walkers.get(agent)
	if walker == null:
		return
	walker.stop_life()
	walker.walk(IslandPaths.route(island.layout, walker.global_position, _stop(to_building)))


## The point on the sand path where a building's residents stand.
func _stop(building_id: String) -> Vector3:
	if _stops.has(building_id):
		return _stops[building_id]
	var b := island.layout.building(str(Net.find_building(building_id).get("type", "")))
	if b.is_empty():
		return IslandPaths.nearest(island.layout, Vector3(0, 0, 8))  # homeless: somewhere near the front
	var stop := _line(b)[0]
	_stops[building_id] = stop
	return stop


## Path tiles from the building's door outward (nearest first), skipping the
## ones the building hides from the camera.
func _line(b: Dictionary) -> Array[Vector3]:
	var layout := island.layout
	var start := IslandLayout.door_tile(b)
	if not layout.path_tiles.has(start):
		start = layout.nearest_path_tile(IslandLayout.tile_center(start))
	var order: Array[Vector2i] = [start]
	var seen := {start: true}
	var i := 0
	while i < order.size() and order.size() < SEARCH_TILES:
		var t := order[i]
		for n in [t + Vector2i.RIGHT, t + Vector2i.LEFT, t + Vector2i.DOWN, t + Vector2i.UP]:
			if layout.path_tiles.has(n) and not seen.has(n):
				seen[n] = true
				order.append(n)
		i += 1
	var lot := Rect2(Vector2(b["x"], b["y"]), Vector2(IslandLayout.LOT, IslandLayout.LOT))
	var out: Array[Vector3] = []
	for t in order:
		# Hidden = right behind the lot as the camera (looking north) sees it.
		var hidden := t.y < lot.position.y and t.x >= lot.position.x - 0.5 and t.x < lot.end.x + 0.5
		if not hidden:
			out.append(IslandPaths.nearest(layout, IslandLayout.tile_center(t)))
	if out.is_empty():
		out.append(IslandPaths.nearest(layout, IslandLayout.tile_center(start)))
	return out


## Where the i-th resident at a building stands: the next path tiles along from the stop.
func _slot(building_id: String, i: int) -> Vector3:
	var b := island.layout.building(str(Net.find_building(building_id).get("type", "")))
	if b.is_empty() or i == 0:
		return _stop(building_id)
	var line := _line(b)
	return line[mini(i, line.size() - 1)]


func _building_position(building_id: String) -> Vector3:
	var node := _building_node(building_id)
	return node.global_position if node else Vector3.ZERO


## The 3D node for a building id. Nodes are named by building *type*, in
## PascalCase ("post_office" -> PostOffice), since ids differ per island.
func _building_node(building_id: String) -> Node3D:
	var type := str(Net.find_building(building_id).get("type", ""))
	if type == "":
		return null
	return island.building_node(type)
