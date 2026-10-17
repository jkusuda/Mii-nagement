// Stylized water for the island.
//
// Godot concept: a *shader* is a small program that runs on the GPU for every
// pixel (fragment) of a mesh, every frame. It decides the pixel's color.
// `uniform`s are settings you can tweak in the Inspector (select Water, then
// Surface Material Override > Shader Parameters).
//
// How it works: every island comes with a "field" texture (island_terrain.gd)
// that stores, for each point around it, how far that point is from the coast.
// So for every water pixel we know its distance to the shore. Close to an
// island = light shallow water + white foam; far away = deep turquoise.
// Scrolling noise textures add moving ripples, sparkles, and wave streaks.

shader_type spatial;

// --- Colors -----------------------------------------------------------------
uniform vec3 deep_color : source_color = vec3(0.12, 0.55, 0.68);
uniform vec3 shallow_color : source_color = vec3(0.42, 0.82, 0.80);
uniform vec3 foam_color : source_color = vec3(1.0, 1.0, 1.0);

// --- Islands (set from code: IslandBuilder, or SeaView for several islands) --
// island_count = 0 means no islands at all, just open sea.
uniform int island_count = 0;
uniform sampler2D island_fields[8] : filter_linear, repeat_disable;
uniform vec4 island_rects[8];     // each field's x, z corner and its width, depth (world units)
uniform float beach_width = 2.6;  // how far past the grass edge the sea starts

// --- Look -------------------------------------------------------------------
uniform float shallow_width = 7.0;   // how far the light shallow water reaches
uniform float shore_foam_width = 0.45;
uniform float ring_count = 2.0;      // white rings moving out from the shore
uniform float ring_reach = 5.0;      // how far the rings travel before fading
uniform float ring_speed = 0.12;
uniform float streak_amount = 0.35;   // 0 = no wave streaks in open water
uniform float wave_speed = 0.02;

uniform sampler2D ripple_normal : hint_normal;  // seamless noise normal map
uniform sampler2D foam_noise;                   // seamless noise, breaks up foam

varying vec3 world_position;


// Island i's field at uv. WebGL (the web export) only reads a sampler array at a
// constant index, so a switch instead of island_fields[i].
float field_at(int i, vec2 uv) {
	switch (i) {
		case 0: return texture(island_fields[0], uv).r;
		case 1: return texture(island_fields[1], uv).r;
		case 2: return texture(island_fields[2], uv).r;
		case 3: return texture(island_fields[3], uv).r;
		case 4: return texture(island_fields[4], uv).r;
		case 5: return texture(island_fields[5], uv).r;
		case 6: return texture(island_fields[6], uv).r;
		default: return texture(island_fields[7], uv).r;
	}
}


// Distance from p to island i's shore (where its beach meets the sea).
// Outside the island's field, keep counting from the field's edge.
float shore_distance(int i, vec2 p) {
	vec4 rect = island_rects[i];
	vec2 uv = (p - rect.xy) / rect.zw;
	vec2 inside_uv = clamp(uv, vec2(0.002), vec2(0.998));
	float coast = field_at(i, inside_uv);  // + on land, in world units
	float beyond = length((uv - inside_uv) * rect.zw);
	return -coast - beach_width + beyond;
}


void vertex() {
	// MODEL_MATRIX turns the mesh's own coordinates into world coordinates.
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}


void fragment() {
	vec2 p = world_position.xz;
	float t = TIME * wave_speed;

	// Noise, sampled at world positions so it tiles across the whole plane.
	float breakup = texture(foam_noise, p * 0.06 + vec2(t, t * 0.6)).r;
	float breakup_fine = texture(foam_noise, p * 0.2 - vec2(t * 1.5, 0.0)).r;

	// Distance to the nearest island's shore. Wobble it a little so the shore
	// lines aren't perfectly smooth (the coast itself already wobbles).
	float d = 1000.0;
	for (int i = 0; i < island_count; i++) {
		d = min(d, shore_distance(i, p));
	}
	d += (breakup - 0.5) * 0.6;

	// 1. Shallow -> deep color.
	float depth = smoothstep(0.0, shallow_width, d);
	vec3 color = mix(shallow_color, deep_color, depth);

	// 2. Foam hugging the shore.
	float foam = 1.0 - smoothstep(shore_foam_width * 0.6, shore_foam_width, d);

	// 3. Rings that drift outward from the shore and fade.
	for (float i = 0.0; i < ring_count; i += 1.0) {
		float travel = fract(TIME * ring_speed + i / ring_count);  // 0 -> 1, then repeats
		float ring_distance = 1.2 + travel * ring_reach;
		float ring = 1.0 - smoothstep(0.03, 0.14, abs(d - ring_distance));
		ring *= (1.0 - travel) * 0.8 * step(0.5, breakup_fine);         // fade out, break into dashes
		foam = max(foam, ring);
	}

	// 4. Sparse white wave streaks in open water.
	vec2 streak_uv = vec2(p.x * 0.008, p.y * 0.06) + vec2(t * 0.5, 0.0);
	float streak = smoothstep(0.8, 0.83, texture(foam_noise, streak_uv).r);
	foam = max(foam, streak * streak_amount * smoothstep(3.0, 9.0, d));

	color = mix(color, foam_color, clamp(foam, 0.0, 1.0) * 0.9);

	ALBEDO = color;
	ROUGHNESS = mix(0.15, 0.6, foam);  // water is shiny, foam is matte
	SPECULAR = 0.4;
	// Two ripple layers moving in different directions, for sparkle.
	vec3 n1 = texture(ripple_normal, p * 0.08 + vec2(t, t)).rgb;
	vec3 n2 = texture(ripple_normal, p * 0.11 - vec2(t * 0.8, t * 1.3)).rgb;
	NORMAL_MAP = mix(n1, n2, 0.5);
	NORMAL_MAP_DEPTH = 0.35;
}
