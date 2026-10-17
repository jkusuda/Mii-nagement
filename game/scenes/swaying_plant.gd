class_name SwayingPlant
extends Node3D
## A tree or bush that gently sways in the breeze (used by apple_tree.tscn
## and bush.tscn, which set their own sway amount in the Inspector).
##
## Each plant scene is set up so this node's origin is the bottom of the
## plant: put it on the grass, then rotate and scale it freely in the editor.
## Only the inner "Sway" node moves at runtime, so the transform you set in
## the editor is never touched.
##
## Godot concept: `_process(delta)` runs once per frame. `delta` is the time
## since the last frame in seconds, so adding it up gives a smooth clock that
## doesn't depend on the frame rate.

## How far the plant leans, in degrees.
@export var sway_degrees := 2.5
## Seconds for one full back-and-forth.
@export var sway_period := 3.5

var _time := 0.0

@onready var _sway: Node3D = $Sway


func _ready() -> void:
	# Start every plant at a different point in its sway (and a slightly
	# different speed), so they don't all move in lockstep like a robot army.
	_time = randf() * 100.0
	sway_period *= randf_range(0.85, 1.15)


func _process(delta: float) -> void:
	_time += delta
	var t := _time * TAU / sway_period
	# Lean mostly along one direction, with a smaller sideways wobble. Using
	# two different speeds keeps the motion from looking like a perfect loop.
	_sway.rotation.z = deg_to_rad(sway_degrees) * sin(t)
	_sway.rotation.x = deg_to_rad(sway_degrees * 0.4) * sin(t * 0.63 + 1.0)
	# A tiny squash and stretch, like the leaves are breathing.
	var breathe := 1.0 + 0.015 * sin(t * 1.7)
	_sway.scale = Vector3(1.0 / sqrt(breathe), breathe, 1.0 / sqrt(breathe))
