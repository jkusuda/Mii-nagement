class_name AnswerPopup
extends PopupPanel
## The popup a bubble card's Answer / Review / Tuck in button opens: who's
## asking, their question, what it's about, and how to answer.
##
##   ready       the task package (+ PR link), a note, Send back / Approve
##   permission  the exact command or file in a dark box, "how long can they
##               keep this key?" (the allow options as cards), Not now / Lend the key
##   other kinds a text box, Not now / Send
##
## The answer goes to Net.send_answer() (an `answer_bubble` message); the popup
## closes, and the card goes away when the server sends `bubble_resolved`.
## The bubble's `task` and `detail` fields are in docs/protocol.md.
##
## Built in code, like SettingsPopup. Each open() throws the old contents away
## and builds them again for the new bubble.

signal answered(bubble_id: String)

const THEME := preload("res://ui/theme.tres")
const AnswerButtonScript := preload("res://scenes/ui/answer_button.gd")
const CLOSE_ICON := preload("res://assets/icons/x.svg")
const KEY_ICON := preload("res://assets/icons/key-round.svg")
const PR_ICON := preload("res://assets/icons/git-pull-request.svg")

const INK := Color("292E50")
const MUTED := Color("68738B")
## bubble kind -> header tint
const HEADER_COLORS := {
	"ready": Color("E3F6EF"), "permission": Color("FFF6D6"), "confused": Color("F0ECFF"),
	"struggling": Color("FFEDE0"), "tired": Color("FCEFD9"),
}
## Text longer than this is cut off with a "Show more" link (_collapsible).
const LONG_CODE := 160
const LONG_QUOTE := 260
const COLLAPSED_HEIGHT := 62  # about three lines
const EXPANDED_HEIGHT := 220  # then it scrolls

## Words that mark an option as a "no" (shown as the Not now button, not a card).
const DECLINE_WORDS := ["reject", "deny", "don't", "cancel", "no"]

var bubble_id := ""
var _kind := ""
var _content: VBoxContainer
var _choice := ""  # permission: the allow option picked in the cards
var _note: LineEdit
var _reply: TextEdit


func _init() -> void:
	theme = THEME  # a popup is its own window, so it doesn't inherit the game's theme
	var style := _box(Color("FFFDF2"), 22, 0)
	style.border_color = Color("E8DFC0")
	style.set_border_width_all(2)
	add_theme_stylebox_override("panel", style)
	Net.bubble_resolved.connect(func(id: String):
		if id == bubble_id and visible:
			hide())  # answered elsewhere, or it timed out


## Open the popup for one bubble (a dictionary from Net.island["bubbles"]).
func open(bubble: Dictionary) -> void:
	bubble_id = str(bubble["id"])
	_kind = str(bubble["kind"])
	_choice = ""
	_note = null
	_reply = null
	if _content:
		remove_child(_content)  # a PopupPanel stretches every child over itself, so take it out now
		_content.queue_free()
	_content = VBoxContainer.new()
	_content.custom_minimum_size = Vector2(580, 0)
	_content.add_theme_constant_override("separation", 0)
	add_child(_content)

	var resident := Net.find_resident(str(bubble["agent"]))
	var who := str(resident.get("name", bubble["agent"]))
	var task := _find_task(bubble.get("task"))
	_content.add_child(_header(bubble, resident))

	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 14)
	_content.add_child(_padded(body, 22, 18))
	body.add_child(_quote(str(bubble["text"])))

	var options: Array = bubble.get("options") if bubble.get("options") else []
	match _kind:
		"permission":
			body.add_child(_key_card(bubble, task))
			_permission_answer(body, options, who)
		"ready":
			if not task.is_empty():
				body.add_child(_task_card(task))
			if not str(_or_empty(bubble.get("detail"))).is_empty():
				body.add_child(_code(str(bubble["detail"])))  # what changed (git diff --stat)
			_review_answer(body, options, who)
		_:
			if not task.is_empty():
				body.add_child(_task_card(task))
			if not str(_or_empty(bubble.get("detail"))).is_empty():
				body.add_child(_code(str(bubble["detail"])))
			_text_answer(body, options, who)

	popup_centered(Vector2i(580, 300))
	_fit.call_deferred()


## Wrapped labels only know their height once they have a width, which happens
## during layout. So after one frame, shrink the popup to fit and center it again.
func _fit() -> void:
	await get_tree().process_frame
	if visible:
		popup_centered(Vector2i(get_contents_minimum_size()))


# --- The parts --------------------------------------------------------------------


