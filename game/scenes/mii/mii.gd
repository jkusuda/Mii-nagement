class_name Mii
extends Node3D
## One resident's 3D body: the blank Mii model, tinted with the resident's color,
## playing an idle / walk / run animation.
##
## The model (assets/models/mii.glb) is a single skinned mesh with a plain white
## material, a 24-bone skeleton, and three animations. It's about 1.7 units tall,
## with its feet at y = 0, facing +Z.
##
## Godot concept: a .glb is imported as a whole scene (nodes, skeleton, mesh,
## AnimationPlayer). mii.tscn *instances* it as the `Model` child, the same way
## game.tscn instances island_3d.tscn, and this script reaches into it.
##
## TODO(stretch): faces, hair, and accessories. See docs/mii-customization.md.

## Animation names inside mii.glb. The idle clip has Blender's default name.
const ANIMATIONS := {
	"idle": "Armature|clip0|baselayer",
	"walk": "Walking",
	"run": "Running",
}

@export var color := Color("B9A8F5"):
	set(value):
		color = value
		_material.albedo_color = value

var _material := StandardMaterial3D.new()
var _player: AnimationPlayer


func _ready() -> void:
	_material.albedo_color = color
	_material.roughness = 0.75
	# find_children(pattern, type) searches the whole imported model for us, so we
	# don't depend on the exact node path Godot gave the mesh on import.
	for mesh: MeshInstance3D in find_children("*", "MeshInstance3D"):
		mesh.material_override = _material

	_player = find_children("*", "AnimationPlayer")[0]
	for anim_name in ANIMATIONS.values():
		if _player.has_animation(anim_name):
			_player.get_animation(anim_name).loop_mode = Animation.LOOP_LINEAR
	play("idle")


## Play "idle", "walk", or "run". Blends over 0.2 s from the current animation.
func play(what: String) -> void:
	var anim_name: String = ANIMATIONS.get(what, "")
	if _player and _player.has_animation(anim_name):
		_player.play(anim_name, 0.2)
