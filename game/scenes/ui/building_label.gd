class_name BuildingLabel
extends PanelContainer
## A name pill (icon, name, count badge) that follows a 3D building on screen.
##
## Every frame we ask the 3D camera "where on screen is this point in the
## world?" with unproject_position(), and move the pill there. So the pill
## sticks to its building while you drag and zoom.

## Where the pill sits relative to the building's base: just in front of the door.
const FRONT_OFFSET := Vector3(0, 0, 4.4)

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


func _process(_delta: float) -> void:
	if target == null or camera == null:
		return
	var world_point := target.global_position + FRONT_OFFSET
	visible = not camera.is_position_behind(world_point)
	position = (camera.unproject_position(world_point) - size / 2.0).round()
