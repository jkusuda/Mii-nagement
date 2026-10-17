class_name IslandBuilding
extends Node3D
## One building on the island (the root of scenes/buildings/<type>.tscn).
##
## The scene's origin is the middle of the building's 3 x 3-tile lot, on the
## ground, and the door faces +Z ("south", toward the camera). IslandBuilder
## moves and turns the whole scene to match the backend's x / y / facing.
##
## Children you can tune in the editor (open the .tscn):
##   Model  the .glb, scaled to fit the lot and lifted so it stands on the grass
##   Door   a marker on the path just outside the front door (residents stop here)
##
## At night a warm porch light comes on over the door (and the windows glow:
## that part is the building's season overlay, shaders/season_overlay.gdshader).

## Turn off for the far views (the home screen's sea, thumbnails): many islands,
## so only the glowing windows, no real lights.
@export var lights_enabled := true
## When the porch light comes on: the night amount (0-1) it switches on at.
@export var switch_on := 0.45

var _porch: OmniLight3D


func _ready() -> void:
	if not lights_enabled:
		set_process(false)
		return
	# Godot concept: an OmniLight3D shines in every direction from a point, and
	# fades out over its `omni_range`. It lights the door and the path in front.
	_porch = OmniLight3D.new()
	_porch.name = "PorchLight"
	_porch.light_color = Color(1.0, 0.74, 0.42)
	_porch.omni_range = 7.0
	_porch.omni_attenuation = 1.4
	_porch.shadow_enabled = false
	_porch.light_energy = 0.0
	_porch.position = Vector3(0, 2.3, 3.4)
	add_child(_porch)


func _process(_delta: float) -> void:
	var on := smoothstep(switch_on, switch_on + 0.12, DayNight.night)
	_porch.light_energy = on * 3.2
	_porch.visible = on > 0.0
