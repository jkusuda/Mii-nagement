class_name Mii
extends Node3D
## One resident's 3D body: the rigged Mii model with a face, hair, and the
## resident's colour as their shirt, playing an idle / walk / run animation.
##
## The model (assets/models/mii.glb) is one skinned mesh, about 1.7 units tall,
## feet at y = 0, facing +Z, with a Mixamo skeleton (bones `mixamorig_Head`,
## `mixamorig_LeftHand`, ...). It has no face or hair of its own:
##   - The face is painted by shaders/mii.gdshader. On first use we work out, for
##     every vertex, where it sits inside the head (in the rest pose) and store it
##     in the mesh's CUSTOM0/CUSTOM1 channels; the shader stamps eyes, brows, nose
##     and mouth from the part atlases onto the front of the head.
##   - The hair is a separate mesh (MiiParts.hair_mesh) hung on the head bone with
##     a BoneAttachment3D, so it follows the head when the Mii walks.
##
## Godot concept: a .glb is imported as a whole scene (nodes, skeleton, mesh,
## AnimationPlayer). mii.tscn *instances* it as the `Model` child, the same way
## game.tscn instances island_3d.tscn, and this script reaches into it.

const SHADER := preload("res://shaders/mii.gdshader")

## Animation names inside mii.glb.
const ANIMATIONS := {
	"idle": "Idle_11",
	"walk": "Walking",
	"run": "Running",
}

## Bones, by the end of their name (the importer turns "mixamorig:Head" into "mixamorig_Head").
const HEAD_BONES := ["Head", "headfront"]
const SKIN_BONES := ["LeftHand", "RightHand"]
## Arms hang below the waist, so the shader needs to know them to keep sleeves out of the trousers.
const ARM_BONES := ["Shoulder", "Arm", "ForeArm", "Hand"]

## Where the head sits inside the hair meshes (tools/build_mii_parts.py has the
## same numbers): a ball of this radius. We stretch that ball onto the Mii's head.
const HAIR_HEAD_CENTER := Vector3(0.0, 0.36, -0.02)
const HAIR_HEAD_RADIUS := 0.27
## A little bigger than the head, so the scalp never pokes through.
const HAIR_FIT := Vector3(1.06, 1.04, 1.08)
## Nudges the hair in head units (x, up, forward) after fitting.
const HAIR_OFFSET := Vector3(0.0, 0.02, -0.02)

## The baked mesh and head measurements, per source mesh, shared by every Mii.
static var _bake_cache := {}

## The resident's colour: their shirt.
@export var color := Color("B9A8F5"):
	set(value):
		color = value
		_material.set_shader_parameter("shirt_color", value)
		_hat_material.albedo_color = value

## The look: {"skin", "hair", "hair_color", "eyes", "brows", "nose", "mouth"} (see MiiParts).
var appearance: Dictionary = MiiParts.DEFAULT.duplicate():
	set(value):
		appearance = MiiParts.DEFAULT.merged(value, true)
		if is_node_ready():
			_apply()

## Eyes shut and no blinking: a resident asleep out on the island (island_resident.gd).
var asleep := false:
	set(value):
		asleep = value
		_blink_time = -1.0
		_material.set_shader_parameter("blink", 1.0 if value else 0.0)

var _material := ShaderMaterial.new()
var _player: AnimationPlayer
var _hair: MeshInstance3D
var _hair_material := StandardMaterial3D.new()
var _hat_material := StandardMaterial3D.new()
var _head := {}  # from _bake(): head centre and half size in mesh space, head bind pose
var _shown_hair := -2
var _animation := "idle"  # asked for before _ready() too
var _blink_wait := 2.0
var _blink_time := -1.0


