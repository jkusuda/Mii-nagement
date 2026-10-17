class_name ResidentCard
extends PanelContainer
## One card on the Residents screen: face, name, status tag, what they're up to,
## how full their notebook (context window) is, and their catchphrase.

signal edit_pressed(resident: Dictionary)
signal action_pressed(resident: Dictionary)

var resident := {}
## "♥ Best friends with Jun · 68%": the friendship meter (made in code, under Doing).
var _friend := Label.new()


func setup(data: Dictionary) -> void:
	resident = data
	var accent := Color(str(data.get("color", "#8A8FA8")))
	var ink := accent.darkened(0.2)  # a darker shade for text on a light background

	%Name.text = str(data.get("name", "?"))
	%Initial.text = %Name.text.left(1)  # TODO(stretch): a rendered Mii portrait (docs/mii-customization.md)
	%Initial.add_theme_color_override("font_color", ink)
	%Role.text = ResidentData.role_line(data)
	%TagLabel.text = ResidentData.status_text(data)
	%TagLabel.add_theme_color_override("font_color", ink)

	var bubble = data.get("bubble")
	if bubble:
		%Doing.text = str(bubble["text"])
	elif data.get("activity"):
		%Doing.text = str(data["activity"])
	else:
		%Doing.text = "Taking it easy." if data.get("state") != "working" else "Busy at work."

	# Their closest friend: residents who hand packages to each other grow closer
	# (`friendships` in the snapshot, live `friendship` events).
	var friend: Dictionary = data.get("friend", {})
	_friend.visible = not friend.is_empty()
	if friend:
		_friend.text = "♥ %s %s · %d%%" % [ResidentData.friendship_words(friend["score"]), friend["name"], roundi(friend["score"] * 100.0)]
		_friend.tooltip_text = "%d package%s handed between them" % [friend["handoffs"], "" if friend["handoffs"] == 1 else "s"]
		_friend.add_theme_color_override("font_color", ink)

	# The notebook is how full the agent's context window is (0-1). The backend
	# sends it (`notebook` in the snapshot and the `notebook` event) once the
	# agent reports usage; it's null until then, so the row stays hidden.
	var has_notebook := data.get("notebook") != null
	%NotebookRow.visible = has_notebook
	%Notebook.visible = has_notebook
	if has_notebook:
		%Notebook.value = float(data["notebook"]) * 100.0
		%Percent.text = "%d%%" % roundi(%Notebook.value)
		%Percent.add_theme_color_override("font_color", ink)

	var catchphrase := str(data.get("catchphrase", ""))
	%Catchphrase.text = "“%s”" % catchphrase if catchphrase else ""

	# The orange button only shows when the resident needs you; otherwise "Visit".
	var has_bubble := bubble != null
	%ActionButton.visible = has_bubble
	%VisitButton.visible = not has_bubble
	if has_bubble:
		%ActionButton.text = ResidentData.BUBBLE_TAGS.get(bubble["kind"], ["", null, "Answer"])[2]

	# Recolor copies of the theme styles for this resident.
	_tint("panel", "CardPanel", self, accent.lerp(Color.WHITE, 0.86))
	_tint("panel", "AvatarCircle", %Avatar, accent.lerp(Color.WHITE, 0.7))
	_tint("fill", "NotebookBar", %Notebook, accent)


func _ready() -> void:
	_friend.theme_type_variation = &"LabelStrong"
	_friend.add_theme_font_size_override("font_size", 12)
	_friend.mouse_filter = Control.MOUSE_FILTER_PASS  # so the tooltip shows
	_friend.visible = false
	%Doing.add_sibling(_friend)
	%EditButton.pressed.connect(func(): edit_pressed.emit(resident))
	%ActionButton.pressed.connect(func(): action_pressed.emit(resident))
	%VisitButton.pressed.connect(func(): action_pressed.emit(resident))


## Copy a theme stylebox, change its color, and apply it to one node only.
func _tint(style_name: String, theme_type: String, node: Control, color: Color) -> void:
	var style: StyleBoxFlat = get_theme_stylebox(style_name, theme_type).duplicate()
	style.bg_color = color
	node.add_theme_stylebox_override(style_name, style)
