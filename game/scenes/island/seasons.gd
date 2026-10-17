class_name Seasons
## The island's season (layout.season, picked in build mode or the island
## creator) changes its whole vibe:
##   spring  fresh green grass with little flowers, blossoms on the trees, petals drifting
##   summer  the classic bright island, fireflies by night
##   autumn  golden grass, orange and red trees, falling leaves
##   winter  snow on the ground, roofs, and trees, a frozen pond, snowfall
##
## IslandBuilder calls apply() after every build. Each season is one row in
## LOOKS, like DayNight's table: tweak the numbers to taste.

const LOOKS := {
	"spring":
	{
		"grass_light": Color(0.62, 0.88, 0.36),
		"grass_dark": Color(0.47, 0.8, 0.28),
		"clover": Color(0.8, 0.93, 0.55),
		"grass_edge": Color(0.38, 0.64, 0.22),
		"snow": 0.0,
		"foliage": 0.0,
		"blossom": 1.0,
		"plant_snow": 0.0,
		"roof_snow": 0.0,
		"ice": 0.0,
		"leaves": Color(1.0, 0.78, 0.88),
		"weather": "petals",
		"light": Color(1.0, 0.98, 1.0),
	},
	"summer":
	{
		"grass_light": Color(0.56, 0.86, 0.25),
		"grass_dark": Color(0.42, 0.76, 0.2),
		"clover": Color(0.66, 0.9, 0.38),
		"grass_edge": Color(0.35, 0.62, 0.18),
		"snow": 0.0,
		"foliage": 0.0,
		"blossom": 0.0,
		"plant_snow": 0.0,
		"roof_snow": 0.0,
		"ice": 0.0,
		"leaves": Color(0.13, 0.5, 0.12),
		"weather": "fireflies",
		"light": Color(1.0, 1.0, 1.0),
	},
	"autumn":
	{
		"grass_light": Color(0.8, 0.78, 0.34),
		"grass_dark": Color(0.66, 0.68, 0.27),
		"clover": Color(0.93, 0.6, 0.28),
		"grass_edge": Color(0.52, 0.5, 0.22),
		"snow": 0.0,
		"foliage": 0.92,
		"blossom": 0.0,
		"plant_snow": 0.0,
		"roof_snow": 0.0,
		"ice": 0.0,
		"leaves": Color(0.95, 0.5, 0.15),
		"weather": "leaves",
		"light": Color(1.05, 0.95, 0.85),
	},
	"winter":
	{
		"grass_light": Color(0.7, 0.8, 0.62),
		"grass_dark": Color(0.58, 0.7, 0.52),
		"clover": Color(0.85, 0.9, 0.85),
		"grass_edge": Color(0.48, 0.58, 0.44),
		"snow": 0.9,
		"foliage": 0.0,
		"blossom": 0.0,
		"plant_snow": 0.75,
		"roof_snow": 0.95,
		"ice": 1.0,
		"leaves": Color(1, 1, 1),
		"weather": "snow",
		"light": Color(0.9, 0.95, 1.08),
	},
}

const OverlayShader := preload("res://shaders/season_overlay.gdshader")

## Overlay materials, shared by every plant/building with the same model and season.
static var _overlays := {}


static func look(season: String) -> Dictionary:
	return LOOKS.get(season, LOOKS["summer"])


static func apply(island: IslandBuilder) -> void:
	var season := island.layout.season
	var row := look(season)

	var ground := island.terrain.material_override as ShaderMaterial
	for key in ["grass_light", "grass_dark", "clover", "grass_edge", "snow"]:
		ground.set_shader_parameter(key, row[key])
	var pond := island.pond.material_override as ShaderMaterial
	pond.set_shader_parameter("ice", row["ice"])
	island.get_node("Pond/LilyPads").visible = row["ice"] < 0.5  # under the ice

	for plant in island.plants.get_children():
		for mesh: MeshInstance3D in plant.find_children("*", "MeshInstance3D", true, false):
			mesh.material_overlay = _overlay(mesh, season, false)
		var leaves := plant.get_node_or_null("FallingLeaves") as CPUParticles3D
		if leaves:
			leaves.emitting = season != "winter"
			var leaf_material := (leaves.mesh as QuadMesh).material.duplicate() as StandardMaterial3D
			leaf_material.albedo_color = row["leaves"]
			leaves.material_override = leaf_material
	for building in island.buildings.get_children():
		for mesh: MeshInstance3D in building.find_children("*", "MeshInstance3D", true, false):
			mesh.material_overlay = _overlay(mesh, season, true)  # snow, and lit windows at night

	if island.detail == IslandBuilder.Detail.FULL:
		_weather(island, row["weather"])


