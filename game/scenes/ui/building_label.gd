class_name BuildingLabel
extends PanelContainer
## A name pill (icon, name, count badge) that follows a 3D building on screen.
##
## Every frame we ask the 3D camera "where on screen is this point in the
## world?" with unproject_position(), and move the pill there. So the pill
## sticks to its building while you drag and zoom. Clicking the pill opens the
## building, like clicking the building itself.

signal pressed

## Where the pill sits relative to the building's origin (the middle of its
## lot, on the ground): just above the roof, so residents in front stay visible.
const FRONT_OFFSET := Vector3(0, 5.2, 0)

var target: Node3D
var camera: Camera3D

@onready var icon_rect: TextureRect = %Icon
@onready var name_label: Label = %Name
@onready var badge: PanelContainer = %Badge
@onready var count_label: Label = %Count


func setup(building: Node3D, view_camera: Camera3D, title: String, icon: Texture2D, color: Color, count: int) -> void:
	target = building
	camera = view_camera
	name_label.text = title
	icon_rect.texture = icon
	icon_rect.modulate = color
	count_label.text = str(count)
	# Copy the theme's badge style and recolor it for this building.
	var style: StyleBoxFlat = get_theme_stylebox("panel", "BadgeCircle").duplicate()
	style.bg_color = color
	badge.add_theme_stylebox_override("panel", style)
	mouse_filter = Control.MOUSE_FILTER_STOP
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	gui_input.connect(
		func(event: InputEvent):
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				pressed.emit()
	)


## How many residents are here (0 included). `title` renames the pill (the
## backend's building name), unless it's empty.
func show_count(count: int, title := "") -> void:
	count_label.text = str(count)
	if title:
		name_label.text = title


func _process(_delta: float) -> void:
	if target == null or camera == null:
		return
	# The island hides buildings it doesn't have (also while build mode previews).
	var world_point := target.global_position + FRONT_OFFSET
	visible = target.is_visible_in_tree() and not camera.is_position_behind(world_point)
	position = (camera.unproject_position(world_point) - size / 2.0).round()
