class_name UI
## Small widget makers shared by screens that build their UI in code.
##
## Godot concept: a `static func` belongs to the script, not to a node, so any
## script can call UI.label(...) without an instance.


## A label in the theme's strong (dark) or muted (grey) style, at `size` points.
## Labels ignore the mouse by default, so clicks reach the button underneath.
static func label(text: String, size: int, strong: bool) -> Label:
	var node := Label.new()
	node.text = text
	node.theme_type_variation = &"LabelStrong" if strong else &"LabelMuted"
	node.add_theme_font_size_override("font_size", size)
	return node