## The overlay for one mesh in one season (null when the season changes nothing).
static func _overlay(mesh: MeshInstance3D, season: String, building: bool) -> Material:
	var row := look(season)
	var snow: float = row["roof_snow"] if building else row["plant_snow"]
	var foliage: float = 0.0 if building else row["foliage"]
	var blossom: float = 0.0 if building else row["blossom"]
	if not building and snow == 0.0 and foliage == 0.0 and blossom == 0.0:
		return null
	var source := mesh.get_active_material(0) as BaseMaterial3D
	var texture: Texture2D = source.albedo_texture if source else null
	var key := "%s|%s|%s" % [season, building, texture.resource_path if texture else mesh.name]
	if not _overlays.has(key):
		var material := ShaderMaterial.new()
		material.shader = OverlayShader
		material.set_shader_parameter("albedo_tex", texture)
		material.set_shader_parameter("noise", IslandBuilder.noise_texture())
		material.set_shader_parameter("foliage_amount", foliage)
		material.set_shader_parameter("blossom_amount", blossom)
		material.set_shader_parameter("snow_amount", snow)
		material.set_shader_parameter("window_glow", 1.0 if building else 0.0)
		_overlays[key] = material
	return _overlays[key]


## Island-wide weather: one particle emitter over the whole island.
## Godot concept: CPUParticles3D spawns many copies of a small mesh (a quad
## here) and moves each one by gravity and velocity until its lifetime ends.
static func _weather(island: IslandBuilder, kind: String) -> void:
	var old := island.get_node_or_null("Weather")
	if old and old.get_meta("kind", "") == kind:
		return
	if old:
		old.free()
	var p := CPUParticles3D.new()
	p.name = "Weather"
	p.set_meta("kind", kind)
	var quad := QuadMesh.new()
	var material := StandardMaterial3D.new()
	material.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	material.billboard_keep_scale = true
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.vertex_color_use_as_albedo = true
	quad.material = material
	p.mesh = quad
	p.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	p.angle_min = -180.0
	p.angle_max = 180.0
	var fade := Curve.new()  # grow in, hold, shrink out
	fade.add_point(Vector2(0.0, 0.0))
	fade.add_point(Vector2(0.1, 1.0))
	fade.add_point(Vector2(0.85, 1.0))
	fade.add_point(Vector2(1.0, 0.0))
	p.scale_amount_curve = fade
	match kind:
		"snow":
			quad.size = Vector2(0.14, 0.14)
			p.amount = 320
			p.lifetime = 9.0
			p.position = Vector3(0, 12, 0)
			p.emission_box_extents = Vector3(22, 0.5, 16)
			p.gravity = Vector3(0.25, -1.5, 0)
			p.color = Color(1, 1, 1)
			material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		"leaves", "petals":
			quad.size = Vector2(0.22, 0.15) if kind == "leaves" else Vector2(0.16, 0.12)
			p.amount = 40
			p.lifetime = 9.0
			p.position = Vector3(0, 9, 0)
			p.emission_box_extents = Vector3(20, 1.0, 14)
			p.gravity = Vector3(0.5, -0.9, 0.15)
			p.angular_velocity_min = -160.0
			p.angular_velocity_max = 160.0
			var colors := Gradient.new()
			if kind == "leaves":
				colors.set_color(0, Color(0.95, 0.55, 0.15))
				colors.set_color(1, Color(0.85, 0.25, 0.12))
				colors.add_point(0.5, Color(0.98, 0.78, 0.25))
			else:
				colors.set_color(0, Color(1.0, 0.78, 0.88))
				colors.set_color(1, Color(1.0, 0.92, 0.96))
			p.color_initial_ramp = colors
		"fireflies":
			quad.size = Vector2(0.1, 0.1)
			p.amount = 28
			p.lifetime = 4.0
			p.position = Vector3(0, 1.2, 0)
			p.emission_box_extents = Vector3(16, 0.8, 11)
			p.gravity = Vector3.ZERO
			p.direction = Vector3(0, 1, 0)
			p.spread = 180.0
			p.initial_velocity_min = 0.1
			p.initial_velocity_max = 0.4
			p.color = Color(1.0, 0.95, 0.5)
			material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			material.emission_enabled = true
			material.emission = Color(1.0, 0.9, 0.4)
			material.emission_energy_multiplier = 4.0
			p.set_meta("night_only", true)
	island.add_child(p)
