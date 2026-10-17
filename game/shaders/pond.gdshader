// Animated shimmer for the pond in the middle of the island.
//
// The pond itself (blue water, lily pads, cloud reflections) is painted into
// the island model's texture. This shader goes on a flat plane that floats just
// above that painted water and only *adds light*: drifting shimmer lines and
// ripple rings, as if a fish just poked its nose up. Everything else stays
// see-through, so the painted pond shows underneath.
//
// The pond sits in a dip in the island, and on top of that the effect is
// masked to the pond's kidney shape (two overlapping ovals, in world units),
// so it can never show up anywhere but the water.
//
// Godot concept: `blend_add` means "add this pixel's color on top of what's
// already there" (like shining a light), so black = invisible, white = bright.

shader_type spatial;
render_mode blend_add, unshaded, depth_draw_never, shadows_disabled;

uniform vec3 tint : source_color = vec3(0.85, 1.0, 1.0);

// --- Shimmer (wobbly bright lines drifting across the water) ----------------
uniform float shimmer_strength = 0.2;
uniform float shimmer_scale = 0.12;  // smaller = bigger, calmer pattern
uniform float shimmer_speed = 0.03;

// --- Ripple rings (world x/z of where they start, matches island_3d.tscn) ----
uniform vec2 ripple_spot_1 = vec2(-2.0, 0.2);
uniform vec2 ripple_spot_2 = vec2(1.2, -0.4);
uniform vec2 ripple_spot_3 = vec2(-1.2, 2.0);
uniform float ripple_strength = 0.45;
uniform float ripple_radius = 1.2;   // how big a ring gets before it fades
uniform float ripple_speed = 0.35;   // rings per second, per spot
uniform float ripple_width = 0.06;

// --- Pond outline: the water is roughly two overlapping ovals -----------------
uniform vec2 left_oval_center = vec2(-2.5, 0.85);
uniform vec2 left_oval_radius = vec2(2.6, 2.3);
uniform vec2 right_oval_center = vec2(1.7, 0.35);
uniform vec2 right_oval_radius = vec2(1.65, 1.75);

uniform sampler2D noise;  // seamless noise texture

varying vec3 world_position;


// A pseudo-random number in 0..1 for any input number.
float hash(float n) {
	return fract(sin(n * 127.1) * 43758.5453);
}


void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}


void fragment() {
	vec2 p = world_position.xz;
	float t = TIME * shimmer_speed;

	// 1. Shimmer: two noise layers sliding in different directions. Where they
	//    have nearly the same value we draw a thin bright line, which gives
	//    the wobbly "sunlight on water" look.
	float n1 = texture(noise, p * shimmer_scale + vec2(t, t * 0.7)).r;
	float n2 = texture(noise, p * shimmer_scale * 1.3 - vec2(t * 0.6, t * 1.1)).r;
	float shimmer = 1.0 - smoothstep(0.0, 0.06, abs(n1 - n2));
	float light = shimmer * shimmer_strength;

	// 2. Ripple rings: each spot repeatedly spawns a ring that grows and fades.
	//    Every new ring gets a slightly different center so it doesn't look
	//    like a loop.
	vec2 spots[3] = vec2[](ripple_spot_1, ripple_spot_2, ripple_spot_3);
	for (int i = 0; i < 3; i++) {
		float cycle = TIME * ripple_speed + float(i) * 0.37;
		float phase = fract(cycle);  // 0 -> 1 while the ring grows, then repeats
		float seed = floor(cycle) + float(i) * 13.0;
		vec2 center = spots[i] + (vec2(hash(seed), hash(seed + 5.0)) - 0.5) * 1.2;
		float d = length(p - center);
		float ring = 1.0 - smoothstep(0.0, ripple_width, abs(d - phase * ripple_radius));
		light += ring * (1.0 - phase) * ripple_strength;
	}

	// 3. Mask to the pond: 1 inside either oval, fading to 0 at the edge.
	//    length(p / radius) is 1 exactly on an oval's outline.
	float left = length((p - left_oval_center) / left_oval_radius);
	float right = length((p - right_oval_center) / right_oval_radius);
	float inside = 1.0 - smoothstep(0.85, 1.0, min(left, right));

	ALBEDO = tint;
	ALPHA = clamp(light * inside, 0.0, 1.0);
}
