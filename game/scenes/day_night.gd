class_name DayNight
extends Node
## Day/night cycle for the 3D island: moves the sun and recolors the light,
## ambient light, background, and water to match Clock.hour() (real time, or the
## dev slider). Lives in island_3d.tscn as the "DayNight" node, so the home
## screen's sea (which reuses island_3d.tscn) gets it too. The 2D screen
## backgrounds follow the same table (scenes/ui/sky_background.gd).
##
## The look at noon is whatever the scene has (Sun's angle in island_3d.tscn), so
## you can still tune daytime in the editor. At night the same light becomes a
## dim blue "moon" on its own arc.
##
## The water is shiny, so it mostly shows a reflection of "background": that's
## why background matters even though you barely see it directly.
##
## Godot concept: _process(delta) runs every frame. Recoloring a few lights each
## frame is cheap, and it keeps the dev slider smooth.
##
## How dark it is (0 = day, 1 = night) is also published for everything that
## lights up at night: DayNight.night for scripts (building porch lights, path
## lamps) and the global shader parameter `night` for shaders (glowing windows,
## lamp heads). Godot concept: a *global* shader parameter (Project Settings >
## Shader Globals) is one value every shader can read.

const NIGHT := {
	"sun": Color(0.7, 0.78, 1.0), "sun_energy": 0.5,
	"ambient": Color(0.45, 0.5, 0.75), "ambient_energy": 0.4,
	"background": Color(0.16, 0.27, 0.48),
	"deep": Color(0.1, 0.3, 0.48), "shallow": Color(0.22, 0.45, 0.6), "foam": Color(0.7, 0.78, 0.95),
	"screen": Color(0.13, 0.2, 0.38), "night": 1.0,
}
const DAY := {
	"sun": Color(1.0, 0.97, 0.9), "sun_energy": 1.9,
	"ambient": Color(0.76, 0.87, 1.0), "ambient_energy": 0.55,
	"background": Color(0.13, 0.7, 0.8),
	"deep": Color(0.12, 0.55, 0.68), "shallow": Color(0.42, 0.82, 0.8), "foam": Color(1, 1, 1),
	"screen": Color(0.412, 0.831, 0.922), "night": 0.0,
}
## "screen" is the 2D page background, "night" how dark it is (0 = day,
## 1 = night) for 2D bits that need to stay readable (night_label.gd).
## The look at each hour; between two rows the values blend, 24 wraps to 0.
## (Day matches the colors set in island_3d.tscn / game.tscn.) The sun's energy
## dips at 6:00 and 18:00, where it swaps over to the moon.
const KEYS := [
	[0.0, NIGHT],
	[5.5, NIGHT],
	[6.0, {"sun": Color(0.9, 0.7, 0.75), "sun_energy": 0.15,
		"ambient": Color(0.6, 0.55, 0.75), "ambient_energy": 0.38,
		"background": Color(0.36, 0.4, 0.55),
		"deep": Color(0.1, 0.27, 0.42), "shallow": Color(0.35, 0.42, 0.58), "foam": Color(0.85, 0.8, 0.88),
		"screen": Color(0.36, 0.42, 0.6), "night": 0.7}],
	[7.0, {"sun": Color(1.0, 0.72, 0.5), "sun_energy": 1.1,
		"ambient": Color(0.95, 0.78, 0.75), "ambient_energy": 0.45,
		"background": Color(0.7, 0.72, 0.8),
		"deep": Color(0.13, 0.42, 0.56), "shallow": Color(0.55, 0.7, 0.72), "foam": Color(1.0, 0.94, 0.9),
		"screen": Color(0.6, 0.72, 0.85), "night": 0.2}],
	[9.5, DAY],
	[16.5, DAY],
	[17.5, {"sun": Color(1.0, 0.62, 0.4), "sun_energy": 1.1,
		"ambient": Color(0.9, 0.66, 0.7), "ambient_energy": 0.45,
		"background": Color(0.78, 0.62, 0.65),
		"deep": Color(0.13, 0.35, 0.52), "shallow": Color(0.58, 0.58, 0.66), "foam": Color(1.0, 0.88, 0.85),
		"screen": Color(0.62, 0.58, 0.78), "night": 0.2}],
	[18.0, {"sun": Color(0.85, 0.6, 0.7), "sun_energy": 0.15,
		"ambient": Color(0.55, 0.48, 0.72), "ambient_energy": 0.38,
		"background": Color(0.38, 0.34, 0.54),
		"deep": Color(0.08, 0.24, 0.4), "shallow": Color(0.3, 0.36, 0.55), "foam": Color(0.8, 0.75, 0.88),
		"screen": Color(0.32, 0.32, 0.55), "night": 0.7}],
	[18.5, NIGHT],
	[24.0, NIGHT],
]
## Lowest the sun/moon gets, so shadows never stretch across the whole island.
const MIN_ELEVATION := deg_to_rad(20.0)
## How far the sun swings east/west of its noon direction.
const SWING := deg_to_rad(70.0)

