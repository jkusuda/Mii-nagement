@tool
class_name BubbleCard
extends PanelContainer
## One card in the Bubbles sidebar: who's asking, their mood tag, the question,
## and an Answer button.
##
## Godot concept: `@export` variables show up in the Inspector, so each card in
## game.tscn sets its own name, colors, and text there. The `set(value):` part
## re-applies them so the editor preview updates as you type.
##
## TODO(mvp): build these cards from Net.bubble_shown instead of mock data,
## and make Answer open the reply box (Net.send_answer).

@export var resident_name := "Mo":
	set(value):
		resident_name = value
		_apply()
@export var role := "Tester · Puzzled":
	set(value):
		role = value
		_apply()
@export var tag_text := "Confused":
	set(value):
		tag_text = value
		_apply()
@export var tag_icon: Texture2D:
	set(value):
		tag_icon = value
		_apply()
@export var tag_color := Color("7553DF"):
	set(value):
		tag_color = value
		_apply()
@export_multiline var quote := "“Two auth tests fail only on Tuesdays.”":
	set(value):
		quote = value
		_apply()
@export var footer := "Lab · 2m ago":
	set(value):
		footer = value
		_apply()
@export var card_color := Color("F0ECFF"):
	set(value):
		card_color = value
		_apply()


func _ready() -> void:
	_apply()


func _apply() -> void:
	if not is_node_ready():
		return  # children don't exist yet; _ready() will call us again
	%Name.text = resident_name
	%Initial.text = resident_name.left(1)  # TODO(mvp): the resident's Mii face instead of an initial
	%Role.text = role
	%TagLabel.text = tag_text
	%TagLabel.add_theme_color_override("font_color", tag_color)
	%TagIcon.texture = tag_icon
	%TagIcon.modulate = tag_color
	%Quote.text = quote
	%Footer.text = footer

	var card_style: StyleBoxFlat = get_theme_stylebox("panel", "CardPanel").duplicate()
	card_style.bg_color = card_color
	add_theme_stylebox_override("panel", card_style)

	var avatar_style: StyleBoxFlat = get_theme_stylebox("panel", "AvatarCircle").duplicate()
	avatar_style.bg_color = tag_color.lerp(Color.WHITE, 0.7)
	%Avatar.add_theme_stylebox_override("panel", avatar_style)
	%Initial.add_theme_color_override("font_color", tag_color)
