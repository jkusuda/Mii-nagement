class_name IslandTerrain
## Builds the island's ground from an IslandLayout: a "field" image and a mesh.
##
## The field is a small image laid over the island (plus some sea around it).
## Each pixel stores:
##   R = how far that point is from the coast, in world units (+ on land, - at sea)
##   G = how much path is there (0 = grass, 1 = sand path)
## It's made at one pixel per tile, then scaled up with smooth (cubic)
## interpolation, which rounds off the tiles' square corners into a soft
## coastline. A little noise makes the coast wobble like a real one.
##
## The mesh is a grid of points over the field, each lifted to a height that
## depends on the field: flat grass, a soft rounded edge, a sloping beach, and
## the sea floor. The pond is pressed into the grass. terrain.gdshader colors it
## (grass, sand, paths) from the same field, and water.gdshader reads R to put
## the shallows and foam where the beach meets the sea.
##
## Godot concept: an ArrayMesh is a mesh built in code from lists of vertices,
## normals, and triangle indices, instead of being loaded from a model file.

const MARGIN := 4  # tiles of sea around the grid that the field also covers
const RES := 6  # field pixels per tile
const GRASS_Y := 0.0
const EDGE_DROP := 0.42  # the grass edge rounds down this far to the beach
const EDGE_WIDTH := 0.7  # over this many units
const BEACH_SLOPE := 0.38  # beach drop per unit outward
const BEACH_END := 3.0  # then the sea floor drops faster
const PATH_DIP := 0.06  # paths sit a touch lower than the grass
const POND_DEPTH := 0.75
const POND_RADIUS := 1.4  # how round the pond's corners are (world units)
## The water level in island_3d.tscn; the beach meets the sea where the ground crosses it.
const WATER_Y := -1.4

## Built fields and meshes, shared by every island with the same ground (the
## island view, the home screen's sea, and thumbnails can show the same island).
static var _cache := {}


## {"field": ImageTexture, "rect": Rect2 (the field's x/z area in world units), "mesh": ArrayMesh}
static func build(layout: IslandLayout) -> Dictionary:
	var key := layout.ground_key() + layout.seed_text
	if _cache.has(key):
		return _cache[key]
	var field := _make_field(layout)
	var size := Vector2(IslandLayout.GRID_W + 2 * MARGIN, IslandLayout.GRID_H + 2 * MARGIN) * IslandLayout.TILE
	var rect := Rect2(-size / 2.0, size)
	var result := {
		"field": ImageTexture.create_from_image(field), "rect": rect, "mesh": _make_mesh(field, rect, layout)
	}
	if _cache.size() > 24:
		_cache.clear()  # don't keep every island ever seen
	_cache[key] = result
	return result


## The pond as a rounded rectangle: {"center": Vector2 (x, z), "half": Vector2}, or {}.
static func pond_shape(layout: IslandLayout) -> Dictionary:
	if layout.pond.is_empty():
		return {}
	var p := layout.pond
	var low := IslandLayout.tile_to_world(Vector2(p["x"], p["y"]))
	var high := IslandLayout.tile_to_world(Vector2(p["x"] + p["w"], p["y"] + p["h"]))
	var inset := 0.25  # a little grass between the pond and the paths around it
	return {
		"center": Vector2((low.x + high.x) / 2.0, (low.z + high.z) / 2.0),
		"half": Vector2((high.x - low.x) / 2.0 - inset, (high.z - low.z) / 2.0 - inset)
	}


## Signed distance to the pond's rounded rectangle: + inside, - outside.
static func pond_distance(shape: Dictionary, x: float, z: float) -> float:
	if shape.is_empty():
		return -100.0
	var half: Vector2 = shape["half"]
	var center: Vector2 = shape["center"]
	var radius := minf(POND_RADIUS, minf(half.x, half.y))
	var q := (Vector2(x, z) - center).abs() - half + Vector2(radius, radius)
	return -(Vector2(maxf(q.x, 0.0), maxf(q.y, 0.0)).length() + minf(maxf(q.x, q.y), 0.0) - radius)