## Avatar, name, "Job · at the Workshop", "Mood: X" + tag, and the close button.
func _header(bubble: Dictionary, resident: Dictionary) -> Control:
	var panel := PanelContainer.new()
	var style := _box(HEADER_COLORS.get(_kind, Color("F0ECFF")), 0, 20)
	style.corner_radius_top_left = 20
	style.corner_radius_top_right = 20
	style.content_margin_left = 22
	style.content_margin_right = 16
	panel.add_theme_stylebox_override("panel", style)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 14)
	panel.add_child(row)

	var tag: Array = ResidentData.BUBBLE_TAGS.get(_kind, ["Needs you", null, "Answer"])
	var tag_color: Color = BubbleCard.KIND_COLORS.get(_kind, Color("7553DF"))
	var who := str(resident.get("name", bubble["agent"]))
	var avatar := PanelContainer.new()
	avatar.custom_minimum_size = Vector2(56, 56)
	avatar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var avatar_style: StyleBoxFlat = THEME.get_stylebox("panel", "AvatarCircle").duplicate()
	avatar_style.bg_color = Color(str(resident.get("color", "#FFFFFF"))).lerp(Color.WHITE, 0.55)
	avatar.add_theme_stylebox_override("panel", avatar_style)
	var initial := _label(who.left(1), &"CardName")  # TODO(mvp): the resident's Mii face instead of an initial
	initial.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	initial.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	avatar.add_child(initial)
	row.add_child(avatar)

	var text := VBoxContainer.new()
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.add_theme_constant_override("separation", 2)
	text.add_child(_label(who, &"CardName"))
	var place := str(Net.find_building(str(resident.get("building", ""))).get("name", ""))
	var job := str(resident.get("job", "resident")).capitalize()
	text.add_child(_label(job + (" · at the %s" % place if place else ""), &"BodyText"))
	var mood_row := HBoxContainer.new()
	mood_row.add_theme_constant_override("separation", 8)
	mood_row.add_child(_label("Mood: %s" % str(resident.get("mood", "happy")).capitalize(), &"LabelMuted"))
	mood_row.add_child(_tag(tag[0], tag[1], tag_color))
	text.add_child(mood_row)
	row.add_child(text)

	var close := Button.new()
	close.icon = CLOSE_ICON
	close.theme_type_variation = &"FlatIconButton"
	close.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	close.tooltip_text = "Close (answer later)"
	close.pressed.connect(hide)
	row.add_child(close)
	return panel


## The question, in a white box with a dark outline.
func _quote(text: String) -> Control:
	var style := _box(Color.WHITE, 14, 14)
	style.border_color = INK
	style.set_border_width_all(2)
	var label := _label("“%s”" % text, &"QuoteText")
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return _collapsible(label, style, text.length() > LONG_QUOTE, "Show the whole message")


## The task package: PR icon, title, little pills, and "Open on GitHub" when there's a PR.
func _task_card(task: Dictionary) -> Control:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 8)
	top.add_child(_icon(PR_ICON, INK, 16))
	var title := _label(str(task.get("title", "Task")), &"LabelStrong")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	top.add_child(title)
	var pr_url := str(_or_empty(task.get("pr_url")))
	if pr_url:
		var link := LinkButton.new()
		link.text = "Open on GitHub"
		link.add_theme_color_override("font_color", Color("4D5BD6"))
		link.add_theme_font_size_override("font_size", 13)
		link.pressed.connect(OS.shell_open.bind(pr_url))
		top.add_child(link)
	box.add_child(top)

	var pills := HBoxContainer.new()
	pills.add_theme_constant_override("separation", 6)
	pills.add_child(_pill(str(task.get("id", "")), Color("E4ECFF")))
	var issue = task.get("issue")
	if issue != null:
		pills.add_child(_pill("Closes #%s" % str(issue), Color("E8F7E8")))
	var building := str(Net.find_building(str(_or_empty(task.get("building")))).get("name", ""))
	if building:
		pills.add_child(_pill("At the %s" % building, Color("FFF1D6")))
	if not pr_url:
		pills.add_child(_pill("No PR yet", Color("F1F1F1")))
	box.add_child(pills)
	return _panel(box, _context_style())


## Permission: key icon, what it's for, and the exact command in a dark box.
func _key_card(bubble: Dictionary, task: Dictionary) -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 14)
	var key_style := _box(Color("FFE15A"), 12, 10)
	key_style.border_color = INK
	key_style.set_border_width_all(2)
	var key_box := _panel(_icon(KEY_ICON, INK, 22), key_style)
	key_box.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(key_box)

	var text := VBoxContainer.new()
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.add_theme_constant_override("separation", 6)
	text.add_child(_label("Key request", &"LabelStrong"))
	var detail := str(_or_empty(bubble.get("detail")))
	if detail:
		text.add_child(_code(detail))
	if not task.is_empty():
		var about := _label("For: %s" % str(task.get("title", "")), &"LabelMuted")
		about.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		text.add_child(about)
	row.add_child(text)
	return _panel(row, _context_style())


