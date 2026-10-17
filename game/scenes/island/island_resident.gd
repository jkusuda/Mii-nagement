class_name IslandResident
extends Node3D
## One resident on the 3D island: their Mii, a name tag, a bubble icon when they
## need you, and "z z" when they sleep.
##
## island_residents.gd decides *where* they go; this script only knows how to
## walk along a list of points and how to look for each state.

signal arrived

const MII_SCALE := 1.6
const WALK_SPEED := 3.5    # units per second
const RUN_SPEED := 7.0
const LONGEST_WALK := 3.5  # seconds: on long routes, run instead so we keep up with the server

var resident_id := ""
var state := "idle"
var is_walking := false

var _facing := 0.0  # radians around y, 0 = toward the camera
var _work_tween: Tween
var _walk_tween: Tween

@onready var mii: Mii = %Mii
@onready var name_tag: Label3D = %NameTag
@onready var bubble: Node3D = %Bubble
@onready var bubble_icon: Sprite3D = %BubbleIcon
@onready var zz: Label3D = %Zz


func _ready() -> void:
	mii.scale = Vector3.ONE * MII_SCALE


## Show the resident's details (a dictionary from Net.island). `open_bubble` is
## their waiting bubble, or {} when they don't need you.
func show_resident(resident: Dictionary, open_bubble: Dictionary) -> void:
	mii.color = Color(str(resident.get("color", "#FFFFFF")))
	var look := MiiParts.for_resident(resident)
	if look != mii.appearance:
		mii.appearance = look
	name_tag.text = str(resident.get("name", "?"))
	bubble.visible = not open_bubble.is_empty()
	if bubble.visible:
		var kind := str(open_bubble["kind"])
		bubble_icon.texture = ResidentData.BUBBLE_TAGS.get(kind, ResidentData.BUBBLE_TAGS["confused"])[1]
		bubble_icon.modulate = BubbleCard.KIND_COLORS.get(kind, Color("7553DF"))


## Walk through `points` (world positions), then emit `arrived`.
func walk(points: Array[Vector3]) -> void:
	var distance := 0.0
	var last := global_position
	for p in points:
		distance += last.distance_to(p)
		last = p
	if distance < 0.05:
		arrived.emit()
		return

	var speed := WALK_SPEED if distance / WALK_SPEED <= LONGEST_WALK else RUN_SPEED
	_stop_working()
	zz.visible = false
	is_walking = true
	mii.play("walk" if speed == WALK_SPEED else "run")

	# Godot concept: a Tween animates properties over time. Chained tweeners run
	# one after another, so each leg starts when the last one ends.
	if _walk_tween:
		_walk_tween.kill()
	_walk_tween = create_tween()
	last = global_position
	for p in points:
		var leg := last.distance_to(p)
		if leg < 0.01:
			continue
		_walk_tween.tween_callback(_face_toward.bind(p))
		_walk_tween.tween_property(self, "global_position", p, leg / speed)
		last = p
	_walk_tween.tween_callback(func():
		is_walking = false
		arrived.emit())


## Stand still at `spot` in a state: "idle", "working", or "sleeping".
## `look_at_point` is what to face while working (the building).
func settle(spot: Vector3, new_state: String, look_at_point: Vector3) -> void:
	if global_position.distance_to(spot) > 0.05:
		var points: Array[Vector3] = [spot]
		walk(points)  # a short step into place
		await arrived
	state = new_state
	zz.visible = state == "sleeping"
	_stop_working()
	match state:
		"working":
			_face_toward(look_at_point)
			mii.play("idle")
			_start_working()
		"sleeping":
			_facing = 0.0
			mii.play("idle")
		_:
			_facing = 0.0
			mii.play("idle")


## Jump straight to a spot (no walk), for a fresh snapshot.
func place(spot: Vector3) -> void:
	if _walk_tween:
		_walk_tween.kill()
	is_walking = false
	global_position = spot


func _process(delta: float) -> void:
	# Turn smoothly toward the direction we want to face.
	mii.rotation.y = lerp_angle(mii.rotation.y, _facing, delta * 10.0)
	if bubble.visible:
		bubble.position.y = 4.3 + sin(Time.get_ticks_msec() / 300.0) * 0.08
	if zz.visible:
		zz.position.y = 3.9 + fmod(Time.get_ticks_msec() / 1500.0, 1.0) * 0.4


func _face_toward(point: Vector3) -> void:
	var d := point - global_position
	if Vector2(d.x, d.z).length() > 0.01:
		_facing = atan2(d.x, d.z)  # the Mii faces +Z, so this angle points it at `point`


## A little hop on repeat while working.
func _start_working() -> void:
	_work_tween = create_tween().set_loops()
	_work_tween.tween_property(mii, "position:y", 0.18, 0.18).set_ease(Tween.EASE_OUT)
	_work_tween.tween_property(mii, "position:y", 0.0, 0.18).set_ease(Tween.EASE_IN)
	_work_tween.tween_interval(0.5)


func _stop_working() -> void:
	if _work_tween:
		_work_tween.kill()
		_work_tween = null
	mii.position.y = 0.0
