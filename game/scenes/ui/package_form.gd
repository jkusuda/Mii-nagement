class_name PackageForm
extends VBoxContainer
## The "Send a new issue" form inside the Mailbox panel: what should be done
## (title + details) or, when the island has a GitHub repo, an issue number the
## backend reads itself, plus files to attach (picked with a FileDialog, shown as
## removable chips, sent base64-encoded). Sends POST /islands/{id}/tasks; the package
## then shows up like any other, through `task` events.
##
## Built in code. The buttons live in the panel's footer, so the panel calls
## submit() and listens for `sent`.

signal sent
## Whether a send is in flight (the panel greys out its Send button).
signal busy_changed(busy: bool)

const CLIP_ICON := preload("res://assets/icons/paperclip.svg")
const REMOVE_ICON := preload("res://assets/icons/x.svg")
# The backend's limits (backend/mii/tasks.py), checked here too for a quick, friendly no.
const MAX_FILES := 10
const MAX_FILE_BYTES := 2 * 1024 * 1024
const MAX_TOTAL_BYTES := 8 * 1024 * 1024

var _title := LineEdit.new()
var _details := TextEdit.new()
var _issue := LineEdit.new()
var _issue_field: Control
var _message := Label.new()
var _busy := false
var _files: Array[String] = []  # paths of the attached files, in the order picked
var _chips := HFlowContainer.new()  # the attach button, then one chip per file
var _picker := FileDialog.new()


func _init() -> void:
	add_theme_constant_override("separation", 12)

	_title.placeholder_text = "e.g. Fix the typo on the login page"
	_title.max_length = 200
	_title.text_submitted.connect(func(_text): submit())
	add_child(_field("What should be done?", _title))

	_details.placeholder_text = "Details, files, acceptance criteria (optional)"
	_details.custom_minimum_size = Vector2(0, 110)
	_details.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	add_child(_field("Details", _details))

	# Godot concept: FileDialog is a Window; popup_centered() shows it and
	# `files_selected` hands back the picked paths. use_native_dialog asks the
	# OS for its own picker (Finder on macOS) where there is one.
	_picker.file_mode = FileDialog.FILE_MODE_OPEN_FILES
	_picker.access = FileDialog.ACCESS_FILESYSTEM
	_picker.use_native_dialog = true
	_picker.title = "Attach files"
	_picker.files_selected.connect(_add_files)
	add_child(_picker)
	_chips.add_theme_constant_override("h_separation", 6)
	_chips.add_theme_constant_override("v_separation", 6)
	add_child(_chips)
	_show_files()

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


## Start from an earlier package (News' "Try again" on a failed one): its title, details
## and GitHub issue number (null when it had none), ready to change and send again.
func fill(title: String, details: String, issue: Variant) -> void:
	_title.text = title
	_details.text = details
	_issue.text = str(int(issue)) if issue != null else ""


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
	var attachments := []
	for path in _files:
		var bytes := FileAccess.get_file_as_bytes(path)
		if bytes.is_empty() and FileAccess.get_open_error() != OK:
			_message.text = "Couldn't read %s." % path.get_file()
			return
		attachments.append({"name": path.get_file(), "content": Marshalls.raw_to_base64(bytes)})
	if attachments:
		body["attachments"] = attachments
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
	_files.clear()
	_show_files()
	sent.emit()


## Add picked files (skipping ones already attached), within the backend's limits.
func _add_files(paths: PackedStringArray) -> void:
	_message.text = ""
	for path in paths:
		if path in _files:
			continue
		if _files.size() == MAX_FILES:
			_message.text = "At most %d files per package." % MAX_FILES
			break
		var file := FileAccess.open(path, FileAccess.READ)
		var size := file.get_length() if file else -1
		if size < 0:
			_message.text = "Couldn't read %s." % path.get_file()
		elif size > MAX_FILE_BYTES:
			_message.text = "%s is too big (at most 2 MB a file)." % path.get_file()
		elif _total_bytes() + size > MAX_TOTAL_BYTES:
			_message.text = "That's too much to send together (at most 8 MB)."
		else:
			_files.append(path)
	_show_files()


func _total_bytes() -> int:
	var total := 0
	for path in _files:
		var file := FileAccess.open(path, FileAccess.READ)
		total += file.get_length() if file else 0
	return total


## Redraw the chips: "Attach files…", then one removable chip per file.
func _show_files() -> void:
	for child in _chips.get_children():
		_chips.remove_child(child)
		child.queue_free()
	var attach := _chip("Attach files…", CLIP_ICON)
	attach.tooltip_text = "Files the residents can read (copied next to their work, never committed)"
	attach.pressed.connect(func(): _picker.popup_centered_ratio(0.6))
	_chips.add_child(attach)
	for path in _files:
		var chip := _chip(path.get_file(), REMOVE_ICON)
		chip.tooltip_text = "%s\nClick to remove" % path
		chip.icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		chip.pressed.connect(
			func():
				_files.erase(path)
				_show_files()
		)
		_chips.add_child(chip)


func _chip(text: String, icon: Texture2D) -> Button:
	var chip := Button.new()
	chip.text = text
	chip.icon = icon
	chip.theme_type_variation = &"OutlineButton"
	chip.focus_mode = Control.FOCUS_NONE
	chip.add_theme_font_size_override("font_size", 13)
	chip.add_theme_constant_override("icon_max_width", 14)
	return chip


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
