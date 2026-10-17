class_name BuildingPicker
extends RefCounted
## Which building is under the mouse? A ray from the camera through the mouse,
## tested against each building's bounding box. No physics bodies needed.
##
## Godot concept: an AABB ("axis-aligned bounding box") is the smallest box
## around a mesh. Each MeshInstance3D knows its own; we merge them per building
## (in the building's local space) once, and reuse them.

var _buildings: Node3D  # island_3d.tscn's Buildings node (children named by type)
var _camera: Camera3D
var _boxes := {}  # building node -> AABB in its local space


func _init(buildings: Node3D, camera: Camera3D) -> void:
	_buildings = buildings
	_camera = camera


## The node name of the nearest visible building at `screen_position` (in the
## island viewport, e.g. "PostOffice"), or "" for none.
func building_at(screen_position: Vector2) -> String:
	var origin := _camera.project_ray_origin(screen_position)
	var direction := _camera.project_ray_normal(screen_position)
	var best := ""
	var best_distance := INF
	for building: Node3D in _buildings.get_children():
		if not building.visible:
			continue
		# Turn the ray into the building's own space, where its box isn't rotated.
		var to_local := building.global_transform.affine_inverse()
		var hit = _box(building).intersects_ray(to_local * origin, to_local.basis * direction)
		if hit == null:
			continue
		var distance := origin.distance_to(building.global_transform * hit)
		if distance < best_distance:
			best_distance = distance
			best = str(building.name)
	return best


func _box(building: Node3D) -> AABB:
	if not _boxes.has(building):
		var box := AABB()
		var first := true
		for mesh: MeshInstance3D in building.find_children("*", "MeshInstance3D", true, false):
			# The mesh's box, moved from the mesh's space into the building's.
			var local := building.global_transform.affine_inverse() * mesh.global_transform * mesh.get_aabb()
			box = local if first else box.merge(local)
			first = false
		_boxes[building] = box
	return _boxes[building]
