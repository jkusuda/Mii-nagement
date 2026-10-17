extends Node3D
## Puts every resident from the backend on the 3D island and keeps them in step
## with its events. Lives in island_3d.tscn as `Residents`.
##
## - snapshot           -> everyone appears at their building, in their state
## - move (from -> to)  -> walk there, along the island's roads
## - agent_state        -> working (hop, facing the building), idle, sleeping (z Z)
## - bubble             -> an icon over their head until it's answered
##
## Where a resident stands: each building has a *stop* on the sand path, the
## point nearest to it. The camera looks from the south, so a building in the
## bottom row would hide anyone standing on the path behind it: those stops
## slide along the path until they're clear of the building. Residents at the
## same building line up along the path, heading toward the island's middle.

const ResidentScene := preload("res://scenes/island/island_resident.tscn")
const SLOT_SPACING := 1.4  # gap between residents lined up at one building
const CLEARANCE := 1.2     # how far past a building's edge a hidden stop slides

var _walkers := {}  # resident id -> IslandResident
var _stops := {}    # building id -> Vector3, worked out once per building (per island)

@onready var buildings: Node3D = get_node("../Buildings")


func _ready() -> void:
	Net.snapshot_received.connect(func(_island): _rebuild())
	Net.agent_moved.connect(_on_moved)
	Net.island_changed.connect(_sync)


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

	# Anyone the server dropped (not possible yet, but cheap to handle).
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
		if walker == null or walker.is_walking:
			continue
		var building := str(resident.get("building", ""))
		if resident.get("state") == "walking" and walker.global_position.distance_to(_stop(building)) > 1.5:
			# Walking, but no `move` came (e.g. they had no building before): go straight there.
			_walk(walker, [_stop(building)])
			continue
		# (Arrived before the server's walk timer ran out: wait at the building.)
		at_building.get_or_add(building, []).append(resident)

	for building in at_building:
		var group: Array = at_building[building]
		group.sort_custom(func(a, b): return str(a["id"]) < str(b["id"]))
		for i in group.size():
			var walker: IslandResident = _walkers[str(group[i]["id"])]
			var spot := _slot(building, i, group.size())
			if snap:
				walker.place(spot)
			var state := str(group[i].get("state", "idle"))
			walker.settle(spot, "idle" if state == "walking" else state, _building_position(building))


func _on_moved(agent: String, from_building: String, to_building: String) -> void:
	var walker: IslandResident = _walkers.get(agent)
	if walker == null:
		return
	var points: Array[Vector3] = []
	var stops := [walker.global_position]
	for building in _road_route(from_building, to_building):
		stops.append(_stop(building))
	for i in range(1, stops.size()):
		points.append_array(IslandPaths.route(stops[i - 1], stops[i]))
	_walk(walker, points)


func _walk(walker: IslandResident, points: Array) -> void:
	var typed: Array[Vector3] = []
	typed.assign(points)
	walker.walk(typed)


## The buildings to pass through to get from one building to another, following
## the island's roads (in either direction), from `from` to `to` inclusive.
## A breadth-first search: look at the neighbours, then their neighbours, and so
## on, remembering where each building was reached from.
func _road_route(from: String, to: String) -> Array:
	var neighbours := {}
	for road in Net.island.get("roads", []):
		neighbours.get_or_add(road["from"], []).append(road["to"])
		neighbours.get_or_add(road["to"], []).append(road["from"])

	var came_from := {from: ""}
	var queue := [from]
	while not queue.is_empty():
		var here: String = queue.pop_front()
		if here == to:
			break
		for next in neighbours.get(here, []):
			if not came_from.has(next):
				came_from[next] = here
				queue.append(next)

	if not came_from.has(to):
		return [to]  # no road connects them: walk straight there
	var route := [to]
	while route[0] != from:
		route.push_front(came_from[route[0]])
	return route


## The point on the sand path where a building's residents stand.
func _stop(building_id: String) -> Vector3:
	if _stops.has(building_id):
		return _stops[building_id]
	var node := _building_node(building_id)
	if node == null:
		return IslandPaths.nearest(Vector3(0, 0, IslandPaths.LOOP_MAX.y))  # homeless: the bottom path
	var stop := IslandPaths.nearest(node.global_position)
	var box := _footprint(node)
	# Behind the building, as the camera sees it? Slide toward the middle until clear.
	var hidden := stop.z < box.end.z and stop.x > box.position.x and stop.x < box.end.x
	if hidden:
		stop.x = box.end.x + CLEARANCE if stop.x < 0.0 else box.position.x - CLEARANCE
		stop = IslandPaths.nearest(stop)
	_stops[building_id] = stop
	return stop


## Where the i-th resident at a building stands: along the path from the stop,
## toward the island's middle (x = 0).
func _slot(building_id: String, i: int, _n: int) -> Vector3:
	var stop := _stop(building_id)
	var toward_middle := Vector3(-signf(stop.x) if absf(stop.x) > 0.5 else 1.0, 0, 0)
	return stop + toward_middle * SLOT_SPACING * i


## A building's bounding box in world space (only x and z matter here).
func _footprint(node: Node3D) -> AABB:
	var box := AABB(node.global_position, Vector3.ZERO)
	for mesh: MeshInstance3D in node.find_children("*", "MeshInstance3D"):
		box = box.merge(mesh.global_transform * mesh.get_aabb())
	return box


func _building_position(building_id: String) -> Vector3:
	var node := _building_node(building_id)
	return node.global_position if node else Vector3.ZERO


## The 3D node for a building id. Nodes are named by building *type*, in
## PascalCase ("post_office" -> PostOffice), since ids differ per island.
func _building_node(building_id: String) -> Node3D:
	var type := str(Net.find_building(building_id).get("type", ""))
	if type == "":
		return null
	return buildings.get_node_or_null(type.to_pascal_case())
