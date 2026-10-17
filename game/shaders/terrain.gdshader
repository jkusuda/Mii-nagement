// The island's ground: grass, sandy paths, the beach, and the pond's banks.
//
// IslandTerrain (scenes/island/island_terrain.gd) builds the mesh and a "field"
// texture laid over the island: R = distance from the coast in world units
// (+ on land), G = how much path is there. This shader reads the field at each
// pixel's world position to decide its color, so the colors line up exactly
// with the shapes the mesh was built from.
//
// Seasons (scenes/island/seasons.gd) change the grass colors and the snow.

shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx;

uniform sampler2D field : filter_linear, repeat_disable;
uniform vec4 field_rect;  // x, z of the field's corner, then its width and depth (island units)
uniform vec2 pond_center;  // island units (the island node's own space, see local_position)
uniform vec2 pond_half = vec2(-1.0);  // negative = no pond
uniform float pond_radius = 1.4;

// --- Colors (the summer look; Seasons sets others) --------------------------
uniform vec3 grass_light : source_color = vec3(0.56, 0.86, 0.25);
uniform vec3 grass_dark : source_color = vec3(0.42, 0.76, 0.2);
uniform vec3 clover : source_color = vec3(0.66, 0.9, 0.38);
uniform vec3 grass_edge : source_color = vec3(0.35, 0.62, 0.18);
uniform vec3 path_color : source_color = vec3(0.98, 0.9, 0.74);
uniform vec3 path_edge : source_color = vec3(0.86, 0.76, 0.58);
uniform vec3 sand : source_color = vec3(0.98, 0.92, 0.78);
uniform vec3 wet_sand : source_color = vec3(0.84, 0.76, 0.6);
uniform vec3 pond_bank : source_color = vec3(0.7, 0.62, 0.45);
uniform float snow = 0.0;      // 0 = none, 1 = everything flat is white
uniform vec3 snow_color : source_color = vec3(0.95, 0.97, 1.0);

uniform sampler2D noise;  // seamless noise, for color variation and clovers

varying vec3 world_position;
varying vec3 local_position;  // relative to the island, which may sit anywhere in a sea of islands
varying vec3 world_normal;


float pond_distance(vec2 p) {
	if (pond_half.x < 0.0) return -100.0;
	float r = min(pond_radius, min(pond_half.x, pond_half.y));
	vec2 q = abs(p - pond_center) - pond_half + r;
	return -(length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r);
}


void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	local_position = VERTEX;
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
}


void fragment() {
	vec2 p = local_position.xz;
	vec2 uv = (p - field_rect.xy) / field_rect.zw;
	vec2 f = texture(field, uv).rg;
	float coast = f.r;
	float n = texture(noise, p * 0.035).r;
	float n_fine = texture(noise, p * 0.21).r;

	// Grass: two greens blended by soft noise, with lighter clover patches.
	vec3 grass = mix(grass_dark, grass_light, smoothstep(0.3, 0.7, n));
	float clovers = smoothstep(0.66, 0.7, texture(noise, p * 0.32 + 7.0).r);
	grass = mix(grass, clover, clovers * 0.55);
	// The grass darkens a little where it rounds over the edge.
	grass = mix(grass_edge, grass, smoothstep(0.05, 0.6, coast));

	// Sand: wet and darker just above the waterline.
	vec3 beach = mix(wet_sand, sand, smoothstep(-1.35, -0.95, local_position.y));
	beach *= 0.96 + 0.08 * n_fine;

	vec3 color = mix(beach, grass, smoothstep(-0.05, 0.12, coast + (n_fine - 0.5) * 0.12));

	// Paths, with a slightly darker rim.
	float path = smoothstep(0.42, 0.52, f.g) * step(0.0, coast);
	float rim = smoothstep(0.42, 0.5, f.g) - smoothstep(0.5, 0.6, f.g);
	vec3 path_col = mix(path_color, path_edge, rim * 0.7) * (0.97 + 0.06 * n_fine);
	color = mix(color, path_col, path);

	// The pond's muddy banks (the water itself is another mesh above them).
	color = mix(color, pond_bank, smoothstep(-0.35, 0.1, pond_distance(p)));

	// Snow settles on flat ground (not on the steep beach edge); the paths stay
	// swept clear, so you can still see where everyone walks.
	float level = smoothstep(0.75, 0.95, world_normal.y);
	float snow_here = snow * level * smoothstep(0.25, 0.55, n_fine + snow * 0.6) * step(-0.2, coast);
	snow_here *= 1.0 - path * 0.8;
	color = mix(color, snow_color, clamp(snow_here, 0.0, 1.0));

	ALBEDO = color;
	ROUGHNESS = mix(0.95, 0.8, snow_here);
	SPECULAR = 0.25;
}
