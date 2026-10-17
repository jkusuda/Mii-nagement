// The pond's water surface.
//
// IslandBuilder puts a flat plane over the pond's hollow in the ground, a bit
// below the grass, so the grass banks hide its edges. This shader colors it:
// deeper in the middle, lighter at the edges, with drifting shimmer lines and
// ripple rings, as if a fish just poked its nose up. In winter (`ice` = 1) the
// pond freezes over: pale and still.
//
// The pond's shape is a rounded rectangle (pond_center / pond_half, x/z in the
// island's own space), the same one the terrain was pressed in with.

shader_type spatial;

uniform vec3 deep_color : source_color = vec3(0.12, 0.6, 0.78);
uniform vec3 shallow_color : source_color = vec3(0.4, 0.82, 0.88);
uniform vec3 ice_color : source_color = vec3(0.82, 0.92, 0.98);
uniform vec2 pond_center;
uniform vec2 pond_half = vec2(2.0, 1.0);
uniform vec2 pond_offset;  // where the pond mesh sits on the island (x, z): set by IslandBuilder
uniform float pond_radius = 1.4;
uniform float ice = 0.0;

// --- Shimmer (wobbly bright lines drifting across the water) ----------------
uniform float shimmer_strength = 0.2;
uniform float shimmer_scale = 0.12;  // smaller = bigger, calmer pattern
uniform float shimmer_speed = 0.03;

// --- Ripple rings -----------------------------------------------------------
uniform float ripple_strength = 0.45;
uniform float ripple_radius = 1.2;   // how big a ring gets before it fades
uniform float ripple_speed = 0.35;   // rings per second, per spot
uniform float ripple_width = 0.06;

uniform sampler2D noise;  // seamless noise texture

varying vec3 island_position;  // relative to the island (it may sit anywhere in a sea of islands)


// A pseudo-random number in 0..1 for any input number.
float hash(float n) {
	return fract(sin(n * 127.1) * 43758.5453);
}


float pond_distance(vec2 p) {
	float r = min(pond_radius, min(pond_half.x, pond_half.y));
	vec2 q = abs(p - pond_center) - pond_half + r;
	return -(length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r);
}


void vertex() {
	// VERTEX is relative to the pond mesh, which IslandBuilder places at
	// pond_offset on the island: adding it gives a position on the island.
	island_position = VERTEX + vec3(pond_offset.x, 0.0, pond_offset.y);
}


void fragment() {
	vec2 p = island_position.xz;
	float t = TIME * shimmer_speed;
	float inside = pond_distance(p);  // + inside the pond, in world units

	// 1. Lighter near the banks, deeper in the middle.
	vec3 color = mix(shallow_color, deep_color, smoothstep(0.0, 1.6, inside));

	// 2. Shimmer: two noise layers sliding in different directions. Where they
	//    have nearly the same value we draw a thin bright line.
	float n1 = texture(noise, p * shimmer_scale + vec2(t, t * 0.7)).r;
	float n2 = texture(noise, p * shimmer_scale * 1.3 - vec2(t * 0.6, t * 1.1)).r;
	float light = (1.0 - smoothstep(0.0, 0.06, abs(n1 - n2))) * shimmer_strength;

	// 3. Ripple rings: three spots inside the pond, each spawning rings that
	//    grow and fade. Every new ring starts somewhere new.
	for (int i = 0; i < 3; i++) {
		float cycle = TIME * ripple_speed + float(i) * 0.37;
		float phase = fract(cycle);  // 0 -> 1 while the ring grows, then repeats
		float seed = floor(cycle) + float(i) * 13.0;
		vec2 spot = pond_center + (vec2(hash(seed), hash(seed + 5.0)) - 0.5) * 2.0 * max(pond_half - 0.8, vec2(0.1));
		float ring = 1.0 - smoothstep(0.0, ripple_width, abs(length(p - spot) - phase * ripple_radius));
		light += ring * (1.0 - phase) * ripple_strength;
	}
	color += vec3(0.85, 1.0, 1.0) * clamp(light, 0.0, 1.0) * (1.0 - ice);

	// 4. Ice: pale, with faint cracks from the same noise.
	float cracks = 1.0 - smoothstep(0.0, 0.03, abs(n1 - 0.5));
	vec3 frozen = mix(ice_color, vec3(1.0), cracks * 0.5);
	color = mix(color, frozen, ice);

	ALBEDO = color;
	ROUGHNESS = mix(0.08, 0.35, ice);
	SPECULAR = 0.5;
}
