class_name TalkRoom
extends Control
## The room behind a resident in the talk popup, Tomodachi-style: a wall in the
## building's colour with soft doodles on it (circles, triangles, squiggles,
## bars), a baseboard, a wooden floor, and a shadow where the Mii stands.
##
## Godot concept: a Control can paint itself in `_draw()` with draw_rect,
## draw_arc, draw_polyline... Godot calls it once and keeps the result until
## queue_redraw() asks for a new one (here: a new colour, or a new size).

const FLOOR_AT := 0.64  # where the floor starts, as a fraction of the height
const FEET_AT := 0.8  # where the Mii's feet land (MiiPreview.ROOM_SHOT)
const WOOD := Color("D8914C")
const PATTERN_SEED := 7  # the same doodles every time

var color := Color("F4A7B9"):
	set(value):
		color = value
		queue_redraw()


func _ready() -> void:
	resized.connect(queue_redraw)
	mouse_filter = Control.MOUSE_FILTER_IGNORE


func _draw() -> void:
	var w := size.x
	var h := size.y
	var floor_y := h * FLOOR_AT
	var wall := color.lerp(Color.WHITE, 0.45)
	draw_rect(Rect2(0, 0, w, floor_y), wall)
	_draw_doodles(Rect2(0, 0, w, floor_y), wall.lerp(Color.WHITE, 0.5))
	_draw_floor(floor_y, w, h)
	draw_rect(Rect2(0, floor_y - 7, w, 9), wall.darkened(0.12))  # baseboard
	# The Mii's shadow: a squashed circle (draw_set_transform scales what's drawn next).
	draw_set_transform(Vector2(w / 2.0, h * FEET_AT), 0.0, Vector2(1.0, 0.26))
	draw_circle(Vector2.ZERO, w * 0.17, Color(0.2, 0.1, 0.05, 0.22))
	draw_set_transform(Vector2.ZERO)


## Doodles on a loose grid, each nudged and turned a little.
func _draw_doodles(area: Rect2, ink: Color) -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = PATTERN_SEED
	var cols := 4
	var rows := 4
	var cell := Vector2(area.size.x / cols, area.size.y / rows)
	var s := minf(cell.x, cell.y) * 0.3
	var line := maxf(3.0, s * 0.18)
	for row in rows:
		for col in cols:
			var jitter := Vector2(rng.randf_range(-0.25, 0.25), rng.randf_range(-0.25, 0.25))
			var p := area.position + cell * (Vector2(col, row) + Vector2(0.5, 0.5) + jitter)
			var turn := rng.randf() * TAU
			match rng.randi() % 4:
				0:  # a ring
					draw_arc(p, s * 0.45, 0.0, TAU, 28, ink, line, true)
				1:  # a triangle
					var tri := PackedVector2Array()
					for i in 4:
						tri.append(p + Vector2(0, -s * 0.6).rotated(turn + i * TAU / 3.0))
					draw_polyline(tri, ink, line, true)
				2:  # a squiggle
					var wave := PackedVector2Array()
					for i in 14:
						var t := i / 13.0
						wave.append(p + Vector2((t - 0.5) * s * 2.4, sin(t * TAU * 1.3) * s * 0.35).rotated(turn))
					draw_polyline(wave, ink, line, true)
				_:  # a rounded bar
					var d := Vector2(s * 0.7, 0).rotated(turn)
					draw_line(p - d, p + d, ink, line * 1.8, true)
					draw_circle(p - d, line * 0.9, ink)
					draw_circle(p + d, line * 0.9, ink)


## Planks that get taller toward the front (a hint of perspective), with seams.
func _draw_floor(top: float, w: float, h: float) -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = PATTERN_SEED
	var y := top
	var plank := (h - top) * 0.11
	var row := 0
	while y < h:
		var shade := WOOD.darkened(rng.randf_range(0.0, 0.1)).lightened(0.04 * (row % 2))
		draw_rect(Rect2(0, y, w, plank + 1), shade)
		draw_line(Vector2(0, y), Vector2(w, y), WOOD.darkened(0.25), 2.0)
		var offset := rng.randf_range(0.0, w / 3.0)
		var x := offset
		while x < w:
			draw_line(Vector2(x, y), Vector2(x, y + plank), WOOD.darkened(0.2), 2.0)
			x += w / 2.2
		y += plank
		plank *= 1.22
		row += 1
