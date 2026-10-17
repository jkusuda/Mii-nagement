@tool
extends Button
## A flat button with a dashed, rounded outline (the "Invite a resident" tile).
## StyleBoxFlat can't draw dashes, so we draw the outline ourselves.
##
## Godot concept: _draw() is where a Control paints itself with draw_* calls.
## Godot calls it when the node first shows and whenever queue_redraw() is called.

const COLOR := Color("C9CDD8")
const RADIUS := 16.0
const WIDTH := 2.0
const DASH := 7.0


func _ready() -> void:
	resized.connect(queue_redraw)
	mouse_entered.connect(queue_redraw)
	mouse_exited.connect(queue_redraw)


func _draw() -> void:
	var r := Rect2(Vector2.ONE * WIDTH, size - Vector2.ONE * WIDTH * 2)
	if is_hovered():
		draw_rect(r, Color(1, 1, 1, 0.6))
	var color := COLOR.darkened(0.2) if is_hovered() else COLOR
	var left := r.position.x
	var top := r.position.y
	var right := r.end.x
	var bottom := r.end.y
	# Four straight dashed sides...
	draw_dashed_line(Vector2(left + RADIUS, top), Vector2(right - RADIUS, top), color, WIDTH, DASH)
	draw_dashed_line(Vector2(left + RADIUS, bottom), Vector2(right - RADIUS, bottom), color, WIDTH, DASH)
	draw_dashed_line(Vector2(left, top + RADIUS), Vector2(left, bottom - RADIUS), color, WIDTH, DASH)
	draw_dashed_line(Vector2(right, top + RADIUS), Vector2(right, bottom - RADIUS), color, WIDTH, DASH)
	# ...and four solid rounded corners (angles in radians, clockwise from +x).
	draw_arc(Vector2(left + RADIUS, top + RADIUS), RADIUS, PI, PI * 1.5, 8, color, WIDTH, true)
	draw_arc(Vector2(right - RADIUS, top + RADIUS), RADIUS, PI * 1.5, TAU, 8, color, WIDTH, true)
	draw_arc(Vector2(right - RADIUS, bottom - RADIUS), RADIUS, 0, PI * 0.5, 8, color, WIDTH, true)
	draw_arc(Vector2(left + RADIUS, bottom - RADIUS), RADIUS, PI * 0.5, PI, 8, color, WIDTH, true)