func _ready() -> void:
	_material.shader = SHADER
	_material.set_shader_parameter("shirt_color", color)
	for kind in MiiParts.FACE:
		var tex: Texture2D = MiiParts.FACE[kind][0]
		_material.set_shader_parameter(kind + "_tex", tex)
		_material.set_shader_parameter(kind + "_grid", tex.get_size() / MiiParts.CELL)
	_hair_material.roughness = 0.7
	_hair_material.cull_mode = BaseMaterial3D.CULL_DISABLED
	_hat_material.roughness = 0.8

	# find_children(pattern, type) searches the whole imported model for us, so we
	# don't depend on the exact node path Godot gave the mesh on import.
	for mesh: MeshInstance3D in find_children("*", "MeshInstance3D"):
		if mesh.skin == null:
			continue
		var baked := _bake(mesh)
		mesh.mesh = baked["mesh"]
		mesh.material_override = _material
		_head = baked
		_material.set_shader_parameter("waist_y", baked["waist_y"])
		_material.set_shader_parameter("ankle_y", baked["ankle_y"])
		_attach_hair_holder(mesh)

	_player = find_children("*", "AnimationPlayer")[0]
	for anim_name in ANIMATIONS.values():
		if _player.has_animation(anim_name):
			_player.get_animation(anim_name).loop_mode = Animation.LOOP_LINEAR
	_apply()
	play(_animation)


## Play "idle", "walk", or "run". Blends over 0.2 s from the current animation.
func play(what: String) -> void:
	_animation = what
	var anim_name: String = ANIMATIONS.get(what, "")
	if _player and _player.has_animation(anim_name):
		_player.play(anim_name, 0.2)


## Blink every few seconds: the shader squashes the eyes while `blink` goes 0 -> 1 -> 0.
func _process(delta: float) -> void:
	if asleep:
		return
	if _blink_time >= 0.0:
		_blink_time += delta
		var t := _blink_time / 0.15
		_material.set_shader_parameter("blink", 1.0 - absf(t * 2.0 - 1.0) if t < 1.0 else 0.0)
		if t >= 1.0:
			_blink_time = -1.0
	else:
		_blink_wait -= delta
		if _blink_wait <= 0.0:
			_blink_time = 0.0
			_blink_wait = randf_range(2.0, 5.5)


## Push `appearance` into the shader and swap the hair mesh if it changed.
func _apply() -> void:
	var look := appearance
	var hair_color := Color(str(look["hair_color"]))
	_material.set_shader_parameter("skin_color", Color(str(look["skin"])))
	_material.set_shader_parameter("hair_color", hair_color)
	for kind in MiiParts.FACE:
		_material.set_shader_parameter(kind, clampi(int(look[kind]), 0, MiiParts.count(kind) - 1))
	_hair_material.albedo_color = hair_color
	_hat_material.albedo_color = color

	var style := int(look["hair"])
	if _hair and style != _shown_hair:
		_shown_hair = style
		_hair.mesh = MiiParts.hair_mesh(style)
	if _hair and _hair.mesh:
		# Hats come with their own knit/stripe textures: keep those, tinted with the
		# resident's colour; their other surfaces are the hair peeking out.
		for i in _hair.mesh.get_surface_count():
			var own := _hair.mesh.surface_get_material(i) as StandardMaterial3D
			if MiiParts.is_hat(style) and own and own.albedo_texture:
				_hat_material.albedo_texture = own.albedo_texture
				_hair.set_surface_override_material(i, _hat_material)
			else:
				_hair.set_surface_override_material(i, _hair_material)


# --- Hair -----------------------------------------------------------------------


## Hang an (empty) hair MeshInstance3D on the head bone, sized to the head.
## Godot concept: a BoneAttachment3D is a node that copies one bone's pose every
## frame, so whatever is under it moves with that bone.
func _attach_hair_holder(body: MeshInstance3D) -> void:
	var skeleton := body.get_node(body.skeleton) as Skeleton3D
	var holder := BoneAttachment3D.new()
	holder.name = "Head"
	skeleton.add_child(holder)
	holder.bone_name = _head["bone"]

	# Fit the hair's head ball onto this head's box, in mesh space...
	var half: Vector3 = _head["half"]
	var hair_scale := half / HAIR_HEAD_RADIUS * HAIR_FIT
	var fit := Transform3D(Basis.from_scale(hair_scale), _head["center"] + HAIR_OFFSET * half)
	fit = fit * Transform3D(Basis.IDENTITY, -HAIR_HEAD_CENTER)
	# ...then move it from mesh space into the bone's space. The skin's bind pose
	# is exactly that conversion (it's how the body's own vertices follow bones).
	_hair = MeshInstance3D.new()
	_hair.name = "Hair"
	_hair.transform = _head["bind"] * fit
	holder.add_child(_hair)


# --- Baking the head coordinates ------------------------------------------------


