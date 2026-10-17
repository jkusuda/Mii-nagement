@tool
class_name AnswerButton
extends Button
## The wavy orange "Answer" button. Its background is painted pixel by pixel
## here instead of loaded from an image, so the colors are easy to change.
##
## Godot concept: `@tool` makes a script also run inside the editor, so you
## see the real button while editing scenes, not a blank one.

const BASE_COLOR := Color("EB471F")
const WAVE_COLOR := Color("F27A2E")
## The yellow look (the talk popup's answers, Tomodachi-style). Use with dark text.
const YELLOW := [Color("FFE55C"), Color("FFF2A6")]
const SCALE := 2  # paint at 2x resolution so it stays crisp

## The two stripe colours. Change them before the button enters the tree.
var base_color := BASE_COLOR
var wave_color := WAVE_COLOR


func _ready() -> void:
	var texture := _paint(Vector2i(custom_minimum_size) * SCALE, base_color, wave_color)
	add_theme_stylebox_override("focus", StyleBoxEmpty.new())  # no focus outline
	for state in ["normal", "hover", "pressed", "hover_pressed", "disabled"]:
		var style := StyleBoxTexture.new()
		style.texture = texture
		# No texture margins: the whole 2x image is scaled down onto the button,
		# which keeps it crisp. (So keep custom_minimum_size big enough for the text.)
		style.content_margin_left = 12
		style.content_margin_right = 10
		if state == "hover":
			style.modulate_color = Color(1.08, 1.08, 1.08)
		elif state in ["pressed", "hover_pressed"]:
			style.modulate_color = Color(0.9, 0.9, 0.9)
		elif state == "disabled":
			style.modulate_color = Color(1, 1, 1, 0.5)  # faded, but still the same pill
		add_theme_stylebox_override(state, style)


## A pill shape filled with `base` and lighter wavy stripes of `wave`.
static func _paint(size: Vector2i, base := BASE_COLOR, wave := WAVE_COLOR) -> ImageTexture:
	var image := Image.create_empty(size.x, size.y, false, Image.FORMAT_RGBA8)
	var radius := size.y / 2.0
	for x in size.x:
		for y in size.y:
			# Distance from this pixel to the pill's center line -> smooth edge.
			var center := Vector2(clampf(x + 0.5, radius, size.x - radius), radius)
			var edge := clampf(radius - Vector2(x + 0.5, y + 0.5).distance_to(center), 0.0, 1.0)
			if edge <= 0.0:
				continue
			var stripe := fposmod(y + 5.0 * sin(x * 0.11), 18.0)
			var color := wave if stripe < 6.0 else base
			color.a = edge
			image.set_pixel(x, y, color)
	return ImageTexture.create_from_image(image)
