class_name BuildingLabel
extends PanelContainer
## A name pill (icon, name, count badge) that follows a 3D building on screen.
## While someone works inside, a little hammer bangs away over the count.
##
## Every frame we ask the 3D camera "where on screen is this point in the
## world?" with unproject_position(), and move the pill there. So the pill
## sticks to its building while you drag and zoom. Clicking the pill opens the
## building, like clicking the building itself.

signal pressed

## Where the pill sits relative to the building's origin (the middle of its
## lot, on the ground): just above the roof, so residents in front stay visible.
const FRONT_OFFSET := Vector3(0, 5.2, 0)
const HAMMER_ICON := preload("res://assets/icons/hammer.svg")
const HAMMER_SIZE := 22.0
const HAMMER_REST := -HAMMER_SIZE - 2.0  # y when the hammer is down: just above the badge

var target: Node3D
var camera: Camera3D
var _hammer := TextureRect.new()  # over the count while someone works inside
var _hammer_tween: Tween

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
	_make_hammer(color)
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


## Someone is working inside (or not): start or stop the hammer.
func show_working(working: bool) -> void:
	if working == _hammer.visible:
		return
	_hammer.visible = working
	if _hammer_tween:
		_hammer_tween.kill()
		_hammer_tween = null
	if not working:
		return
	# Lift the hammer up and back slowly, then bang it down fast.
	_hammer_tween = create_tween().set_loops()
	_hammer_tween.set_parallel()
	_hammer_tween.tween_property(_hammer, "rotation", -0.7, 0.28).set_ease(Tween.EASE_OUT)
	_hammer_tween.tween_property(_hammer, "position:y", HAMMER_REST - 5.0, 0.28).set_ease(Tween.EASE_OUT)
	_hammer_tween.chain().tween_property(_hammer, "rotation", 0.35, 0.1).set_ease(Tween.EASE_IN)
	_hammer_tween.tween_property(_hammer, "position:y", HAMMER_REST, 0.1).set_ease(Tween.EASE_IN)
	_hammer_tween.chain().tween_interval(0.22)


## Build the hammer. The badge is a container and would squeeze the hammer into its
## own box, so the hammer goes in a plain Control (no layout) and is placed by hand.
func _make_hammer(color: Color) -> void:
	var holder := Control.new()  # fills the badge; the hammer hangs above it
	holder.mouse_filter = Control.MOUSE_FILTER_IGNORE
	badge.add_child(holder)
	_hammer.texture = HAMMER_ICON
	_hammer.modulate = color
	_hammer.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_hammer.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_hammer.size = Vector2(HAMMER_SIZE, HAMMER_SIZE)
	_hammer.pivot_offset = Vector2(HAMMER_SIZE / 2.0, HAMMER_SIZE - 2.0)  # swing from the bottom of the handle
	_hammer.position = Vector2((22.0 - HAMMER_SIZE) / 2.0, HAMMER_REST)  # centred over the 22 px badge
	_hammer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hammer.visible = false
	holder.add_child(_hammer)


func _process(_delta: float) -> void:
	if target == null or camera == null:
		return
	# The island hides buildings it doesn't have (also while build mode previews).
	var world_point := target.global_position + FRONT_OFFSET
	visible = target.is_visible_in_tree() and not camera.is_position_behind(world_point)
	position = (camera.unproject_position(world_point) - size / 2.0).round()
