class_name NewsScreen
extends PanelContainer
## The News tab: the island's newspaper, written from real backend events.
##
## - Stories: every event from Net (`event_received`) goes through NewsStories,
##   which keeps the newsworthy ones. On connect we also ask the backend for
##   older events (GET /islands/{id}/events, see docs/architecture.md).
## - Headline: the latest shipped package (or the latest story).
## - This week: from the insights endpoint, like the Insights tab.
## - Unread: stories newer than the last time you opened News. game.gd shows the
##   count on the nav button (`unread_changed`). Remembered in user://news.cfg.
## - Merge: a shipped story's merge button merges the package's branch into your
##   local checkout (POST /islands/{id}/tasks/{task_id}/merge). A conflict queues
##   a package for the residents to fix; the result shows in game.gd's toast.
##
## This node lives in game.tscn all the time (hidden when another tab is open),
## so it keeps reading the news and counting unread stories in the background.

signal unread_changed(count: int)
## A one-line result to show the player (game.gd puts it in the toast).
signal notice(message: String)
## "Try again" on a failed package: game.gd reopens its issue in the Mailbox's form.
signal retry_requested(task_id: String)

const SETTINGS := "user://news.cfg"
const MAX_FEED := 40
const FILTERS := [["all", "Everything"], ["shipped", "Shipped"], ["packages", "Packages"], ["residents", "Residents"]]
## [insights field, label, card color]
const WEEK := [
	["prs_opened", "PRs opened", "FBF3CF"],
	["prs_merged", "PRs merged", "E3F5EC"],
	["bubbles_answered", "Bubbles answered", "EEEAFE"],
	["keys_lent", "Keys lent", "FFF0DD"],
]
const WEEKDAYS := ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
const MERGE_ICON := preload("res://assets/icons/git-pull-request.svg")
const MAIL_ICON := preload("res://assets/icons/mail.svg")

var _desk := NewsStories.new()
var _live_events: Array[Dictionary] = []  # this session's events, added on top of the history
var _filter := "all"
var _island_id := ""
var _last_seen := 0.0
var _week := {}  # the insights `island` section
var _week_values := {}  # field -> value Label
var _history_request := 0  # only the latest GET .../events reply counts
var _week_request := 0  # same, for GET .../insights?range=week


func _ready() -> void:
	_load_settings()
	_build_filters()
	_build_week()

	Net.event_received.connect(_on_event)
	Net.snapshot_received.connect(
		func(island: Dictionary):
			if island.get("id") != _island_id:  # another island: start a fresh paper
				_island_id = str(island.get("id"))
				_desk = NewsStories.new()
				_live_events.clear()
				_week = {}
			_desk.know_residents(island.get("residents", []))
			_history_request += 1
			var path := "/islands/%s/events?limit=200" % str(island["id"]).uri_encode()
			Net.request_json(path, _on_history.bind(_history_request))
	)
	visibility_changed.connect(
		func():
			if visible:
				_opened()
	)
	%GitHubLink.pressed.connect(func(): OS.shell_open(str(%GitHubLink.get_meta("url", ""))))
	_show()


func unread() -> int:
	return _desk.stories.filter(func(s): return s["when"] > _last_seen).size()


func _on_event(event: Dictionary) -> void:
	if event.get("type") == "snapshot":
		return
	_live_events.append(event)
	if _live_events.size() > 500:
		_live_events.pop_front()
	if _desk.add(event).is_empty():
		return
	if is_visible_in_tree():
		_mark_seen()
		_show()
	else:
		unread_changed.emit(unread())


## Older events from the backend: rebuild the paper from them plus this session's.
func _on_history(ok: bool, data: Dictionary, asked: int) -> void:
	if not ok or asked != _history_request:
		return  # backend unreachable (keep the live-only paper), or an older island's reply
	var desk := NewsStories.new()
	for event in data.get("events", []):
		desk.add(event)
	desk.know_residents(Net.island.get("residents", []))
	for event in _live_events:
		desk.add(event)
	desk.stories.sort_custom(func(a, b): return a["when"] < b["when"])
	_desk = desk
	unread_changed.emit(unread())
	if is_visible_in_tree():
		_show()


func _on_week(ok: bool, data: Dictionary, asked: int) -> void:
	if asked != _week_request:
		return
	_week = data.get("island", {}) if ok else {}
	_show_week()


func _opened() -> void:
	_mark_seen()
	if not Net.island.is_empty():
		_week_request += 1
		var path := "/islands/%s/insights?range=week" % str(Net.island["id"]).uri_encode()
		Net.request_json(path, _on_week.bind(_week_request))
	_show()


