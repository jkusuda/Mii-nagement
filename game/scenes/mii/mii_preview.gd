class_name MiiPreview
extends SubViewportContainer
## A little 3D stage that shows one Mii, for the resident creator.
## Drag sideways to spin the Mii around.
##
## Godot concept: `own_world_3d` on the SubViewport gives this stage its own 3D
## world. Without it, every SubViewport shares one world, and this Mii would be
## standing in the middle of the island (and the island's sun would light it).

const SPIN_PER_PIXEL := 0.01
const IDLE_SPIN_SPEED := 0.25  # radians per second while nobody is dragging

var _dragging := false
var _anim_index := 0

@onready var turntable: Node3D = %Turntable
@onready var mii: Mii = %Mii


func set_color(color: Color) -> void:
	mii.color = color


## Show the next animation (idle -> walk -> run -> idle ...).
func next_animation() -> void:
	var names := Mii.ANIMATIONS.keys()
	_anim_index = (_anim_index + 1) % names.size()
	mii.play(names[_anim_index])


func _process(delta: float) -> void:
	if not _dragging:
		# Ease back toward facing the camera, with a gentle sway.
		var target := sin(Time.get_ticks_msec() / 1000.0 * IDLE_SPIN_SPEED) * 0.35
		turntable.rotation.y = lerp_angle(turntable.rotation.y, target, delta * 2.0)


## Godot concept: _gui_input gets mouse events that land on this Control.
func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		_dragging = event.pressed
	elif event is InputEventMouseMotion and _dragging:
		turntable.rotation.y += event.relative.x * SPIN_PER_PIXEL
