class_name IslandBuilder
extends Node3D
## Turns an IslandLayout into the 3D island: the ground (grass, beach, paths),
## the pond, the buildings, and the trees and bushes. Lives in island_3d.tscn as
## the "Island" node; call build() whenever the layout changes.
##
##   Island
##     Terrain     the ground mesh (IslandTerrain + terrain.gdshader)
##     Pond        the pond's water surface (pond.gdshader) + LilyPads
##     Buildings   Mailbox, Workshop, Lab, Courthouse, PostOffice (always all
##                 five; the ones this island doesn't have are hidden, so labels
##                 and residents can keep pointing at them)
##     Plants      apple trees and bushes
##     Lamps       lamp posts along the paths, lit at night
##     Weather     petals, leaves, snow, or fireflies (Seasons, island view only)
##
## The ground is only rebuilt when the land, pond, or paths change; moving a
## plant or switching the season is cheap.

signal built

## FULL for the island view; FAR for the home screen's sea and thumbnails.
enum Detail { FULL, FAR }

const BUILDING_SCENES := {
	"mailbox": preload("res://scenes/buildings/mailbox.tscn"),
	"workshop": preload("res://scenes/buildings/workshop.tscn"),
	"lab": preload("res://scenes/buildings/lab.tscn"),
	"courthouse": preload("res://scenes/buildings/courthouse.tscn"),
	"post_office": preload("res://scenes/buildings/post_office.tscn"),
}
const TreeScene := preload("res://scenes/plants/apple_tree.tscn")
const BushScene := preload("res://scenes/plants/bush.tscn")
const TerrainShader := preload("res://shaders/terrain.gdshader")
const PondShader := preload("res://shaders/pond.gdshader")
const TREE_SCALE := 3.4
const BUSH_SCALE := 1.25
const POND_WATER_Y := -0.36  # below the grass, so the banks hide the water's edges
const LampShader := preload("res://shaders/lamp.gdshader")
const MAX_LAMPS := 14
const LAMP_SPACING := 3.2  # tiles between lamps, at least

static var _noise: NoiseTexture2D

## FAR skips falling leaves and lamp lights, and leaves the sea alone.
@export var detail := Detail.FULL

var layout: IslandLayout
var ground := {}  # IslandTerrain.build(): field texture, its rect, the mesh

# Made in _ready() (not here), so a builder that's freed before it ever enters
# the scene tree (the sea view drops island_3d.tscn's own one) leaves no orphans.
var terrain: MeshInstance3D
var pond: MeshInstance3D
var buildings: Node3D
var plants: Node3D
var lamps: Node3D
var _lily_pads: Node3D
var _lamp_lights: Array = []  # [OmniLight3D, switch_on] for each lamp (island view only)
var _ground_key := ""
var _plants_key := ""


func _ready() -> void:
	terrain = MeshInstance3D.new()
	pond = MeshInstance3D.new()
	buildings = Node3D.new()
	plants = Node3D.new()
	lamps = Node3D.new()
	_lily_pads = Node3D.new()
	terrain.name = "Terrain"
	pond.name = "Pond"
	buildings.name = "Buildings"
	plants.name = "Plants"
	lamps.name = "Lamps"
	_lily_pads.name = "LilyPads"
	add_child(terrain)
	add_child(pond)
	pond.add_child(_lily_pads)
	add_child(buildings)
	add_child(plants)
	add_child(lamps)
	for type in BUILDING_SCENES:
		var node: IslandBuilding = BUILDING_SCENES[type].instantiate()
		node.name = type.to_pascal_case()  # "post_office" -> PostOffice
		node.lights_enabled = detail == Detail.FULL
		node.visible = false
		buildings.add_child(node)

	var material := ShaderMaterial.new()
	material.shader = TerrainShader
	material.set_shader_parameter("noise", noise_texture())
	terrain.material_override = material
	var water := ShaderMaterial.new()
	water.shader = PondShader
	water.set_shader_parameter("noise", noise_texture())
	pond.material_override = water

	if layout == null:  # until the backend says otherwise: the classic island
		build(IslandLayout.from_shape("classic", IslandLayout.STEP_ORDER))


## A seamless noise texture shared by the island shaders.
static func noise_texture() -> NoiseTexture2D:
	if _noise == null:
		var source := FastNoiseLite.new()
		source.frequency = 0.02
		_noise = NoiseTexture2D.new()
		_noise.noise = source
		_noise.seamless = true
	return _noise