func _mark_seen() -> void:
	_last_seen = Time.get_unix_time_from_system()
	_save("last_seen", _last_seen)
	unread_changed.emit(0)


# --- Drawing -------------------------------------------------------------------


func _show() -> void:
	var island := str(Net.island.get("name", "Island"))
	var now := Time.get_unix_time_from_system()
	var today := NewsStories.local_day(now)
	var todays := _desk.stories.filter(func(s): return NewsStories.local_day(s["when"]) == today)
	%Title.text = "The %s Gazette" % island
	%Edition.text = (
		"%s edition · %d stor%s today"
		% [
			WEEKDAYS[Time.get_datetime_dict_from_system()["weekday"]],
			todays.size(),
			"y" if todays.size() == 1 else "ies"
		]
	)
	_show_headline(island)
	_show_feed(todays)
	_show_week()


func _show_headline(island: String) -> void:
	var shipped := _desk.stories.filter(func(s): return s["section"] == "shipped")
	var story: Dictionary = shipped.back() if shipped else (_desk.stories.back() if _desk.stories else {})
	%GitHubLink.visible = story.has("pr_url")
	if story.is_empty():
		%Kicker.text = "HEADLINE"
		%HeadlineTitle.text = "All quiet on %s" % island
		%HeadlineBody.text = "No news yet. Stories appear here as residents pick up packages."
		return
	var ago := _ago(story["when"])
	if story["section"] == "shipped":
		%Kicker.text = "HEADLINE · %s" % ("JUST SHIPPED" if ago == "just now" else "SHIPPED " + ago.to_upper())
		%HeadlineTitle.text = "“%s” is done" % story.get("title", "A package")
		var who := str(Net.find_resident(story["agent"]).get("name", ""))
		%HeadlineBody.text = (
			("Delivered by %s." % who if who else "Delivered.")
			+ (" The pull request is waiting on GitHub." if story.has("pr_url") else "")
		)
		if story.has("pr_url"):
			%GitHubLink.set_meta("url", story["pr_url"])
	else:
		%Kicker.text = "HEADLINE · LATEST"
		%HeadlineTitle.text = story["text"]
		%HeadlineBody.text = "Nothing shipped yet, but the island is busy. (%s)" % ago


func _show_feed(todays: Array) -> void:
	for child in %Feed.get_children():
		if child != %FeedEmpty:
			child.queue_free()
	# Today's stories, or the latest ones if nothing happened today.
	var list: Array = todays if todays else _desk.stories
	%FeedTitle.text = "Today on the island" if todays or _desk.stories.is_empty() else "Latest on the island"
	list = list.filter(func(s): return _filter == "all" or s["section"] == _filter)
	list.reverse()  # newest first
	%FeedEmpty.visible = list.is_empty()
	%FeedEmpty.text = (
		"Nothing yet today. Stories show up here as residents work."
		if _filter == "all"
		else "No %s stories yet." % _filter
	)
	for story in list.slice(0, MAX_FEED):
		%Feed.add_child(_story_row(story))


func _story_row(story: Dictionary) -> Control:
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 0)
	var row := HBoxContainer.new()
	row.custom_minimum_size.y = 40
	row.add_theme_constant_override("separation", 12)

	var time := UI.label(NewsStories.clock(story["when"]), 11, false)
	time.custom_minimum_size.x = 38
	var resident := Net.find_resident(story["agent"])
	var avatar := UI.avatar(resident, 26, 0.75)

	var text := UI.label(story["text"], 13, true)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	text.tooltip_text = story["text"]
	text.mouse_filter = Control.MOUSE_FILTER_PASS
	var tag := UI.label(story["tag"], 12, true)
	tag.add_theme_color_override("font_color", story["color"])

	var parts: Array[Control] = [time, avatar, text, tag]
	if story.get("failed"):
		parts.append(_retry_button(story))
	elif story.has("task"):
		parts.append(_merge_button(story))
	for part in parts:
		part.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(part)
	rows.add_child(row)
	var line := HSeparator.new()
	var style := StyleBoxLine.new()
	style.color = Color("E9E5D6")
	line.add_theme_stylebox_override("separator", style)
	rows.add_child(line)
	return rows