static func _make_field(layout: IslandLayout) -> Image:
	var w := IslandLayout.GRID_W + 2 * MARGIN
	var h := IslandLayout.GRID_H + 2 * MARGIN
	# Which cells are land, and the coast cells on each side (only those matter
	# for distances, so we don't compare every cell with every other cell).
	var is_land := {}
	for r in h:
		for c in w:
			if layout.is_land(Vector2i(c - MARGIN, r - MARGIN)):
				is_land[Vector2i(c, r)] = true
	var land_edge: Array[Vector2] = []
	var sea_edge: Array[Vector2] = []
	for r in h:
		for c in w:
			var t := Vector2i(c, r)
			var mine := is_land.has(t)
			for n in [t + Vector2i.RIGHT, t + Vector2i.LEFT, t + Vector2i.DOWN, t + Vector2i.UP]:
				if is_land.has(n) != mine:
					(land_edge if mine else sea_edge).append(Vector2(t))
					break

	var coarse := Image.create(w, h, false, Image.FORMAT_RGF)
	for r in h:
		for c in w:
			var t := Vector2(c, r)
			var mine := is_land.has(Vector2i(c, r))
			var nearest := INF
			for e in sea_edge if mine else land_edge:
				nearest = minf(nearest, t.distance_squared_to(e))
			# Halfway between a land cell and a sea cell is the coast: distance 0.
			var d := (sqrt(nearest) - 0.5) if nearest < INF else float(w)
			var tiles := d if mine else -d
			var path := 1.0 if layout.path_tiles.has(Vector2i(c - MARGIN, r - MARGIN)) else 0.0
			coarse.set_pixel(c, r, Color(tiles * IslandLayout.TILE, path, 0.0))

	# Scale up smoothly: square tiles become a rounded coastline and rounded paths.
	coarse.resize(w * RES, h * RES, Image.INTERPOLATE_CUBIC)

	# A doorstep: a short strip of path from each door up to the building, since
	# the models are a little smaller than their lots.
	var doorsteps: Array[Rect2] = []
	for b in layout.buildings:
		var door := Vector2(IslandLayout.door_tile(b)) + Vector2(0.5, 0.5)  # tile units
		var inward: Vector2 = [Vector2.UP, Vector2.LEFT, Vector2.DOWN, Vector2.RIGHT][int(b["facing"])]  # door -> building
		var tip := door + inward * 1.05
		var across := Vector2(absf(inward.y), absf(inward.x)) * 0.32
		doorsteps.append(Rect2(door.min(tip) - across, (tip - door).abs() + across * 2.0))

	# A gentle wobble, so the coast looks natural (the same island always wobbles the same way).
	var noise := FastNoiseLite.new()
	noise.seed = hash(layout.seed_text)
	noise.frequency = 0.08
	for y in coarse.get_height():
		for x in coarse.get_width():
			var color := coarse.get_pixel(x, y)
			color.r += noise.get_noise_2d(x / float(RES) * IslandLayout.TILE, y / float(RES) * IslandLayout.TILE) * 0.7
			var at := Vector2(x + 0.5, y + 0.5) / RES - Vector2(MARGIN, MARGIN)  # tile units
			for step in doorsteps:
				if step.has_point(at):
					color.g = maxf(color.g, 1.0)
			coarse.set_pixel(x, y, color)
	return coarse


static func _make_mesh(field: Image, rect: Rect2, layout: IslandLayout) -> ArrayMesh:
	var w := field.get_width()
	var h := field.get_height()
	var step := rect.size.x / w
	var pond := pond_shape(layout)
	var heights := PackedFloat32Array()
	heights.resize(w * h)
	for y in h:
		for x in w:
			var wx := rect.position.x + (x + 0.5) * step
			var wz := rect.position.y + (y + 0.5) * step
			var px := field.get_pixel(x, y)
			heights[y * w + x] = _height(px.r, px.g, pond_distance(pond, wx, wz))

	var verts := PackedVector3Array()
	var normals := PackedVector3Array()
	verts.resize(w * h)
	normals.resize(w * h)
	for y in h:
		for x in w:
			var i := y * w + x
			verts[i] = Vector3(rect.position.x + (x + 0.5) * step, heights[i], rect.position.y + (y + 0.5) * step)
			var left := heights[y * w + maxi(x - 1, 0)]
			var right := heights[y * w + mini(x + 1, w - 1)]
			var up := heights[maxi(y - 1, 0) * w + x]
			var down := heights[mini(y + 1, h - 1) * w + x]
			normals[i] = Vector3(left - right, 2.0 * step, up - down).normalized()

	var indices := PackedInt32Array()
	for y in h - 1:
		for x in w - 1:
			var a := y * w + x
			# Skip the deep sea floor nobody sees.
			if maxf(maxf(heights[a], heights[a + 1]), maxf(heights[a + w], heights[a + w + 1])) < WATER_Y - 1.5:
				continue
			indices.append_array([a, a + 1, a + w, a + 1, a + w + 1, a + w])

	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh


## Ground height from the distance to the coast (d), path amount, and distance into the pond.
static func _height(d: float, path: float, pond_d: float) -> float:
	var y: float
	if d >= 0.0:
		y = GRASS_Y - EDGE_DROP * (1.0 - smoothstep(0.0, EDGE_WIDTH, d))
		y -= PATH_DIP * smoothstep(0.35, 0.65, path) * smoothstep(0.5, 1.2, d)
	else:
		var out := -d
		y = GRASS_Y - EDGE_DROP - BEACH_SLOPE * minf(out, BEACH_END) - 0.9 * maxf(out - BEACH_END, 0.0)
	if pond_d > -0.6:
		y = lerpf(y, GRASS_Y - POND_DEPTH, smoothstep(-0.6, 0.7, pond_d))
	return maxf(y, -5.0)