@onready var sun: DirectionalLight3D = $"../Sun"
@onready var env: Environment = $"../WorldEnvironment".environment
@onready var water: MeshInstance3D = get_node_or_null("../Water")  # thumbnails have none

## How dark it is right now: 0 = day, 1 = night (see the top of this file).
static var night := 0.0

var _noon_elevation := 0.0
var _noon_azimuth := 0.0


## The blended look at this hour (0-24): a dictionary like NIGHT / DAY.
static func at(h: float) -> Dictionary:
	var i := 0
	while KEYS[i + 1][0] <= h and i < KEYS.size() - 2:
		i += 1
	var a: Dictionary = KEYS[i][1]
	var b: Dictionary = KEYS[i + 1][1]
	var t := clampf((h - KEYS[i][0]) / (KEYS[i + 1][0] - KEYS[i][0]), 0.0, 1.0)
	var out := {}
	for key in a:
		out[key] = a[key].lerp(b[key], t) if a[key] is Color else lerpf(a[key], b[key], t)
	return out


func _ready() -> void:
	# Remember the angle the scene was designed with; that's the noon sun.
	# (A light shines along its -Z; rotation.x below 0 tilts it down.)
	_noon_elevation = -sun.rotation.x
	_noon_azimuth = sun.rotation.y


func _process(_delta: float) -> void:
	var h := Clock.hour()

	# Daytime (6-18) the sun crosses the sky; nighttime (18-6) the moon does.
	var arc := fposmod(h - 6.0, 12.0) / 12.0  # 0 = rising, 0.5 = highest, 1 = setting
	sun.rotation.x = -lerpf(MIN_ELEVATION, _noon_elevation, sin(arc * PI))
	sun.rotation.y = _noon_azimuth + lerpf(SWING, -SWING, arc)

	var look := at(h)
	# The island's season tints the light a little (golden autumn, crisp winter).
	var island := get_node_or_null("../Island") as IslandBuilder
	var tint: Color = Seasons.look(island.layout.season)["light"] if island and island.layout else Color.WHITE
	sun.light_color = look["sun"] * tint
	sun.light_energy = look["sun_energy"]
	env.ambient_light_color = look["ambient"] * tint
	env.ambient_light_energy = look["ambient_energy"]
	env.background_color = look["background"]
	# Windows and lamps glow a little more at night.
	env.glow_intensity = lerpf(0.35, 0.9, look["night"])
	night = look["night"]
	RenderingServer.global_shader_parameter_set("night", night)
	# The home screen's sea swaps in its own copy of the water material
	# (material_override), so color whichever one is showing.
	var material := water.get_active_material(0) as ShaderMaterial if water else null
	if material:
		material.set_shader_parameter("deep_color", look["deep"])
		material.set_shader_parameter("shallow_color", look["shallow"])
		material.set_shader_parameter("foam_color", look["foam"])
