// A Mii's body: skin, shirt (the resident's colour), trousers, shoes, and a face
// painted onto the front of the head from the Mii Maker part atlases.
//
// mii.gd bakes two extra values into each vertex of the model, measured in the
// rest pose, so everything stays stuck to the body while it animates:
//   CUSTOM0 = position inside the head box (-1..1 on each axis, +z = the face), head weight
//   CUSTOM1 = rest height in metres, skin weight (hands), arm weight
shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx;

uniform vec3 skin_color : source_color = vec3(0.97, 0.82, 0.69);
uniform vec3 shirt_color : source_color = vec3(0.61, 0.53, 0.96);
uniform vec3 hair_color : source_color = vec3(0.23, 0.14, 0.09);
uniform vec3 trousers_color : source_color = vec3(0.20, 0.21, 0.29);
uniform vec3 shoe_color : source_color = vec3(0.33, 0.25, 0.20);
uniform vec3 ink_color : source_color = vec3(0.07, 0.05, 0.05);

// Heights in metres, from the skeleton (mii.gd).
uniform float waist_y = 0.70;
uniform float ankle_y = 0.11;

// Part atlases: a grid of styles, `grid` = (columns, rows). The index picks one.
uniform sampler2D eyes_tex : source_color, filter_linear_mipmap, repeat_disable;
uniform sampler2D brows_tex : source_color, filter_linear_mipmap, repeat_disable;
uniform sampler2D nose_tex : source_color, filter_linear_mipmap, repeat_disable;
uniform sampler2D mouth_tex : source_color, filter_linear_mipmap, repeat_disable;
uniform vec2 eyes_grid = vec2(8.0, 8.0);
uniform vec2 brows_grid = vec2(8.0, 3.0);
uniform vec2 nose_grid = vec2(8.0, 3.0);
uniform vec2 mouth_grid = vec2(8.0, 5.0);
uniform int eyes = 0;
uniform int brows = 0;
uniform int nose = 0;
uniform int mouth = 0;

// Where the parts sit, in head units (the head spans -1..1 across and up).
// `*_size` is half the width of the part's atlas square.
uniform float eye_spacing = 0.3;
uniform float eye_height = -0.1;
uniform float eye_size = 0.32;
uniform float brow_spacing = 0.3;
uniform float brow_height = 0.1;
uniform float brow_size = 0.24;
uniform float nose_height = -0.32;
uniform float nose_size = 0.2;
uniform float mouth_height = -0.52;
uniform float mouth_size = 0.3;
uniform float blink : hint_range(0.0, 1.0) = 0.0;

varying vec4 v_head;
varying vec3 v_body;

void vertex() {
	v_head = CUSTOM0;
	v_body = CUSTOM1.xyz;
}

// One style from an atlas. p is the point in the part's square, -1..1 with +y up.
vec4 part(sampler2D tex, vec2 grid, int index, vec2 p) {
	vec2 uv = vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5);
	float inside = step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);
	int cols = int(grid.x);
	vec2 cell = vec2(float(index % cols), float(index / cols));
	return texture(tex, (cell + clamp(uv, 0.0, 1.0)) / grid) * inside;
}

void fragment() {
	float y = v_body.x;
	float aa = fwidth(y) * 1.5 + 1e-4;

	// Body: shirt, then trousers below the waist and shoes below the ankles.
	// Arms hang below the waist too, so they keep their sleeves.
	float legs = 1.0 - smoothstep(0.3, 0.6, v_body.z);
	vec3 col = shirt_color;
	col = mix(col, trousers_color, (1.0 - smoothstep(waist_y - aa, waist_y + aa, y)) * legs);
	col = mix(col, shoe_color, (1.0 - smoothstep(ankle_y - aa, ankle_y + aa, y)) * legs);
	col = mix(col, skin_color, smoothstep(0.35, 0.65, v_body.y));
	float rough = 0.85;

	float head_w = v_head.w;
	if (head_w > 0.01) {
		vec2 f = v_head.xy;
		// Only paint on the front of the head, fading out around the sides.
		float front = smoothstep(0.05, 0.35, v_head.z);
		vec3 h = skin_color;

		// Nose: a soft shadow line.
		vec4 n = part(nose_tex, nose_grid, nose, (f - vec2(0.0, nose_height)) / nose_size);
		h = mix(h, h * 0.55, n.a * front);

		// Mouth: ink, with white teeth.
		vec4 m = part(mouth_tex, mouth_grid, mouth, (f - vec2(0.0, mouth_height)) / mouth_size);
		h = mix(h, mix(ink_color, vec3(0.97), m.r), m.a * front);

		// Eyes: the atlas draws the viewer's right eye, so abs() mirrors it for the left.
		// Blinking squashes them flat.
		vec2 e = vec2(abs(f.x) - eye_spacing, f.y - eye_height) / eye_size;
		e.y /= max(1.0 - blink, 0.08);
		vec4 ey = part(eyes_tex, eyes_grid, eyes, e);
		h = mix(h, mix(ink_color, vec3(0.98), ey.r), ey.a * front);

		// Brows, mirrored the same way, in the hair colour.
		vec2 b = vec2(abs(f.x) - brow_spacing, f.y - brow_height) / brow_size;
		vec4 br = part(brows_tex, brows_grid, brows, b);
		h = mix(h, hair_color * 0.8, br.a * front);

		col = mix(col, h, smoothstep(0.3, 0.7, head_w));
		rough = mix(rough, 0.6, head_w);
	}

	ALBEDO = col;
	ROUGHNESS = rough;
	SPECULAR = 0.3;
	RIM = 0.15;
	RIM_TINT = 0.6;
}