## Permission answers: the allow options as cards, the reject option as "Not now".
func _permission_answer(body: VBoxContainer, options: Array, who: String) -> void:
	var allows := options.filter(func(o): return not _declines(str(o)))
	var declines := options.filter(func(o): return _declines(str(o)))
	if allows.is_empty():
		_text_answer(body, options, who)
		return
	_choice = str(allows[0])
	body.add_child(_label("How long can %s keep this key?" % who, &"BodyText"))
	var cards := HBoxContainer.new()
	cards.add_theme_constant_override("separation", 10)
	var group := ButtonGroup.new()  # Godot concept: toggle buttons in one group act like radio buttons
	for option in allows:
		var card := Button.new()
		card.toggle_mode = true
		card.button_group = group
		card.button_pressed = str(option) == _choice
		card.text = str(option)
		card.tooltip_text = _scope_hint(str(option))
		card.theme_type_variation = &"ChoiceButton"
		card.custom_minimum_size = Vector2(0, 48)
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		card.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		card.pressed.connect(func(): _choice = str(option))
		cards.add_child(card)
	body.add_child(cards)

	var no := str(declines[0]) if declines else ""
	_footer(body, "You can take back keys anytime in Residents → Keys.", [
		["Not now", func(): _send(no)] if no else ["Later", hide],
		["Lend the key", func(): _send(_choice)],
	])


## Review answers: an optional note, the other options (Send back), and the first one (Approve).
func _review_answer(body: VBoxContainer, options: Array, who: String) -> void:
	if options.is_empty():
		_text_answer(body, options, who)
		return
	body.add_child(_label("Note for %s (optional, sent with your answer)" % who, &"BodyText"))
	_note = LineEdit.new()
	_note.placeholder_text = "e.g. “Also test IPv6 and proxy headers.”"
	_note.custom_minimum_size = Vector2(0, 38)
	body.add_child(_note)
	var buttons := []
	for option in options.slice(1):
		buttons.append([str(option), _send_with_note.bind(str(option))])
	buttons.append([str(options[0]), _send_with_note.bind(str(options[0]))])
	var merges := "merge" in str(options[0]).to_lower()
	_footer(body, "Approving merges this work at the Post Office." if merges
		else "Approving sends the package on to the next building.", buttons)


## Questions: option buttons when there are options, otherwise a text box.
func _text_answer(body: VBoxContainer, options: Array, who: String) -> void:
	if options:
		var buttons := []
		for option in options.slice(1):
			buttons.append([str(option), _send.bind(str(option))])
		buttons.append([str(options[0]), _send.bind(str(options[0]))])
		_footer(body, "%s is waiting for your answer." % who, buttons)
		return
	body.add_child(_label("Your answer for %s" % who, &"BodyText"))
	_reply = TextEdit.new()
	_reply.custom_minimum_size = Vector2(0, 76)
	_reply.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	for state in ["normal", "focus", "read_only"]:
		_reply.add_theme_stylebox_override(state, THEME.get_stylebox(state if state != "read_only" else "normal", "LineEdit"))
	_reply.add_theme_color_override("font_color", INK)
	_reply.add_theme_color_override("caret_color", INK)
	_reply.add_theme_color_override("font_placeholder_color", MUTED)
	_reply.placeholder_text = {
		"tired": "e.g. “Keep going, you're close.” or “Stop here for today.”",
		"struggling": "A hint, e.g. “The clock mock lives in tests/conftest.py.”",
	}.get(_kind, "Type your answer…")
	body.add_child(_reply)
	_footer(body, "Your answer goes straight to %s." % who, [
		["Not now", hide],
		["Send", func(): _send(_reply.text)],
	])
	_reply.grab_focus.call_deferred()


## A muted hint on the left and buttons on the right; the last button is the orange one.
func _footer(body: VBoxContainer, hint: String, buttons: Array) -> void:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var label := _label(hint, &"LabelMuted")
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.size_flags_vertical = Control.SIZE_SHRINK_END
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	row.add_child(label)
	for i in buttons.size():
		var button := Button.new()
		button.text = buttons[i][0]
		button.pressed.connect(buttons[i][1])
		button.size_flags_vertical = Control.SIZE_SHRINK_END
		if i == buttons.size() - 1:
			button.theme_type_variation = &"AnswerButton"
			button.custom_minimum_size = Vector2(maxf(120, 24 + 9 * button.text.length()), 38)
			button.set_script(AnswerButtonScript)  # the wavy orange pill (painted in its _ready)
		else:
			button.theme_type_variation = &"OutlineButton"
			button.custom_minimum_size = Vector2(0, 38)
		row.add_child(button)
	body.add_child(row)


