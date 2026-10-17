class_name TalkRoom
extends Control
## The room behind a resident in the talk popup, Tomodachi-style: a back wall and two
## side walls with moulded panels, a baseboard, and a tiled floor drawn in perspective,
## all in a pale shade of the building's colour. A soft shadow marks where the Mii stands.
##
## Godot concept: a Control can paint itself in `_draw()` with draw_rect,
## draw_colored_polygon, draw_line... Godot calls it once and keeps the result until
## queue_redraw() asks for a new one (here: a new colour, a new size, or the feet moved).

const BACK_LEFT := 0.15  # where the back wall starts and ends, as fractions of the width
const BACK_RIGHT := 0.85
const FLOOR_AT := 0.58  # where the back wall meets the floor, as a fraction of the height
const FLOOR := Color("EFE6D6")

## The building's colour; the walls are a pale shade of it.
var color := Color("F4A7B9"):
	set(value):
		color = value
		queue_redraw()

## Where the Mii's feet are (in this Control's coordinates), for the shadow. NAN = no shadow.
var feet := Vector2(NAN, NAN):
	set(value):
		feet = value
		queue_redraw()


func _ready() -> void:
	resized.connect(queue_redraw)
	mouse_filter = Control.MOUSE_FILTER_IGNORE


func _draw() -> void:
	var w := size.x
	var h := size.y
	var x0 := w * BACK_LEFT
	var x1 := w * BACK_RIGHT
	var fy := h * FLOOR_AT
	var wall := color.lerp(Color.WHITE, 0.74)
	var side := wall.darkened(0.07)
	var trim := wall.darkened(0.16)

	# Floor first, then the walls over its edges.
	var floor_tint := FLOOR.lerp(color, 0.06)
	draw_colored_polygon(
		PackedVector2Array([Vector2(x0, fy), Vector2(x1, fy), Vector2(w, h), Vector2(0, h)]), floor_tint
	)
	_draw_floor_tiles(x0, x1, fy, w, h, floor_tint.darkened(0.09))

	draw_rect(Rect2(x0, 0, x1 - x0, fy), wall)
	draw_colored_polygon(PackedVector2Array([Vector2(0, 0), Vector2(x0, 0), Vector2(x0, fy), Vector2(0, h)]), side)
	draw_colored_polygon(PackedVector2Array([Vector2(x1, 0), Vector2(w, 0), Vector2(w, h), Vector2(x1, fy)]), side)
	# The room's corners.
	draw_line(Vector2(x0, 0), Vector2(x0, fy), trim, 2.0, true)
	draw_line(Vector2(x1, 0), Vector2(x1, fy), trim, 2.0, true)

	_draw_back_panels(x0, x1, fy, h, trim)
	_draw_side_panels(x0, fy, w, h, trim, false)
	_draw_side_panels(x1, fy, w, h, trim, true)

	# Baseboards: along the back wall, and down each side wall to the front.
	var board := wall.darkened(0.1)
	draw_rect(Rect2(x0, fy - 10, x1 - x0, 10), board)
	draw_line(Vector2(x0, fy - 5), Vector2(0, h - 14), board, 12.0, true)
	draw_line(Vector2(x1, fy - 5), Vector2(w, h - 14), board, 12.0, true)

	if not is_nan(feet.x):
		# A squashed circle: draw_set_transform scales what's drawn next.
		draw_set_transform(feet, 0.0, Vector2(1.0, 0.24))
		draw_circle(Vector2.ZERO, 92.0, Color(0.25, 0.18, 0.1, 0.18))
		draw_set_transform(Vector2.ZERO)


## Three tall panels with an inner moulding, and a picture rail near the top.
func _draw_back_panels(x0: float, x1: float, fy: float, h: float, trim: Color) -> void:
	draw_line(Vector2(x0, h * 0.06), Vector2(x1, h * 0.06), trim, 2.0, true)
	var gap := (x1 - x0) * 0.04
	var width := ((x1 - x0) - gap * 4) / 3.0
	for i in 3:
		var left := x0 + gap + i * (width + gap)
		var outer := Rect2(left, h * 0.11, width, fy - h * 0.17)
		draw_rect(outer, trim, false, 2.0)
		draw_rect(outer.grow(-12.0), trim.lightened(0.25), false, 1.5)


## Two panels on a side wall, skewed to follow it. `right` = the right-hand wall.
func _draw_side_panels(edge: float, fy: float, w: float, h: float, trim: Color, right: bool) -> void:
	# A point on the wall: u = 0 at the front (screen edge), 1 at the back corner; v = 0 top, 1 bottom.
	var at := func(u: float, v: float) -> Vector2:
		var x := lerpf(w if right else 0.0, edge, u)
		var bottom := lerpf(h, fy, u)
		var top := lerpf(0.0, h * 0.06, u)
		return Vector2(x, lerpf(top + (bottom - top) * 0.12, bottom - (bottom - top) * 0.08, v))
	for span in [[0.12, 0.46], [0.56, 0.9]]:
		var quad := PackedVector2Array([at.call(span[0], 0.0), at.call(span[1], 0.0), at.call(span[1], 1.0)])
		quad.append(at.call(span[0], 1.0))
		quad.append(quad[0])
		draw_polyline(quad, trim, 2.0, true)


## Tiles in perspective: seams from the back edge to the front fan out, and rows get
## deeper toward the viewer.
func _draw_floor_tiles(x0: float, x1: float, fy: float, w: float, h: float, seam: Color) -> void:
	var columns := 8
	for i in columns + 1:
		var t := float(i) / columns
		draw_line(Vector2(lerpf(x0, x1, t), fy), Vector2(lerpf(0.0, w, t), h), seam, 1.5, true)
	var rows := 6
	for i in range(1, rows + 1):
		var t := pow(float(i) / rows, 1.7)
		var y := lerpf(fy, h, t)
		draw_line(Vector2(lerpf(x0, 0.0, t), y), Vector2(lerpf(x1, w, t), y), seam, 1.5, true)
