class_name PackageForm
extends VBoxContainer
## The "Send a new issue" form inside the Mailbox panel: what should be done
## (title + details) or, when the island has a GitHub repo, an issue number the
## backend reads itself. Sends POST /islands/{id}/tasks; the package then shows
## up like any other, through `task` events.
##
## Built in code. The buttons live in the panel's footer, so the panel calls
## submit() and listens for `sent`.

signal sent
## Whether a send is in flight (the panel greys out its Send button).
signal busy_changed(busy: bool)

const THEME := preload("res://ui/theme.tres")

var _title := LineEdit.new()
var _details := TextEdit.new()
var _issue := LineEdit.new()
var _issue_field: Control
var _message := Label.new()
var _busy := false


func _init() -> void:
	add_theme_constant_override("separation", 12)

	_title.placeholder_text = "e.g. Fix the typo on the login page"
	_title.max_length = 200
	_title.text_submitted.connect(func(_text): submit())
	add_child(_field("What should be done?", _title))

	_details.placeholder_text = "Details, files, acceptance criteria (optional)"
	_details.custom_minimum_size = Vector2(0, 110)
	_details.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_details.add_theme_stylebox_override("normal", THEME.get_stylebox("normal", "LineEdit"))
	_details.add_theme_stylebox_override("focus", THEME.get_stylebox("focus", "LineEdit"))
	# TextEdit has no colors in theme.tres, so Godot's light default would show
	# on white. Take the title field's ink and caret so both inputs match.
	_details.add_theme_color_override("font_color", THEME.get_color("font_color", "LineEdit"))
	_details.add_theme_color_override("caret_color", THEME.get_color("caret_color", "LineEdit"))
	add_child(_field("Details", _details))

	_issue.placeholder_text = "e.g. 42"
	_issue.max_length = 9
	_issue.text_submitted.connect(func(_text): submit())
	_issue_field = _field("…or a GitHub issue number (its title and details come from GitHub)", _issue)
	add_child(_issue_field)

	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_message.add_theme_color_override("font_color", Color("C0392B"))
	add_child(_message)


## Get ready for a new package on the island Net is watching.
func reset() -> void:
	_issue_field.visible = Net.island.get("repo") != null
	_message.text = ""
	_title.grab_focus.call_deferred()


func submit() -> void:
	if _busy:
		return
	var title := _title.text.strip_edges()
	var issue := _issue.text.strip_edges() if _issue_field.visible else ""
	if Net.island.is_empty():
		_message.text = "Not connected to an island yet."
		return
	if issue != "" and not issue.is_valid_int():
		_message.text = "The issue number should be just digits."
		return
	if title == "" and issue == "":
		_message.text = "Say what should be done (or give an issue number)."
		_title.grab_focus()
		return

	var body := {"issue": int(issue)} if issue != "" else {"title": title, "description": _details.text.strip_edges()}
	_set_busy(true)
	_message.text = ""
	var path := "/islands/%s/tasks" % str(Net.island["id"]).uri_encode()
	Net.request_json(path, _on_sent, HTTPClient.METHOD_POST, body)


func _on_sent(ok: bool, data: Dictionary) -> void:
	_set_busy(false)
	if not ok:
		_message.text = "Hmm, that didn't work: %s" % data["error"]
		return
	_title.clear()
	_details.clear()
	_issue.clear()
	sent.emit()


func _set_busy(busy: bool) -> void:
	_busy = busy
	busy_changed.emit(busy)


## A small label above an input.
func _field(text: String, input: Control) -> Control:
	var field := VBoxContainer.new()
	field.add_theme_constant_override("separation", 4)
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"FieldLabel"
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	field.add_child(label)
	field.add_child(input)
	return field
