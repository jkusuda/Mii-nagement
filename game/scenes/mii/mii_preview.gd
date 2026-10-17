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
## Camera spots: the whole Mii, or close on the face (while picking face parts).
const BODY_SHOT := [Vector3(0, 1.1, 3.8), -0.07]
const FACE_SHOT := [Vector3(0, 1.3, 3.0), -0.02]
## Further back, with room above the hair and floor below the feet (the talk popup's room;
## the feet land at TalkRoom.FEET_AT).
const ROOM_SHOT := [Vector3(0, 1.15, 5.4), -0.05]

## False = no drag-to-spin (the talk popup: the Mii just stands and sways).
var spin_enabled := true

var _dragging := false
var _anim_index := 0

var _camera_tween: Tween
var _hop_tween: Tween

@onready var turntable: Node3D = %Turntable
@onready var mii: Mii = %Mii
@onready var camera: Camera3D = $SubViewport/Camera3D


func set_color(color: Color) -> void:
	mii.color = color


func set_appearance(look: Dictionary) -> void:
	mii.appearance = look


## Move the camera close to the face (true) or back to the whole Mii (false).
## Godot concept: a Tween animates properties over time; tween_property() glides
## one property to a new value, and set_parallel() runs several at once.
func focus_face(on: bool) -> void:
	var shot: Array = FACE_SHOT if on else BODY_SHOT
	if _camera_tween:
		_camera_tween.kill()
	_camera_tween = create_tween().set_parallel().set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	_camera_tween.tween_property(camera, "position", shot[0], 0.35)
	_camera_tween.tween_property(camera, "rotation:x", shot[1], 0.35)


## Jump the camera to one of the shots above (no glide).
func use_shot(shot: Array) -> void:
	if _camera_tween:
		_camera_tween.kill()
	camera.position = shot[0]
	camera.rotation.x = shot[1]


## Show the next animation (idle -> walk -> run -> idle ...).
func next_animation() -> void:
	var names := Mii.ANIMATIONS.keys()
	_anim_index = (_anim_index + 1) % names.size()
	mii.play(names[_anim_index])


## A little hop (`times` in a row), like the residents' emotes on the island.
func hop(times := 1) -> void:
	if _hop_tween:
		_hop_tween.kill()
	_hop_tween = create_tween()
	for i in times:
		_hop_tween.tween_property(mii, "position:y", 0.22, 0.14).set_ease(Tween.EASE_OUT)
		_hop_tween.tween_property(mii, "position:y", 0.0, 0.14).set_ease(Tween.EASE_IN)


func _process(delta: float) -> void:
	if not _dragging:
		# Ease back toward facing the camera, with a gentle sway.
		var target := sin(Time.get_ticks_msec() / 1000.0 * IDLE_SPIN_SPEED) * 0.35
		turntable.rotation.y = lerp_angle(turntable.rotation.y, target, delta * 2.0)


## Godot concept: _gui_input gets mouse events that land on this Control.
func _gui_input(event: InputEvent) -> void:
	if not spin_enabled:
		return
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		_dragging = event.pressed
	elif event is InputEventMouseMotion and _dragging:
		turntable.rotation.y += event.relative.x * SPIN_PER_PIXEL
