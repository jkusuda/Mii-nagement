// Seasonal touches drawn over trees, bushes, and buildings: autumn colors,
// spring blossoms, and winter snow. On buildings it also lights the windows
// and doors at night (window_glow), following the global `night` value
// (scenes/island/day_night.gd).
//
// Godot concept: a GeometryInstance3D (any mesh node) has a `material_overlay`:
// a second material drawn on top of the mesh's own. Seasons (scenes/island/seasons.gd)
// sets one per plant and building, so each island can have its own season
// while the models keep their original textures underneath.
//
// Where this draws nothing (ALPHA = 0), the model shows as it is. To know where
// the leaves are, it reads the model's own texture (albedo_tex) and looks for
// green.

shader_type spatial;
render_mode blend_mix, depth_draw_never, diffuse_burley;

uniform sampler2D albedo_tex : source_color, hint_default_white;
uniform sampler2D noise;
// Autumn: greens turn orange, red, or gold (a different mix on each plant).
uniform float foliage_amount = 0.0;
uniform vec3 foliage_a : source_color = vec3(0.98, 0.55, 0.16);
uniform vec3 foliage_b : source_color = vec3(0.88, 0.26, 0.14);
uniform vec3 foliage_c : source_color = vec3(0.98, 0.78, 0.24);
// Spring: little blossoms dotted over the leaves.
uniform float blossom_amount = 0.0;
uniform vec3 blossom_color : source_color = vec3(1.0, 0.6, 0.8);
// Winter: snow on everything that faces up (roofs, the tops of trees).
uniform float snow_amount = 0.0;
uniform vec3 snow_color : source_color = vec3(0.96, 0.97, 1.0);
// Night: windows (glass-blue texels) and doors glow warm. 1 on buildings, 0 on plants.
uniform float window_glow = 0.0;
uniform vec3 window_color : source_color = vec3(1.0, 0.78, 0.42);
global uniform float night;

varying vec3 world_normal;
varying vec3 object_position;


float hash(vec3 p) {
	return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
}


void vertex() {
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
	object_position = VERTEX;
}


void fragment() {
	vec3 base = texture(albedo_tex, UV).rgb;
	float green = smoothstep(0.03, 0.18, base.g - max(base.r, base.b));
	float light = dot(base, vec3(0.3, 0.59, 0.11));

	// Each plant picks its own autumn color from where it stands.
	float pick = hash(floor(NODE_POSITION_WORLD * 0.7));
	vec3 autumn = pick < 0.5 ? foliage_a : (pick < 0.8 ? foliage_b : foliage_c);
	vec4 color = vec4(autumn * (0.55 + light * 0.9), green * foliage_amount);

	// Blossom dots, from noise laid over the model's texture (they stick to the plant).
	float n = texture(noise, UV * 7.0).r;
	float blossom = green * blossom_amount * smoothstep(0.6, 0.63, n);
	color = mix(color, vec4(blossom_color, 1.0), blossom);

	// Snow on surfaces that face up, a little patchy.
	float up = smoothstep(0.35, 0.75, world_normal.y);
	float patches = texture(noise, object_position.xz * 1.5).r;
	float snow = snow_amount * up * smoothstep(0.2, 0.45, patches + 0.25);
	color = mix(color, vec4(snow_color, 1.0), snow);

	// Windows: blue glass in the texture, and only on walls (not roofs).
	float glass = smoothstep(0.12, 0.25, base.b - base.r) * smoothstep(-0.02, 0.06, base.b - base.g);
	float lit = window_glow * glass * (1.0 - up) * smoothstep(0.3, 0.8, night);
	color = mix(color, vec4(window_color, 1.0), lit);

	ALBEDO = color.rgb;
	ALPHA = clamp(color.a, 0.0, 1.0);
	EMISSION = window_color * lit * 2.5;
	ROUGHNESS = 0.85;
}
