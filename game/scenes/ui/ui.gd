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


## A round avatar with the resident's Mii headshot (Portraits), on a light circle in
## their colour. `px` is the diameter. clip_children cuts the photo to the circle.
## An empty resident (a story nobody is in) gets the plain circle.
static func avatar(resident: Dictionary, px: int, lighten := 0.7) -> PanelContainer:
	var circle := PanelContainer.new()
	circle.theme_type_variation = &"AvatarCircle"
	circle.custom_minimum_size = Vector2(px, px)
	circle.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	circle.mouse_filter = Control.MOUSE_FILTER_IGNORE
	circle.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	var style: StyleBoxFlat = preload("res://ui/theme.tres").get_stylebox("panel", "AvatarCircle").duplicate()
	style.bg_color = Color(str(resident.get("color", "#8A8FA8"))).lerp(Color.WHITE, lighten)
	style.set_corner_radius_all(px / 2)
	circle.add_theme_stylebox_override("panel", style)
	if resident.is_empty():
		return circle
	var photo := TextureRect.new()
	photo.texture = Portraits.of(resident)
	photo.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	photo.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	photo.mouse_filter = Control.MOUSE_FILTER_IGNORE
	circle.add_child(photo)
	return circle
