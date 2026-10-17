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
	var tiles := layout.walk_tiles(layout.nearest_path_tile(from), layout.nearest_path_tile(to))
	var points: Array[Vector3] = []
	for i in tiles.size():
		# Keep only the corners: a tile where the direction changes.
		if i > 0 and i < tiles.size() - 1 and (tiles[i] - tiles[i - 1]) == (tiles[i + 1] - tiles[i]):
			continue
		points.append(_center(tiles[i]))
	if points.is_empty():
		points.append(Vector3(to.x, PATH_Y, to.z))
	elif Vector2(to.x, to.z).distance_to(Vector2(points[-1].x, points[-1].z)) < IslandLayout.TILE:
		points[-1] = Vector3(to.x, PATH_Y, to.z)  # a stop slightly off the tile's middle
	return points


## A random point on the paths (the middle of a random path tile), for wandering.
static func random_point(layout: IslandLayout, rng: RandomNumberGenerator) -> Vector3:
	var tiles: Array = layout.path_tiles.keys()
	if tiles.is_empty():
		return Vector3(0, PATH_Y, 0)
	return _center(tiles[rng.randi() % tiles.size()])


static func _center(t: Vector2i) -> Vector3:
	var p := IslandLayout.tile_center(t)
	return Vector3(p.x, PATH_Y, p.z)
