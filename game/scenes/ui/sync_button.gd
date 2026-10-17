class_name SyncButton
extends Button
## The bottom nav's "Sync" button: pulls the island's branch from GitHub
## (and pushes it back), with a small "2 behind" count next to the word.
##
## The backend does the git work (`GET /islands/{id}/sync` for the count,
## `POST` to sync, which also sends a fix package for every open PR that now
## conflicts); this button only shows the numbers and sends the click.
## game.gd shows `finished` messages in its toast.

## A sync ended (or failed): the backend's message, for the toast.
signal finished(message: String)

const ICON := preload("res://assets/icons/rotate-ccw.svg")
const REFRESH_SECONDS := 60.0
const TOOLTIP := "Pull the island's branch from GitHub, push it back, fix PRs that conflict"

var _text: Label
var _count: Label
var _syncing := false
var _checking := false

## Godot concept: a Timer node counts down and emits `timeout` when it reaches
## zero; it starts over by itself unless `one_shot` is set. It only counts while
## it's in the scene tree, so it's added as a child of this button.
var _timer := Timer.new()


func _init() -> void:
	theme_type_variation = &"NavButton"
	custom_minimum_size = Vector2(160, 50)
	tooltip_text = TOOLTIP

	# Same look as the other nav buttons in game.tscn: icon + big word, centred.
	var content := HBoxContainer.new()
	content.set_anchors_preset(Control.PRESET_FULL_RECT)
	content.mouse_filter = Control.MOUSE_FILTER_IGNORE
	content.alignment = BoxContainer.ALIGNMENT_CENTER
	content.add_theme_constant_override("separation", 8)
	add_child(content)
	var icon := TextureRect.new()
	icon.texture = ICON
	icon.custom_minimum_size = Vector2(26, 26)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	content.add_child(icon)
	_text = Label.new()
	_text.theme_type_variation = &"NavText"
	_text.text = "Sync"
	content.add_child(_text)
	_count = UI.label("", 13, false)
	_count.visible = false
	content.add_child(_count)

	_timer.wait_time = REFRESH_SECONDS
	add_child(_timer)


func _ready() -> void:
	pressed.connect(sync)
	_timer.timeout.connect(refresh)
	_timer.start()
	# A new island's snapshot, or coming back to the island from the Archipelago.
	Net.snapshot_received.connect(func(_island): refresh())
	visibility_changed.connect(refresh)


## Ask the backend how far the island's branch is from GitHub. Only while an island is open
## (the nav is hidden on the Archipelago); errors just clear the count.
func refresh() -> void:
	if _checking or _syncing or Net.island.is_empty() or not is_visible_in_tree():
		return
	_checking = true
	var island_id := str(Net.island["id"])
	Net.request_json(
		"/islands/%s/sync" % island_id.uri_encode(),
		func(ok: bool, data: Dictionary):
			_checking = false
			if island_id != str(Net.island.get("id", "")):
				return  # another island by now
			_show_status(data if ok else {})
	)


## The click: sync, then show what happened in the toast.
func sync() -> void:
	if _syncing or Net.island.is_empty():
		return
	_syncing = true
	disabled = true
	_text.text = "Syncing…"
	var path := "/islands/%s/sync" % str(Net.island["id"]).uri_encode()
	Net.request_json(path, _on_synced, HTTPClient.METHOD_POST, {})


func _on_synced(ok: bool, data: Dictionary) -> void:
	_syncing = false
	_text.text = "Sync"
	disabled = false
	if ok:
		_show_status(data)
		finished.emit(str(data.get("message", "Synced.")))
	else:
		finished.emit("Couldn't sync: %s" % data["error"])
		refresh()


## "2 behind", "1 ahead", both, or nothing. No `origin` remote: nothing to sync with.
func _show_status(data: Dictionary) -> void:
	var has_remote := bool(data.get("remote", true))
	disabled = not has_remote
	tooltip_text = TOOLTIP if has_remote else "This island's repository has no GitHub remote"
	var parts: Array[String] = []
	var behind := int(data.get("behind", 0))
	var ahead := int(data.get("ahead", 0))
	if has_remote and behind > 0:
		parts.append("%d behind" % behind)
	if has_remote and ahead > 0:
		parts.append("%d ahead" % ahead)
	_count.text = " · ".join(parts)
	_count.visible = not parts.is_empty()
