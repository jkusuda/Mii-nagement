class_name CameraRig
extends Node3D
## Camera rig: mouse-wheel / trackpad / button zoom, and click-and-drag panning.
## A click that doesn't drag is reported as `clicked` (game.gd uses it to open
## buildings), and focus_on() glides the camera to a spot, unfocus() back.
##
## The rig sits on the ground in the middle of the island, and the Camera3D is
## its child, pulled back and tilted down. Because a child moves with its
## parent, we can:
##   - zoom by moving the camera closer along the direction it's looking, and
##   - pan by sliding the whole rig across the ground.

signal zoom_changed(percent: int)
## A left click that didn't drag, at this point in the island's viewport.
signal clicked(screen_position: Vector2)
## The mouse moved over the island without dragging (for a hover cursor).
signal hovered(screen_position: Vector2)

const MIN_ZOOM := 0.6
const MAX_ZOOM := 2.0
const ZOOM_STEP := 0.1
const SMOOTHING := 12.0  # higher = snappier, lower = floatier
const PAN_LIMIT := Vector2(14.0, 10.0)  # how far (x, z) you can drag from the start
const CLICK_SLOP := 6.0  # pixels the mouse may move and still count as a click

var zoom := 1.0  # 1.0 = 100%

var _target_zoom := 1.0
var _home_offset := Vector3.ZERO  # the camera's position at 100% zoom
var _home_position := Vector3.ZERO  # the rig's starting position
var _target_position := Vector3.ZERO
var _dragging := false
var _drag_distance := 0.0
var _before_focus := {}  # zoom + position to go back to after focus_on()

@onready var camera: Camera3D = $Camera3D


func _ready() -> void:
	_home_offset = camera.position
	_home_position = position
	_target_position = position


## Zoom in (positive) or out (negative). 0.1 = 10 percentage points.
func zoom_by(amount: float) -> void:
	_target_zoom = clampf(snappedf(_target_zoom + amount, 0.01), MIN_ZOOM, MAX_ZOOM)
	zoom_changed.emit(roundi(_target_zoom * 100.0))


## Godot concept: _unhandled_input() receives input that no UI element used.
## So clicks on buttons never pan the island, but clicks on the island do.
func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
			zoom_by(ZOOM_STEP)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
			zoom_by(-ZOOM_STEP)
		elif event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_drag_distance = 0.0
			elif _dragging and _drag_distance < CLICK_SLOP:
				clicked.emit(event.position)
			_dragging = event.pressed
	elif event is InputEventMouseMotion and _dragging:
		_drag_distance += event.relative.length()
		_pan(event.relative)
	elif event is InputEventMouseMotion:
		hovered.emit(event.position)
	elif event is InputEventMagnifyGesture:  # trackpad pinch
		zoom_by((event.factor - 1.0) * 2.0)
	elif event is InputEventPanGesture:  # trackpad two-finger scroll
		zoom_by(-event.delta.y * 0.05)


## Glide to `point` (on the ground) at `to_zoom`. `shift` (-0.5..0.5, a fraction
## of the view's width) puts the point left (negative) or right of the middle,
## so a panel can sit beside it. unfocus() goes back to where you were.
func focus_on(point: Vector3, to_zoom: float, shift := 0.0) -> void:
	if _before_focus.is_empty():
		_before_focus = {"zoom": _target_zoom, "position": _target_position}
	_target_zoom = clampf(to_zoom, MIN_ZOOM, MAX_ZOOM)
	var view_size := get_viewport().get_visible_rect().size
	var view_width := _view_height(_target_zoom) * view_size.x / view_size.y
	_target_position = Vector3(point.x - shift * view_width, position.y, point.z)
	zoom_changed.emit(roundi(_target_zoom * 100.0))


func unfocus() -> void:
	if _before_focus.is_empty():
		return
	_target_zoom = _before_focus["zoom"]
	_target_position = _before_focus["position"]
	_before_focus = {}
	zoom_changed.emit(roundi(_target_zoom * 100.0))


## How many meters of ground the view covers top to bottom (at the focus point).
func _view_height(at_zoom: float) -> float:
	return 2.0 * (_home_offset / at_zoom).length() * tan(deg_to_rad(camera.fov / 2.0))


func _pan(screen_delta: Vector2) -> void:
	# How many meters on the ground one screen pixel covers right now.
	var meters_per_pixel := _view_height(_target_zoom) / get_viewport().get_visible_rect().size.y
	# The ground is tilted away from us, so vertical mouse movement covers more ground.
	var tilt := 1.0 / sin(-camera.rotation.x)

	# Grab-and-drag: move the mouse right and the island follows it right.
	_target_position.x -= screen_delta.x * meters_per_pixel
	_target_position.z -= screen_delta.y * meters_per_pixel * tilt
	_target_position.x = clampf(_target_position.x, _home_position.x - PAN_LIMIT.x, _home_position.x + PAN_LIMIT.x)
	_target_position.z = clampf(_target_position.z, _home_position.z - PAN_LIMIT.y, _home_position.z + PAN_LIMIT.y)


func _process(delta: float) -> void:
	# Ease toward the targets. Using exp() keeps the speed the same at any frame rate.
	var t := 1.0 - exp(-SMOOTHING * delta)
	zoom = lerpf(zoom, _target_zoom, t)
	camera.position = _home_offset / zoom
	position = position.lerp(_target_position, t)
