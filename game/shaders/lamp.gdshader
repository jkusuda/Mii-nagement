// The glowing head of a path lamp. It lights up as night falls, each lamp at
// its own moment (switch_on), so they come on one after another at dusk.
//
// Godot concepts:
// - `global uniform float night` is shared by every shader (Project Settings >
//   Shader Globals); scenes/island/day_night.gd sets it every frame.
// - an `instance uniform` can differ per mesh node while they all share one
//   material: IslandBuilder gives each lamp its own switch_on.

shader_type spatial;

global uniform float night;
instance uniform float switch_on = 0.5;
uniform vec3 off_color : source_color = vec3(0.93, 0.9, 0.8);
uniform vec3 glow_color : source_color = vec3(1.0, 0.8, 0.45);

void fragment() {
	float on = smoothstep(switch_on, switch_on + 0.08, night);
	ALBEDO = mix(off_color, glow_color, on);
	EMISSION = glow_color * on * 3.0;
	ROUGHNESS = 0.4;
}
