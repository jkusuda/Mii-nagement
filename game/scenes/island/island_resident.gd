class_name IslandResident
extends Node3D
## One resident on the 3D island: their Mii, a name tag, a mood word under it, a
## bubble icon when they need you, "z z" when they sleep, a speech line when they
## say something, and little emotes (hops, slumps, head turns).
##
## island_residents.gd decides *where* they go; this script only knows how to
## walk along a list of points and how to look for each state. island_life.gd
## borrows idle residents for ambient strolls and chats (`life_busy`); any
## server event for them calls stop_life() and the server's word wins.

signal arrived

const MII_SCALE := 1.6
const WALK_SPEED := 3.5    # units per second
const RUN_SPEED := 7.0
const LONGEST_WALK := 3.5  # seconds: on long routes, run instead so we keep up with the server

var resident_id := ""
var state := "idle"
var is_walking := false

## True while island_life.gd has borrowed this resident for an ambient action.
var life_busy := false
## Where they stood before island_life took them for a stroll (so they can go back).
var home_spot := Vector3.ZERO

const FONT := preload("res://assets/font/DreamLife-V1.0.0_by_MaxiGamer.ttf")
const INK := Color(0.161, 0.18, 0.314, 1)

var _facing := 0.0  # radians around y, 0 = toward the camera
var _work_tween: Tween
var _walk_tween: Tween
var _emote_tween: Tween
var _speech_tween: Tween
var _mood_tag: Label3D
var _speech: Label3D

@onready var mii: Mii = %Mii
@onready var name_tag: Label3D = %NameTag
@onready var bubble: Node3D = %Bubble
@onready var bubble_icon: Sprite3D = %BubbleIcon
@onready var zz: Label3D = %Zz


func _ready() -> void:
	mii.scale = Vector3.ONE * MII_SCALE
	# Built in code rather than in the .tscn: two more Label3Ds like NameTag.
	_mood_tag = _make_label(Vector3(0, 2.72, 0), 30, 0.02)
	_speech = _make_label(Vector3(0, 5.1, 0), 36, 0.02)
	_speech.width = 440.0
	_speech.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_speech.visible = false


## A billboard label in the island's font with a white outline, like NameTag.
func _make_label(at: Vector3, size: int, pixel: float) -> Label3D:
	var label := Label3D.new()
	label.position = at
	label.pixel_size = pixel
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.no_depth_test = true
	label.render_priority = 1
	label.font = FONT
	label.font_size = size
	label.outline_size = 12
	label.modulate = INK
	label.outline_modulate = Color.WHITE
	add_child(label)
	return label


## Show the resident's details (a dictionary from Net.island). `open_bubble` is
## their waiting bubble, or {} when they don't need you.
func show_resident(resident: Dictionary, open_bubble: Dictionary) -> void:
	mii.color = Color(str(resident.get("color", "#FFFFFF")))
	name_tag.text = str(resident.get("name", "?"))
	_mood_tag.text = ResidentData.vibe_word(resident)
	_mood_tag.modulate = ResidentData.vibe_color(resident)
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


## Say something out loud for a few seconds (a chat line, a help request, a thought).
## Godot concept: a Tween can also wait (tween_interval) and run a function at the
## end (tween_callback), so one Tween does "show, wait, fade, hide".
func say(text: String, seconds := 4.0) -> void:
	if _speech_tween:
		_speech_tween.kill()
	_speech.text = text
	_speech.modulate.a = 1.0
	_speech.outline_modulate.a = 1.0
	_speech.visible = true
	_speech_tween = create_tween()
	_speech_tween.tween_interval(max(seconds - 0.6, 0.2))
	_speech_tween.tween_property(_speech, "modulate:a", 0.0, 0.6)
	_speech_tween.parallel().tween_property(_speech, "outline_modulate:a", 0.0, 0.6)
	_speech_tween.tween_callback(func(): _speech.visible = false)


## A little reaction: "hop", "proud" (three hops), "frustrated" (a slump), "tired"
## (a slow sway), "confused" (a head turn), "shift" (roll over in their sleep),
## "wave". While working, the hop loop owns the Mii's height, so hops become a
## small bounce in scale instead.
func emote(kind: String) -> void:
	if _emote_tween:
		_emote_tween.kill()
	var s := MII_SCALE
	_emote_tween = create_tween()
	match kind:
		"hop", "happy", "proud", "wave":
			var hops := 3 if kind == "proud" else 1
			for i in hops:
				if _work_tween:
					_emote_tween.tween_property(mii, "scale", Vector3(s * 1.08, s * 0.94, s * 1.08), 0.12)
					_emote_tween.tween_property(mii, "scale", Vector3.ONE * s, 0.12)
				else:
					_emote_tween.tween_property(mii, "position:y", 0.4, 0.16).set_ease(Tween.EASE_OUT)
					_emote_tween.tween_property(mii, "position:y", 0.0, 0.16).set_ease(Tween.EASE_IN)
			if kind == "wave":
				say("hi!", 1.5)
		"frustrated":
			_emote_tween.tween_property(mii, "scale:y", s * 0.9, 0.25)
			_emote_tween.tween_interval(1.2)
			_emote_tween.tween_property(mii, "scale:y", s, 0.3)
		"tired":
			for i in 2:
				_emote_tween.tween_property(mii, "rotation:z", 0.08, 0.9).set_trans(Tween.TRANS_SINE)
				_emote_tween.tween_property(mii, "rotation:z", -0.08, 0.9).set_trans(Tween.TRANS_SINE)
			_emote_tween.tween_property(mii, "rotation:z", 0.0, 0.5)
		"confused":
			var start := _facing
			_emote_tween.tween_property(self, "_facing", start + 0.6, 0.3)
			_emote_tween.tween_property(self, "_facing", start - 0.6, 0.6)
			_emote_tween.tween_property(self, "_facing", start, 0.3)
		"shift":
			_emote_tween.tween_property(self, "_facing", _facing + deg_to_rad(25), 1.5).set_trans(Tween.TRANS_SINE)
		_:
			_emote_tween.kill()
			_emote_tween = null


## Turn to face a point (used by island_life when two residents chat).
func look_toward(point: Vector3) -> void:
	_face_toward(point)


## The server has plans for this resident: drop whatever ambient action they were
## in. Anyone awaiting `arrived` gets it (deferred, so callers finish first).
func stop_life() -> void:
	if not life_busy and not is_walking:
		return
	life_busy = false
	if _walk_tween:
		_walk_tween.kill()
		_walk_tween = null
	if _emote_tween:
		_emote_tween.kill()
		_emote_tween = null
	mii.position.y = 0.0
	mii.scale = Vector3.ONE * MII_SCALE
	mii.rotation.z = 0.0
	if is_walking:
		is_walking = false
		mii.play("idle")
		arrived.emit.call_deferred()


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
