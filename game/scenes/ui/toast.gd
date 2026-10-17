class_name Toast
extends PanelContainer
## A short message that slides in at the top of the window and fades away,
## e.g. "Couldn't abandon Fern Harbor: a package is being worked on there".
## game.gd owns one and shows server errors no screen handled itself.
##
## Godot concept: a Tween animates properties over time (here the toast's
## transparency, `modulate:a`), so we don't have to count frames in _process.

const SHOW_SECONDS := 4.0

var _label := Label.new()
var _tween: Tween


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false
	var style := StyleBoxFlat.new()
	style.bg_color = Color("292E50")
	style.set_corner_radius_all(16)
	style.content_margin_left = 18
	style.content_margin_right = 18
	style.content_margin_top = 10
	style.content_margin_bottom = 10
	add_theme_stylebox_override("panel", style)
	_label.theme_type_variation = &"WhiteStrong"
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.custom_minimum_size.x = 440
	_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	add_child(_label)
	# Stay at the top middle of the parent, whatever the text's size.
	set_anchors_preset(Control.PRESET_CENTER_TOP)
	grow_horizontal = Control.GROW_DIRECTION_BOTH


func show_message(text: String) -> void:
	_label.text = text
	visible = true
	modulate.a = 1.0
	size = Vector2.ZERO  # shrink to the new text
	position.y = 16
	if _tween:
		_tween.kill()
	_tween = create_tween()
	_tween.tween_interval(SHOW_SECONDS)
	_tween.tween_property(self, "modulate:a", 0.0, 0.4)
	_tween.tween_callback(hide)