static func _bone_ends_with(bone_name: String, ends: Array) -> bool:
	for end in ends:
		if bone_name.ends_with(end):
			return true
	return false


## Copy the model's mesh with two extra per-vertex channels for the shader:
## CUSTOM0 = position in the head box + head weight, CUSTOM1 = height, skin weight, arm weight.
## Also returns the head box and a few body heights. Done once per model.
static func _bake(body: MeshInstance3D) -> Dictionary:
	var key := body.mesh.get_instance_id()
	if _bake_cache.has(key):
		return _bake_cache[key]

	var skin := body.skin
	var head_binds := {}
	var skin_binds := {}
	var arm_binds := {}
	var head_bone := ""
	var head_bind := Transform3D()
	var bone_y := {}  # bone name end -> height of the bone in mesh space
	for i in skin.get_bind_count():
		var bone_name := String(skin.get_bind_name(i))
		# A bind pose maps mesh space into the bone's space; its inverse's origin
		# is where the bone sits on the mesh.
		bone_y[bone_name.get_slice("_", bone_name.get_slice_count("_") - 1)] = (
			skin.get_bind_pose(i).affine_inverse().origin.y
		)
		if _bone_ends_with(bone_name, HEAD_BONES):
			head_binds[i] = true
			if bone_name.ends_with("Head"):
				head_bone = bone_name
				head_bind = skin.get_bind_pose(i)
		elif _bone_ends_with(bone_name, SKIN_BONES):
			skin_binds[i] = true
		if _bone_ends_with(bone_name, ARM_BONES):
			arm_binds[i] = true

	var src := body.mesh
	var surfaces := []
	var head_min := Vector3(INF, INF, INF)
	var head_max := -head_min
	# Pass 1: weights per vertex, and the head's box.
	for s in src.get_surface_count():
		var arrays := src.surface_get_arrays(s)
		var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
		var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
		var n := verts.size()
		@warning_ignore("integer_division")
		var stride := bones.size() / maxi(n, 1)
		var head_w := PackedFloat32Array()
		var skin_w := PackedFloat32Array()
		var arm_w := PackedFloat32Array()
		head_w.resize(n)
		skin_w.resize(n)
		arm_w.resize(n)
		for v in n:
			for k in stride:
				var b := bones[v * stride + k]
				if head_binds.has(b):
					head_w[v] += weights[v * stride + k]
				elif skin_binds.has(b):
					skin_w[v] += weights[v * stride + k]
				if arm_binds.has(b):
					arm_w[v] += weights[v * stride + k]
			if head_w[v] > 0.5:
				head_min = head_min.min(verts[v])
				head_max = head_max.max(verts[v])
		surfaces.append([arrays, head_w, skin_w, stride, arm_w])

	var center := (head_min + head_max) * 0.5
	center.x = 0.0
	var half := (head_max - head_min) * 0.5

	# Pass 2: write the channels into a new mesh.
	var out := ArrayMesh.new()
	for sdata in surfaces:
		var arrays: Array = sdata[0]
		var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var n := verts.size()
		var c0 := PackedFloat32Array()
		var c1 := PackedFloat32Array()
		c0.resize(n * 4)
		c1.resize(n * 4)
		for v in n:
			var p := (verts[v] - center) / half
			c0[v * 4] = p.x
			c0[v * 4 + 1] = p.y
			c0[v * 4 + 2] = p.z
			c0[v * 4 + 3] = sdata[1][v]
			c1[v * 4] = verts[v].y
			c1[v * 4 + 1] = sdata[2][v]
			c1[v * 4 + 2] = sdata[4][v]
		arrays[Mesh.ARRAY_CUSTOM0] = c0
		arrays[Mesh.ARRAY_CUSTOM1] = c1
		var flags := (
			(Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT)
			| (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM1_SHIFT)
		)
		if sdata[3] == 8:
			flags |= Mesh.ARRAY_FLAG_USE_8_BONE_WEIGHTS
		out.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays, [], {}, flags)

	var result := {
		"mesh": out,
		"center": center,
		"half": half,
		"bone": head_bone,
		"bind": head_bind,
		"waist_y": bone_y.get("Hips", 0.68) + 0.02,
		"ankle_y": (bone_y.get("LeftFoot", 0.12) + bone_y.get("RightFoot", 0.12)) * 0.5 - 0.01,
	}
	_bake_cache[key] = result
	return result
