class_name IslandResident
extends Node3D
## One resident on the 3D island: their Mii, a name tag, a mood word under it, a
## bubble icon when they need you, "z z" when they sleep, a speech bubble when they
## say something, and little emotes (hops, slumps, head turns).
##
## island_residents.gd decides *where* they go; this script only knows how to
## walk along a list of points and how to look for each state. island_life.gd
## borrows idle residents for ambient strolls and chats (`life_busy`); any
## server event for them calls stop_life() and the server's word wins.
##
## Working and sleeping residents go *inside* their building: enter() walks them
## to the door and hides the Mii and its tags (the ✋ bubble and the "z Z" stay,
## floating over the door). The next walk() pops them back out of the door first.

signal arrived

const MII_SCALE := 1.6
const WALK_SPEED := 3.5  # units per second
const RUN_SPEED := 7.0
const LONGEST_WALK := 5.0  # seconds: a hurried walk (off to work) longer than this runs
const SPEECH_MAX := 80  # characters over a head (see short_line)
const MOOD_DISC := preload("res://assets/ui/mood_disc.svg")
const MOOD_GAP := 0.45  # world units between the end of the name and the mood icon
const SPEECH_Y := 4.3  # the speech bubble's tail tip, over the name tag
const SPEECH_Y_OVER_BUBBLE := 5.4  # ...or over the request bubble when one shows

var resident_id := ""
var state := "idle"
var is_walking := false

## True while island_life.gd has borrowed this resident for an ambient action.
var life_busy := false
## True while they're inside a building (working): the Mii is hidden at the door.
var inside := false

var _facing := 0.0  # radians around y, 0 = toward the camera
var _dest := Vector3.ZERO  # where the current walk ends
var _settling := 0  # bumped by each settle(), so an older one that wakes up late stands down
var _work_tween: Tween
var _walk_tween: Tween
var _emote_tween: Tween
var _door_tween: Tween
var _mood_tag: Sprite3D  # a white disc with the mood icon on it
var _mood_icon: Sprite3D
var _speech: SpeechBubble
var _steps := AudioStreamPlayer3D.new()  # footsteps on sand while walking

@onready var mii: Mii = %Mii
@onready var name_tag: Label3D = %NameTag
@onready var bubble: Node3D = %Bubble
@onready var bubble_icon: Sprite3D = %BubbleIcon
@onready var zz: Label3D = %Zz


func _ready() -> void:
	mii.scale = Vector3.ONE * MII_SCALE
	# Built in code, not in the .tscn: the mood tag beside the name, a Lucide icon
	# tinted with the mood's colour on a small white disc (like the request bubble).
	_mood_tag = _make_sprite(MOOD_DISC, 0.008, 1)
	_mood_tag.position.y = 3.0
	add_child(_mood_tag)
	_mood_icon = _make_sprite(null, 0.0075, 2)
	_mood_tag.add_child(_mood_icon)
	# The speech bubble's tail tip sits above the name tag (see say).
	_speech = SpeechBubble.new()
	_speech.position = Vector3(0, SPEECH_Y, 0)
	add_child(_speech)
	# Godot concept: an AudioStreamPlayer3D sounds louder the closer it is to the
	# camera. unit_size is the distance at which it plays at volume_db.
	_steps.stream = Audio.footsteps
	_steps.bus = &"Sounds"
	_steps.volume_db = -12.0
	_steps.unit_size = 40.0
	add_child(_steps)


## A billboard picture drawn over everything, like the request bubble's sprites.
func _make_sprite(texture: Texture2D, pixel: float, priority: int) -> Sprite3D:
	var sprite := Sprite3D.new()
	sprite.texture = texture
	sprite.pixel_size = pixel
	sprite.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	sprite.no_depth_test = true
	sprite.render_priority = priority
	return sprite


## Show the resident's details (a dictionary from Net.island). `open_bubble` is
## their waiting bubble, or {} when they don't need you.
func show_resident(resident: Dictionary, open_bubble: Dictionary) -> void:
	mii.color = Color(str(resident.get("color", "#FFFFFF")))
	var look := MiiParts.for_resident(resident)
	if look != mii.appearance:
		mii.appearance = look
	name_tag.text = str(resident.get("name", "?"))
	_mood_icon.texture = ResidentData.vibe_icon(resident)
	_mood_icon.modulate = ResidentData.vibe_color(resident)
	# Sit right of the name with a little gap; get_aabb() is the name's size in world units.
	_mood_tag.position.x = name_tag.get_aabb().size.x / 2.0 + MOOD_GAP
	bubble.visible = not open_bubble.is_empty()
	if bubble.visible:
		var kind := str(open_bubble["kind"])
		bubble_icon.texture = Protocol.bubble(kind)["icon"]
		bubble_icon.modulate = Protocol.bubble(kind)["color"]


## Walk through `points` (world positions), then emit `arrived`. Someone inside a
## building steps out of the door first. Residents stroll; only a `hurry` (the
## server sent them to work) over a long route makes them run.
func walk(points: Array[Vector3], hurry := false) -> void:
	if inside:
		_come_out()
	var distance := route_length(points)
	if distance < 0.05:
		arrived.emit()
		return

	var run := hurry and distance / WALK_SPEED > LONGEST_WALK
	var speed := RUN_SPEED if run else WALK_SPEED
	_stop_working()
	zz.visible = false
	mii.asleep = false
	is_walking = true
	_dest = points[-1]
	mii.play("run" if run else "walk")
	_footsteps(true, run)

	# Godot concept: a Tween animates properties over time. Chained tweeners run
	# one after another, so each leg starts when the last one ends.
	if _walk_tween:
		_walk_tween.kill()
	_walk_tween = create_tween()
	var last := global_position
	for p in points:
		var leg := last.distance_to(p)
		if leg < 0.01:
			continue
		_walk_tween.tween_callback(_face_toward.bind(p))
		_walk_tween.tween_property(self, "global_position", p, leg / speed)
		last = p
	_walk_tween.tween_callback(
		func():
			is_walking = false
			mii.play("idle")  # stop the legs as soon as the feet stop
			_footsteps(false)
			arrived.emit()
	)


