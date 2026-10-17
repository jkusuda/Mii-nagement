class_name SpeechBubble
extends Node3D
## A white speech bubble over a resident's head: a rounded pill with a small tail,
## dark text and any resident's name picked out in orange.
##
## Godot concept: a SubViewport is a tiny hidden screen. We lay the bubble out with
## normal 2D nodes inside it and show its picture on a billboard Sprite3D, so the
## 2D look (rounded corners, shadow, rich text) lives in the 3D island. The node's
## origin is the tip of the tail: put it just above the head.

const FONT := preload("res://assets/font/DreamLife-V1.0.0_by_MaxiGamer.ttf")
const INK := Color("292E50")  # the theme's dark text colour
const NAME_COLOR := Color("E07B39")
const WRAP := 460  # px: the widest the text gets before it wraps
const PAD := Vector2(36, 20)  # between the text and the edge of the pill
const MARGIN := 18  # room around the pill for the shadow
const TAIL := Vector2(28, 22)  # width and height of the tail
const MAX_RADIUS := 60  # corner radius cap, so tall bubbles stay rounded rectangles
const PIXEL := 0.016  # world units per bubble pixel

var _tween: Tween
var _viewport: SubViewport
var _body: Control
var _text: RichTextLabel
var _sprite: Sprite3D
var _pill := Rect2()  # the white pill inside the viewport


func _ready() -> void:
	_viewport = SubViewport.new()
	_viewport.transparent_bg = true
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	add_child(_viewport)

	_body = Control.new()
	_body.draw.connect(_draw_body)  # signal: runs _draw_body whenever the Control redraws
	_viewport.add_child(_body)

	_text = RichTextLabel.new()
	_text.bbcode_enabled = true
	_text.scroll_active = false
	_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART  # wraps at the width _layout gives it
	_text.add_theme_font_override("normal_font", FONT)
	_text.add_theme_font_override("bold_font", FONT)
	_text.add_theme_color_override("default_color", INK)
	_body.add_child(_text)

	_sprite = Sprite3D.new()
	_sprite.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	_sprite.no_depth_test = true
	_sprite.render_priority = 2
	_sprite.pixel_size = PIXEL
	_sprite.centered = false
	_sprite.texture = _viewport.get_texture()
	add_child(_sprite)
	visible = false


## Show `line` for `seconds`: pop in, wait, fade out.
func say(line: String, seconds := 4.0) -> void:
	if _tween:
		_tween.kill()
	_layout(line)
	_sprite.modulate.a = 1.0
	scale = Vector3.ONE * 0.6
	visible = true
	_tween = create_tween()
	_tween.tween_property(self, "scale", Vector3.ONE, 0.25).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	_tween.tween_interval(max(seconds - 0.85, 0.2))
	_tween.tween_property(_sprite, "modulate:a", 0.0, 0.6)
	_tween.tween_callback(func(): visible = false)


## Short lines get big text, long lines smaller text.
static func font_size_for(line: String) -> int:
	if line.length() <= 18:
		return 48
	if line.length() <= 40:
		return 40
	return 32


## Size the text and the pill to fit the line, then draw it once. The text is as wide as
## it needs (up to WRAP), so a short line gets a small bubble and a long one a bigger one.
func _layout(line: String) -> void:
	var font_size := font_size_for(line)
	_text.add_theme_font_size_override("normal_font_size", font_size)
	_text.add_theme_font_size_override("bold_font_size", font_size)
	var one_line := FONT.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size)
	var width := minf(ceil(one_line.x) + 8.0, WRAP)
	var wrapped := FONT.get_multiline_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, width, font_size)
	var size := Vector2(width, ceil(wrapped.y))
	_pill = Rect2(Vector2(MARGIN, MARGIN), size + PAD * 2.0)
	var view := Vector2i(_pill.size + Vector2(MARGIN * 2, MARGIN * 2 + TAIL.y))
	_viewport.size = view
	_body.size = Vector2(view)
	_text.position = _pill.position + PAD
	_text.size = size
	_text.text = _with_names(line)
	_body.queue_redraw()
	_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	# The tail's tip sits at the node's origin; the bubble grows upward from it.
	_sprite.offset = Vector2(-view.x / 2.0, -(view.y - MARGIN))


## BBCode for the line, with each resident's name in orange.
func _with_names(line: String) -> String:
	var out := line.replace("[", "[lb]")
	for r in Net.island.get("residents", []):
		var resident_name := str(r.get("name", ""))
		if resident_name != "":
			out = out.replace(resident_name, "[color=#%s]%s[/color]" % [NAME_COLOR.to_html(false), resident_name])
	return out


func _draw_body() -> void:
	var box := StyleBoxFlat.new()
	box.bg_color = Color.WHITE
	box.set_corner_radius_all(int(min(_pill.size.y / 2.0, MAX_RADIUS)))
	box.shadow_color = Color(0, 0, 0, 0.2)
	box.shadow_size = 10
	box.shadow_offset = Vector2(0, 4)
	_body.draw_style_box(box, _pill)
	var x := _pill.get_center().x
	var y := _pill.end.y
	_body.draw_colored_polygon(
		PackedVector2Array(
			[Vector2(x - TAIL.x / 2.0, y - 2), Vector2(x + TAIL.x / 2.0, y - 2), Vector2(x, y + TAIL.y)]
		),
		Color.WHITE
	)
