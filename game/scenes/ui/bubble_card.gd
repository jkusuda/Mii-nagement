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
## In the game, bubbles_sidebar.gd makes one card per open bubble and calls
## setup(). Answer opens the reply box: one button per option when the bubble
## has `options`, otherwise a text field. The reply goes to Net.send_answer();
## the card disappears when the server sends `bubble_resolved`.

## bubble kind -> tag color
const KIND_COLORS := {
	"confused": Color("7553DF"), "struggling": Color("E36F1E"), "permission": Color("D6457A"),
	"ready": Color("009D85"), "tired": Color("C57722"),
}

var bubble_id := ""
var _since := 0.0
var _place := ""

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
	if Engine.is_editor_hint():
		return  # in the editor, only the preview
	%AnswerButton.pressed.connect(open_reply)
	%Send.pressed.connect(func(): _send(%ReplyEdit.text))
	%ReplyEdit.text_submitted.connect(_send)


## Fill the card from a bubble and its resident (dictionaries from Net.island).
func setup(bubble: Dictionary, resident: Dictionary, place: String) -> void:
	bubble_id = str(bubble["id"])
	_since = float(bubble.get("since", Time.get_unix_time_from_system()))
	_place = place
	var kind := str(bubble["kind"])
	var tag: Array = ResidentData.BUBBLE_TAGS.get(kind, ["Needs you", null, "Answer"])
	resident_name = str(resident.get("name", bubble["agent"]))
	role = ResidentData.role_line(resident) if resident else ""
	tag_text = tag[0]
	tag_icon = tag[1]
	tag_color = KIND_COLORS.get(kind, Color("7553DF"))
	quote = "“%s”" % bubble["text"]
	card_color = Color(str(resident.get("color", "#FFFFFF"))).lerp(Color.WHITE, 0.86)
	%AnswerButton.text = tag[2]

	# Options become buttons; without options you type a reply.
	var options = bubble.get("options")
	%InputRow.visible = not options
	for child in %Options.get_children():
		child.queue_free()
	if options:
		for option in options:
			var button := Button.new()
			button.text = str(option)
			button.theme_type_variation = &"OutlineButton"
			button.add_theme_font_size_override("font_size", 13)
			button.pressed.connect(_send.bind(str(option)))
			%Options.add_child(button)
	refresh_age()


## Update "Lab · 2m ago". The sidebar calls this every few seconds.
func refresh_age() -> void:
	var minutes := int((Time.get_unix_time_from_system() - _since) / 60.0)
	footer = "%s · %s" % [_place, "just now" if minutes < 1 else "%dm ago" % minutes]


func open_reply() -> void:
	%ReplyBox.visible = true
	if %InputRow.visible:
		%ReplyEdit.grab_focus()


func _send(text: String) -> void:
	if text.strip_edges() == "":
		return
	Net.send_answer(bubble_id, text.strip_edges())
	%ReplyBox.visible = false
	%AnswerButton.disabled = true
	%AnswerButton.text = "Sent!"


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
