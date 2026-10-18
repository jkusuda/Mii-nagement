@tool
class_name BubbleCard
extends PanelContainer
## One card in the Bubbles sidebar: who's asking, their mood tag, the question,
## and an Answer button.
##
## Godot concept: `@export` variables show up in the Inspector; with `@tool` the
## editor preview of bubble_card.tscn redraws as you type (the `set(value):` part).
##
## In the game, bubbles_sidebar.gd makes one card per open bubble and calls
## setup(). Answer emits `answer_requested`; the sidebar opens the AnswerPopup
## (answer_popup.gd), which sends the reply. The card disappears when the server
## sends `bubble_resolved`.

signal answer_requested(bubble: Dictionary)

@export var resident_name := "Mo":
	set(value):
		resident_name = value
		_apply()
@export var role := "Coder · Puzzled":
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

var bubble := {}  # the bubble from Net.island this card shows
var _since := 0.0
var _place := ""


func _ready() -> void:
	_apply()
	if Engine.is_editor_hint():
		return  # in the editor, only the preview
	%AnswerButton.pressed.connect(open_reply)


## Fill the card from a bubble and its resident (dictionaries from Net.island).
func setup(data: Dictionary, resident: Dictionary, place: String) -> void:
	bubble = data
	_since = float(bubble.get("since", Time.get_unix_time_from_system()))
	_place = place
	var kind := str(bubble["kind"])
	var look := Protocol.bubble(kind)
	resident_name = str(resident.get("name", bubble["agent"]))
	# The Mii headshot covers the initial (which the editor preview still shows).
	%Portrait.texture = Portraits.of(resident) if resident else null
	%Initial.visible = resident.is_empty()
	role = ResidentData.role_line(resident) if resident else ""
	tag_text = look["tag"]
	tag_icon = look["icon"]
	tag_color = look["color"]
	quote = "“%s”" % bubble["text"]
	card_color = Color(str(resident.get("color", "#FFFFFF"))).lerp(Color.WHITE, 0.86)
	if not %AnswerButton.disabled:
		%AnswerButton.text = look["button"]
	refresh_age()


## Update "Lab · 2m ago". The sidebar calls this every few seconds.
func refresh_age() -> void:
	var minutes := int((Time.get_unix_time_from_system() - _since) / 60.0)
	footer = "%s · %s" % [_place, "just now" if minutes < 1 else "%dm ago" % minutes]


func open_reply() -> void:
	answer_requested.emit(bubble)


## The popup sent an answer: wait for the server's `bubble_resolved`.
func mark_sent() -> void:
	%AnswerButton.disabled = true
	%AnswerButton.text = "Sent!"


func _apply() -> void:
	if not is_node_ready():
		return  # children don't exist yet; _ready() will call us again
	%Name.text = resident_name
	%Initial.text = resident_name.left(1)
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