# --- Sending ------------------------------------------------------------------------


func _send_with_note(option: String) -> void:
	var note := _note.text.strip_edges() if _note else ""
	_send(option + (": " + note if note else ""))


func _send(text: String) -> void:
	text = text.strip_edges()
	if text.is_empty():
		if _reply:
			_reply.grab_focus()
		return
	Net.send_answer(bubble_id, text)
	answered.emit(bubble_id)
	hide()


# --- Helpers ------------------------------------------------------------------------


func _find_task(task_id) -> Dictionary:
	if task_id == null:
		return {}
	for t in Net.island.get("tasks", []):
		if str(t["id"]) == str(task_id):
			return t
	return {}


static func _declines(option: String) -> bool:
	var words := option.to_lower().replace("[", " ").split(" ", false)
	return DECLINE_WORDS.any(func(w): return w in words)


## A one-line explanation of an allow option, for its tooltip.
static func _scope_hint(option: String) -> String:
	var lower := option.to_lower()
	if "always" in lower:
		return "Don't ask again for this kind of thing"
	if "once" in lower:
		return "Only this one time"
	return option


static func _or_empty(value):
	return "" if value == null else value


func _context_style() -> StyleBoxFlat:
	var style := _box(Color("FFF4C9"), 16, 14)
	style.border_color = Color("E9D27A")
	style.set_border_width_all(2)
	return style


func _code(text: String) -> Control:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
	label.add_theme_color_override("font_color", Color("FFE15A"))
	label.add_theme_font_override("font", SystemFont.new())  # TODO(stretch): a monospace font in assets/font
	label.add_theme_font_size_override("font_size", 13)
	var is_long := text.length() > LONG_CODE or text.count("\n") >= 3
	return _collapsible(label, _box(INK, 8, 10), is_long, "Show the full command")


## A box for text that may be long (a command, a question). Short text shows as
## is. Long text is cut off at a few lines, with a link under it that opens the
## box up into a scrollable one, so the popup never grows past the window and
## its buttons stay where they are.
##
## Godot concept: a ScrollContainer shows part of its child and clips the rest.
## With SCROLL_MODE_SHOW_NEVER it still cuts off but shows no scroll bar.
func _collapsible(content: Control, style: StyleBox, is_long: bool, more_text: String) -> Control:
	if not is_long:
		return _panel(content, style)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_SHOW_NEVER
	scroll.custom_minimum_size.y = COLLAPSED_HEIGHT
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(content)

	var more := LinkButton.new()
	more.text = more_text + "  ▾"
	more.add_theme_color_override("font_color", Color("4D5BD6"))
	more.add_theme_font_size_override("font_size", 13)
	more.pressed.connect(func():
		var opening := scroll.vertical_scroll_mode == ScrollContainer.SCROLL_MODE_SHOW_NEVER
		scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO if opening else ScrollContainer.SCROLL_MODE_SHOW_NEVER
		scroll.custom_minimum_size.y = EXPANDED_HEIGHT if opening else COLLAPSED_HEIGHT
		scroll.scroll_vertical = 0
		more.text = ("Show less  ▴" if opening else more_text + "  ▾")
		_fit())

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	box.add_child(_panel(scroll, style))
	box.add_child(more)
	return box


func _tag(text: String, icon: Texture2D, color: Color) -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 4)
	if icon:
		row.add_child(_icon(icon, color, 12))
	var label := _label(text, &"TagText")
	label.add_theme_color_override("font_color", color)
	row.add_child(label)
	var pill := PanelContainer.new()
	pill.theme_type_variation = &"TagPill"
	pill.add_child(row)
	return pill


func _pill(text: String, color: Color) -> Control:
	var label := _label(text, &"TagText")
	label.add_theme_font_size_override("font_size", 11)
	return _panel(label, _box(color, 8, 4))


func _icon(texture: Texture2D, color: Color, px: int) -> TextureRect:
	var icon := TextureRect.new()
	icon.texture = texture
	icon.modulate = color
	icon.custom_minimum_size = Vector2(px, px)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return icon


func _label(text: String, variation: StringName) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = variation
	return label


func _panel(child: Control, style: StyleBox) -> PanelContainer:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", style)
	panel.add_child(child)
	return panel


func _padded(child: Control, x: int, y: int) -> MarginContainer:
	var margin := MarginContainer.new()
	for side in ["left", "right"]:
		margin.add_theme_constant_override("margin_" + side, x)
	for side in ["top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, y)
	margin.add_child(child)
	return margin


## A rounded box: background color, corner radius, inner padding.
static func _box(color: Color, radius: int, pad: int) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = color
	style.set_corner_radius_all(radius)
	style.corner_detail = 12
	style.set_content_margin_all(pad)
	return style