## Merge the package's branch into the branch checked out in the island's folder.
## On a conflict the backend sends a "Fix merge conflicts" package to the mailbox.
func _merge_button(story: Dictionary) -> Button:
	var button := Button.new()
	button.icon = MERGE_ICON
	button.theme_type_variation = &"FlatIconButton"
	button.focus_mode = Control.FOCUS_NONE
	button.tooltip_text = "Merge this package into your local branch"
	button.pressed.connect(
		func():
			button.disabled = true
			Net.request_json(
				"/islands/%s/tasks/%s/merge" % [_island_id.uri_encode(), str(story["task"]).uri_encode()],
				func(ok: bool, data: Dictionary):
					if is_instance_valid(button):
						button.disabled = false
					notice.emit(_merge_message(ok, data, str(story.get("title", "the package")))),
				HTTPClient.METHOD_POST,
				{}
			)
	)
	return button


## A failed package: open its original issue in the Mailbox's "Send a new issue" form, to resend.
func _retry_button(story: Dictionary) -> Button:
	var button := Button.new()
	button.text = "Try again"
	button.icon = MAIL_ICON
	button.theme_type_variation = &"OutlineButton"
	button.add_theme_font_size_override("font_size", 12)
	button.focus_mode = Control.FOCUS_NONE
	button.tooltip_text = "Open “%s” in the Mailbox to change it and send it again" % story.get("title", "the package")
	button.pressed.connect(func(): retry_requested.emit(str(story["task"])))
	return button


func _merge_message(ok: bool, data: Dictionary, title: String) -> String:
	if not ok:
		return "Couldn't merge “%s”: %s" % [title, data["error"]]
	if data.get("merged"):
		return "Merged “%s” into %s." % [title, data.get("branch", "your branch")]
	var files: Array = data.get("conflicts", [])
	return (
		"“%s” conflicts with %s (%s). Your folder is unchanged; a package to fix it is on its way to the mailbox."
		% [title, data.get("branch", "your branch"), ", ".join(files)]
	)


func _show_week() -> void:
	for field in _week_values:
		var value = _week.get(field)
		_week_values[field].text = str(int(value)) if value != null else "—"


func _ago(when: float) -> String:
	var minutes := int((Time.get_unix_time_from_system() - when) / 60.0)
	if minutes < 2:
		return "just now"
	if minutes < 90:
		return "%d min ago" % minutes
	return "%d h ago" % roundi(minutes / 60.0)


# --- Building the controls -----------------------------------------------------


func _build_filters() -> void:
	for f in FILTERS:
		var chip := Button.new()
		chip.text = f[1]
		chip.focus_mode = Control.FOCUS_NONE
		chip.set_meta("filter", f[0])
		chip.pressed.connect(
			func():
				_filter = f[0]
				for c in %Filters.get_children():
					c.theme_type_variation = &"FilterChipActive" if c.get_meta("filter") == _filter else &"FilterChip"
				_show()
		)
		chip.theme_type_variation = &"FilterChipActive" if f[0] == _filter else &"FilterChip"
		%Filters.add_child(chip)


func _build_week() -> void:
	for w in WEEK:
		var card := PanelContainer.new()
		card.theme_type_variation = &"StatCard"
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		card.custom_minimum_size.y = 76
		var style: StyleBoxFlat = get_theme_stylebox("panel", "StatCard").duplicate()
		style.bg_color = Color(w[2])
		card.add_theme_stylebox_override("panel", style)
		var rows := VBoxContainer.new()
		rows.add_theme_constant_override("separation", 0)
		var value := Label.new()
		value.theme_type_variation = &"StatValue"
		value.add_theme_font_size_override("font_size", 26)
		value.text = "—"
		var label := UI.label(w[1], 12, false)
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.custom_minimum_size.x = 1
		rows.add_child(value)
		rows.add_child(label)
		card.add_child(rows)
		%Week.add_child(card)
		_week_values[w[0]] = value


# --- Settings (user://news.cfg) ------------------------------------------------
# Godot concept: user:// is a per-user folder Godot gives every game for saves
# and settings. ConfigFile reads and writes simple INI-style files.


func _load_settings() -> void:
	var config := ConfigFile.new()
	if config.load(SETTINGS) != OK:
		_last_seen = Time.get_unix_time_from_system()  # first run: start with nothing unread
		_save("last_seen", _last_seen)
		return
	_last_seen = float(config.get_value("news", "last_seen", Time.get_unix_time_from_system()))


func _save(key: String, value) -> void:
	var config := ConfigFile.new()
	config.load(SETTINGS)  # keep the other keys
	config.set_value("news", key, value)
	config.save(SETTINGS)
