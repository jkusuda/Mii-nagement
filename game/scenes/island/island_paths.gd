class_name IslandPaths
## Walking on the island's sand paths.
##
## The paths themselves are tiles worked out by IslandLayout (they join the
## buildings' doors). This turns them into points a resident walks through:
## along the middle of the path tiles, turning only at corners.

const PATH_Y := -0.05  # the path's height (a touch below the grass)


## The point on the paths closest to `p` (for example, a building's front door).
static func nearest(layout: IslandLayout, p: Vector3) -> Vector3:
	var t := layout.nearest_path_tile(p)
	return _center(t)


## Points to walk through to get from `from` to `to` along the paths, taking the
## shortest way. The list starts near `from` and ends at `to` (when `to` is on a
## path), with the corners in between.
static func route(layout: IslandLayout, from: Vector3, to: Vector3) -> Array[Vector3]:
	# Someone strolling on the grass first gets back onto the path, round the obstacles.
	if not layout.path_tiles.has(IslandLayout.world_to_tile(from)):
		var back := nearest(layout, from)
		var on_grass := layout.roam_route(from, back)
		on_grass.append_array(route(layout, back, to))
		return on_grass
	var tiles := layout.walk_tiles(layout.nearest_path_tile(from), layout.nearest_path_tile(to))
	var points: Array[Vector3] = []
	for i in tiles.size():
		# Keep only the corners: a tile where the direction changes.
		if i > 0 and i < tiles.size() - 1 and (tiles[i] - tiles[i - 1]) == (tiles[i + 1] - tiles[i]):
			continue
		points.append(_center(tiles[i]))
	# Already partway along the first stretch (stopped mid-walk)? Don't step back
	# to the first tile's middle, just carry on.
	if points.size() > 1 and _between(from, points[0], points[1]):
		points.remove_at(0)
	if points.is_empty():
		points.append(Vector3(to.x, PATH_Y, to.z))
	elif IslandLayout.world_to_tile(to) == tiles[-1]:
		points[-1] = Vector3(to.x, PATH_Y, to.z)  # a stop slightly off the tile's middle
	return points


## Is `p` on the straight bit of path from `a` to `b` (give or take a little)?
static func _between(p: Vector3, a: Vector3, b: Vector3) -> bool:
	var p2 := Vector2(p.x, p.z)
	var a2 := Vector2(a.x, a.z)
	var b2 := Vector2(b.x, b.z)
	var closest := Geometry2D.get_closest_point_to_segment(p2, a2, b2)
	return closest != a2 and p2.distance_to(closest) < 0.1


static func _center(t: Vector2i) -> Vector3:
	var p := IslandLayout.tile_center(t)
	return Vector3(p.x, PATH_Y, p.z)