func build(new_layout: IslandLayout) -> void:
	layout = new_layout
	layout.refresh()
	var key := layout.ground_key() + layout.seed_text
	if key != _ground_key:
		_ground_key = key
		ground = IslandTerrain.build(layout)
		_show_ground()
		_place_lamps()
	_place_buildings()
	_place_plants()
	Seasons.apply(self)
	if detail == Detail.FULL:
		show_on_water(get_node_or_null("../Water"))
	built.emit()


## Night: lamp lights fade in (each at its own moment); fireflies (summer
## weather) only come out in the dark.
func _process(_delta: float) -> void:
	for lamp in _lamp_lights:
		var on := smoothstep(lamp[1], lamp[1] + 0.08, DayNight.night)
		lamp[0].light_energy = on * 2.6
		lamp[0].visible = on > 0.0
	var weather := get_node_or_null("Weather") as CPUParticles3D
	if weather and weather.get_meta("night_only", false):
		weather.emitting = DayNight.night > 0.5


## Tell a water surface (water.gdshader) where this island's shore is.
func show_on_water(water: MeshInstance3D) -> void:
	if water == null or ground.is_empty():
		return
	var material := water.get_active_material(0) as ShaderMaterial
	if material == null:
		return
	material.set_shader_parameter("island_count", 1)
	material.set_shader_parameter("island_fields", [ground["field"]])
	material.set_shader_parameter("island_rects", PackedVector4Array([shore_rect()]))


## The field's area on the water, for water.gdshader (x, z corner + size).
func shore_rect(offset := Vector3.ZERO) -> Vector4:
	var rect: Rect2 = ground["rect"]
	return Vector4(rect.position.x + offset.x, rect.position.y + offset.z, rect.size.x, rect.size.y)


## The 3D node for a building type ("post_office" -> Buildings/PostOffice).
func building_node(type: String) -> Node3D:
	return buildings.get_node_or_null(type.to_pascal_case())


func _show_ground() -> void:
	terrain.mesh = ground["mesh"]
	var rect: Rect2 = ground["rect"]
	var material := terrain.material_override as ShaderMaterial
	material.set_shader_parameter("field", ground["field"])
	material.set_shader_parameter("field_rect", Vector4(rect.position.x, rect.position.y, rect.size.x, rect.size.y))

	var shape := IslandTerrain.pond_shape(layout)
	pond.visible = not shape.is_empty()
	for pad in _lily_pads.get_children():
		pad.queue_free()
	if shape.is_empty():
		material.set_shader_parameter("pond_half", Vector2(-1, -1))
		return
	var center: Vector2 = shape["center"]
	var half: Vector2 = shape["half"]
	material.set_shader_parameter("pond_center", center)
	material.set_shader_parameter("pond_half", half)
	var plane := PlaneMesh.new()
	plane.size = half * 2.0 + Vector2(1.2, 1.2)  # a bit bigger: the banks cover the edges
	pond.mesh = plane
	pond.position = Vector3(center.x, POND_WATER_Y, center.y)
	var water := pond.material_override as ShaderMaterial
	water.set_shader_parameter("pond_center", center)
	water.set_shader_parameter("pond_half", half)
	water.set_shader_parameter("pond_offset", center)
	_add_lily_pads(half)


## A few lily pads (one with a flower), always in the same spots for this island.
func _add_lily_pads(half: Vector2) -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(layout.seed_text + "lily")
	var pad_material := StandardMaterial3D.new()
	pad_material.albedo_color = Color(0.45, 0.78, 0.3)
	var flower_material := StandardMaterial3D.new()
	flower_material.albedo_color = Color(1.0, 0.72, 0.85)
	var count := clampi(int(half.x * half.y / 2.0), 1, 4)
	for i in count:
		var pad := MeshInstance3D.new()
		var disc := CylinderMesh.new()
		disc.top_radius = rng.randf_range(0.35, 0.55)
		disc.bottom_radius = disc.top_radius
		disc.height = 0.04
		disc.radial_segments = 14
		pad.mesh = disc
		pad.material_override = pad_material
		pad.position = Vector3(
			rng.randf_range(-half.x + 0.7, half.x - 0.7), 0.02, rng.randf_range(-half.y + 0.6, half.y - 0.6)
		)
		_lily_pads.add_child(pad)
		if i == 0:
			var flower := MeshInstance3D.new()
			var bud := SphereMesh.new()
			bud.radius = 0.16
			bud.height = 0.22
			flower.mesh = bud
			flower.material_override = flower_material
			flower.position = Vector3(0.08, 0.1, 0.05)
			pad.add_child(flower)


