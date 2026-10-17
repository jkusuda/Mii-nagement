class_name IslandPaths
## The sand footpath: a rectangle loop around the pond. Residents walk on it.
##
## The numbers were measured from base_island.glb as placed in island_3d.tscn
## (scaled ×20): the path's centerline in x / z, and its height. If you move or
## rescale the island, measure again.
##
## A point on the loop can be described by one number, `s`: how far along the
## loop it is, going clockwise (seen from above) from the top-left corner:
##
##     s = 0 ──────── top ───────► s = W
##       ▲                           │
##      left                       right
##       │                           ▼
##     s = 2W+H ◄──── bottom ──── s = W+H

const LOOP_MIN := Vector2(-9.9, -4.2)  # (x, z) of the top-left corner
const LOOP_MAX := Vector2(9.9, 5.3)    # (x, z) of the bottom-right corner
const PATH_Y := -0.23                  # the path's height

const W := LOOP_MAX.x - LOOP_MIN.x
const H := LOOP_MAX.y - LOOP_MIN.y
const LENGTH := 2.0 * (W + H)
const CORNERS := [0.0, W, W + H, W + W + H]  # the s of each corner


## The point on the loop closest to `p` (for example, a building's front door).
static func nearest(p: Vector3) -> Vector3:
	var x := clampf(p.x, LOOP_MIN.x, LOOP_MAX.x)
	var z := clampf(p.z, LOOP_MIN.y, LOOP_MAX.y)
	# Inside the loop: push out to the closest edge.
	var to_edges := [x - LOOP_MIN.x, LOOP_MAX.x - x, z - LOOP_MIN.y, LOOP_MAX.y - z]
	match to_edges.find(to_edges.min()):
		0: x = LOOP_MIN.x
		1: x = LOOP_MAX.x
		2: z = LOOP_MIN.y
		3: z = LOOP_MAX.y
	return Vector3(x, PATH_Y, z)


## Points to walk through to get from `from` to `to` along the loop, taking the
## shorter way round. Both are snapped onto the loop first. The list starts at
## `from` and ends at `to`, with the corners in between.
static func route(from: Vector3, to: Vector3) -> Array[Vector3]:
	var a := _s_of(nearest(from))
	var b := _s_of(nearest(to))
	var forward := fposmod(b - a, LENGTH)
	var clockwise := forward <= LENGTH / 2.0
	var total := forward if clockwise else LENGTH - forward

	# Which corners do we pass, and how far along the way is each one?
	var passed := []
	for c in CORNERS:
		var dist := fposmod(c - a, LENGTH) if clockwise else fposmod(a - c, LENGTH)
		if dist > 0.01 and dist < total - 0.01:
			passed.append([dist, c])
	passed.sort_custom(func(p, q): return p[0] < q[0])

	var points: Array[Vector3] = [_point_at(a)]
	for p in passed:
		points.append(_point_at(p[1]))
	points.append(_point_at(b))
	return points


static func _s_of(p: Vector3) -> float:
	# Find which side the point is on (the closest one), then measure along it.
	var sides := [
		absf(p.z - LOOP_MIN.y), absf(p.x - LOOP_MAX.x), absf(p.z - LOOP_MAX.y), absf(p.x - LOOP_MIN.x),
	]
	match sides.find(sides.min()):
		0: return p.x - LOOP_MIN.x                   # top, going right
		1: return W + (p.z - LOOP_MIN.y)             # right, going down
		2: return W + H + (LOOP_MAX.x - p.x)         # bottom, going left
		_: return W + W + H + (LOOP_MAX.y - p.z)     # left, going up


static func _point_at(s: float) -> Vector3:
	s = fposmod(s, LENGTH)
	if s <= W:
		return Vector3(LOOP_MIN.x + s, PATH_Y, LOOP_MIN.y)
	if s <= W + H:
		return Vector3(LOOP_MAX.x, PATH_Y, LOOP_MIN.y + (s - W))
	if s <= W + W + H:
		return Vector3(LOOP_MAX.x - (s - W - H), PATH_Y, LOOP_MAX.y)
	return Vector3(LOOP_MIN.x, PATH_Y, LOOP_MAX.y - (s - W - W - H))