## Where they'll be standing: the end of their walk, or right here.
func spot() -> Vector3:
	return _dest if is_walking else global_position


## How far it is from here through `points`.
func route_length(points: Array[Vector3]) -> float:
	var distance := 0.0
	var last := global_position
	for p in points:
		distance += last.distance_to(p)
		last = p
	return distance


## Go inside a building to work or sleep (`new_state`): walk through `points`
## (the last one is the door), then shrink away through the door. Safe to call
## again while already inside (say, from working to sleeping). `instant`: already
## in there (the island was just rebuilt), so no walk and no shrinking.
func enter(points: Array[Vector3], new_state := "working", instant := false) -> void:
	state = new_state
	if inside:
		zz.visible = state == "sleeping"
		return
	zz.visible = false
	_stop_working()
	if instant:
		inside = true
		mii.scale = Vector3.ONE * MII_SCALE * 0.15  # as small as after the shrink, for when they come out
		_set_shown(false)
		zz.visible = state == "sleeping"
		return
	if route_length(points) < 0.05:
		inside = true
	else:
		walk(points, state == "working")
		inside = true  # only now: walk() steps out of the door first when inside
		await arrived
		if not inside or is_walking:
			return  # the server sent them somewhere else on the way in
	if not points.is_empty():
		_face_toward(points[-1] + (points[-1] - _route_before_door(points)))
	# Godot concept: tween_callback runs a function when the tween reaches it, so
	# this is "shrink for a quarter second, then hide".
	_door_tween = create_tween()
	_door_tween.tween_property(mii, "scale", Vector3.ONE * MII_SCALE * 0.15, 0.25).set_ease(Tween.EASE_IN)
	_door_tween.tween_callback(_set_shown.bind(false))
	_door_tween.tween_callback(func(): zz.visible = state == "sleeping")  # "z Z" over the door


## The point walked from just before the door (to face through the doorway).
func _route_before_door(points: Array[Vector3]) -> Vector3:
	return points[-2] if points.size() >= 2 else global_position


## Pop back out of the door: visible again, full size.
func _come_out() -> void:
	inside = false
	if _door_tween:
		_door_tween.kill()
	_set_shown(true)
	mii.scale = Vector3.ONE * MII_SCALE * 0.15
	_door_tween = create_tween()
	_door_tween.tween_property(mii, "scale", Vector3.ONE * MII_SCALE, 0.2).set_ease(Tween.EASE_OUT)


## Show or hide the Mii and its tags (the bubble and speech lines stay).
func _set_shown(on: bool) -> void:
	mii.visible = on
	name_tag.visible = on
	_mood_tag.visible = on


## Walk `route` (along the path) to its last point and stand still there in a
## state: "idle", "working", or "sleeping". `look_at_point` is what to face
## while working (the building). Someone inside comes out first, unless still
## working or sleeping in there.
func settle(route: Array[Vector3], new_state: String, look_at_point: Vector3) -> void:
	if inside:
		if new_state in ["working", "sleeping"]:
			return  # still in there
		_come_out()
	_settling += 1
	var mine := _settling
	if not route.is_empty() and global_position.distance_to(route[-1]) > 0.05:
		walk(route)  # a few steps into place
		await arrived
		if mine != _settling or is_walking or inside:
			return  # sent somewhere else meanwhile: that walk decides how they end up
	state = new_state
	zz.visible = state == "sleeping"
	mii.asleep = state == "sleeping"  # no building to sleep in (a new resident): asleep where they stand
	_stop_working()
	match state:
		"working":
			_face_toward(look_at_point)
			mii.play("idle")
			_start_working()
		_:  # idle, sleeping (the "z Z" shows above)
			_facing = 0.0
			mii.play("idle")


## Say something out loud for a few seconds (a chat line, a help request, a thought),
## in a speech bubble over their head.
func say(text: String, seconds := 4.0) -> void:
	# Above the name tag, or above the request bubble when one is showing.
	_speech.position.y = SPEECH_Y_OVER_BUBBLE if bubble.visible else SPEECH_Y
	_speech.say(short_line(text), seconds)


## What fits over a head: the first sentence, cut at a word with "…" past
## SPEECH_MAX characters. The full message is still in the chat and the news.
static func short_line(text: String) -> String:
	var line := text.strip_edges().replace("\n", " ")
	for end in [". ", "! ", "? "]:
		var at := line.find(end)
		if at > 0:
			line = line.left(at + 1)
	if line.length() <= SPEECH_MAX:
		return line
	var cut := line.left(SPEECH_MAX).rfind(" ")
	return line.left(cut if cut > SPEECH_MAX / 2.0 else SPEECH_MAX).strip_edges() + "…"


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
		_footsteps(false)
		arrived.emit.call_deferred()


## Jump straight to a spot (no walk), for a fresh snapshot.
func place(at: Vector3) -> void:
	if _walk_tween:
		_walk_tween.kill()
	is_walking = false
	mii.play("idle")
	_footsteps(false)
	if inside:
		_come_out()
	global_position = at


## Footsteps on (quicker when running) or off. Each resident starts somewhere
## else in the recording, so two walkers don't step in time.
func _footsteps(on: bool, run := false) -> void:
	if not on or Audio.silent:
		_steps.stop()
		return
	_steps.pitch_scale = 1.3 if run else 1.0
	if not _steps.playing:
		_steps.play(randf() * _steps.stream.get_length())


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