## Lamp posts along the paths: spaced out, on the grass just beside the path,
## away from doors, alternating sides where they can. The same island always
## gets the same lamps.
func _place_lamps() -> void:
	for child in lamps.get_children():
		child.free()
	_lamp_lights.clear()
	var doors := layout.buildings.map(func(b): return Vector2(IslandLayout.door_tile(b)))
	var blocked := layout.blocked_tiles()
	var tiles := layout.path_tiles.keys()
	tiles.sort()
	var chosen: Array[Vector2] = []
	var flip := false
	for t: Vector2i in tiles:
		if chosen.size() >= MAX_LAMPS:
			break
		var here := Vector2(t)
		if (
			doors.any(func(d): return d.distance_to(here) < 1.5)
			or chosen.any(func(c): return c.distance_to(here) < LAMP_SPACING)
		):
			continue
		var sides := [Vector2i.UP, Vector2i.RIGHT, Vector2i.DOWN, Vector2i.LEFT]
		if flip:
			sides.reverse()
		for side: Vector2i in sides:
			var n := t + side
			if layout.is_land(n) and not layout.path_tiles.has(n) and not blocked.has(n):
				_add_lamp(
					IslandLayout.tile_center(t) + Vector3(side.x, 0, side.y) * IslandLayout.TILE * 0.62, chosen.size()
				)
				chosen.append(here)
				flip = not flip
				break


func _add_lamp(at: Vector3, index: int) -> void:
	var lamp := Node3D.new()
	lamp.position = at
	lamps.add_child(lamp)
	var pole := MeshInstance3D.new()
	var pole_mesh := CylinderMesh.new()
	pole_mesh.top_radius = 0.05
	pole_mesh.bottom_radius = 0.08
	pole_mesh.height = 1.7
	pole.mesh = pole_mesh
	pole.position.y = 0.85
	var dark := StandardMaterial3D.new()
	dark.albedo_color = Color(0.22, 0.27, 0.33)
	pole.material_override = dark
	lamp.add_child(pole)
	var head := MeshInstance3D.new()
	var bulb := SphereMesh.new()
	bulb.radius = 0.17
	bulb.height = 0.3
	head.mesh = bulb
	head.position.y = 1.82
	var glow := ShaderMaterial.new()
	glow.shader = LampShader
	head.material_override = glow
	# Lamps switch on one after another as it gets dark.
	var switch_on := 0.35 + 0.3 * fposmod(index * 0.618, 1.0)
	head.set_instance_shader_parameter("switch_on", switch_on)
	lamp.add_child(head)
	if detail == Detail.FULL:
		var light := OmniLight3D.new()
		light.light_color = Color(1.0, 0.78, 0.48)
		light.omni_range = 5.6
		light.omni_attenuation = 1.3
		light.shadow_enabled = false
		light.light_energy = 0.0
		light.position.y = 1.75
		lamp.add_child(light)
		_lamp_lights.append([light, switch_on])


func _place_buildings() -> void:
	for type in BUILDING_SCENES:
		var node := building_node(type)
		var b := layout.building(type)
		node.visible = not b.is_empty()
		if b.is_empty():
			continue
		node.position = IslandLayout.lot_center(b)
		node.rotation = Vector3(0, IslandLayout.facing_angle(b), 0)


func _place_plants() -> void:
	var key := JSON.stringify(layout.shown_plants)
	if key == _plants_key:
		return
	_plants_key = key
	for child in plants.get_children():
		child.queue_free()
	for plant in layout.shown_plants:
		var tree: bool = plant["kind"] == "tree"
		var node: Node3D = (TreeScene if tree else BushScene).instantiate()
		var s: float = (TREE_SCALE if tree else BUSH_SCALE) * float(plant.get("scale", 1.0))
		node.position = IslandLayout.tile_to_world(Vector2(plant["x"], plant["y"]))
		node.rotation_degrees = Vector3(0, float(plant.get("rot", 0.0)), 0)
		node.scale = Vector3(s, s, s)
		if detail != Detail.FULL and node.has_node("FallingLeaves"):
			node.get_node("FallingLeaves").free()
		plants.add_child(node)
