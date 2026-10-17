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
## - Daily digest: when on, the game opens on News the first time you start it
##   each day.
## - Replay: a shipped story's ↻ button plays that package's recorded run
##   again on the island (POST /replay/{task_id}), the demo safety net. Nothing
##   is redone or saved; game.gd switches to the island view to watch it.
##
## This node lives in game.tscn all the time (hidden when another tab is open),
## so it keeps reading the news and counting unread stories in the background.

signal unread_changed(count: int)
## A recorded run started playing (game.gd shows the island).
signal replay_started

const SETTINGS := "user://news.cfg"
const MAX_FEED := 40
const FILTERS := [["all", "Everything"], ["shipped", "Shipped"], ["packages", "Packages"], ["residents", "Residents"]]
## [insights field, label, card color]
const WEEK := [
	["prs_opened", "PRs opened", "FBF3CF"], ["prs_merged", "PRs merged", "E3F5EC"],
	["bubbles_answered", "Bubbles answered", "EEEAFE"], ["keys_lent", "Keys lent", "FFF0DD"],
]
const WEEKDAYS := ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
const REPLAY_SPEED := 4  # recorded runs play back this many times faster
const REPLAY_ICON := preload("res://assets/icons/rotate-ccw.svg")

var _desk := NewsStories.new()
var _live_events: Array[Dictionary] = []  # this session's events, to replay over the history
var _filter := "all"
var _island_id := ""
var _last_seen := 0.0
var _last_opened_day := ""
var _week := {}  # the insights `island` section
var _week_values := {}  # field -> value Label
var _history := HTTPRequest.new()
var _week_http := HTTPRequest.new()


func _ready() -> void:
	_load_settings()
	add_child(_history)
	add_child(_week_http)
	_history.request_completed.connect(_on_history)
	_week_http.request_completed.connect(_on_week)
	_build_filters()
	_build_week()

	Net.event_received.connect(_on_event)
	Net.snapshot_received.connect(func(island: Dictionary):
		if island.get("id") != _island_id:  # another island: start a fresh paper
			_island_id = str(island.get("id"))
			_desk = NewsStories.new()
			_live_events.clear()
			_week = {}
		_desk.know_residents(island.get("residents", []))
		_history.cancel_request()
		_history.request(Net.http_url("/islands/%s/events?limit=200" % island["id"])))
	visibility_changed.connect(func(): if visible: _opened())
	%GitHubLink.pressed.connect(func(): OS.shell_open(str(%GitHubLink.get_meta("url", ""))))
	%Digest.button_pressed = _digest_on()
	%Digest.toggled.connect(func(on: bool): _save("digest", on))
	_show()


## True if the game should open on this tab (daily digest on, first start today).
func wants_morning_paper() -> bool:
	return _digest_on() and _last_opened_day != NewsStories.local_day(Time.get_unix_time_from_system())


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
func _on_history(result: int, code: int, _headers, body: PackedByteArray) -> void:
	var data = JSON.parse_string(body.get_string_from_utf8()) if code == 200 else null
	if result != HTTPRequest.RESULT_SUCCESS or typeof(data) != TYPE_DICTIONARY:
		return  # backend unreachable: keep the live-only paper
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


func _on_week(_result: int, code: int, _headers, body: PackedByteArray) -> void:
	var data = JSON.parse_string(body.get_string_from_utf8()) if code == 200 else null
	_week = data.get("island", {}) if typeof(data) == TYPE_DICTIONARY else {}
	_show_week()


func _opened() -> void:
	_mark_seen()
	_save("opened_day", NewsStories.local_day(Time.get_unix_time_from_system()))
	if not Net.island.is_empty():
		_week_http.cancel_request()
		_week_http.request(Net.http_url("/islands/%s/insights?range=week" % Net.island["id"]))
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
	%Edition.text = "%s edition · %d stor%s today" % [
		WEEKDAYS[Time.get_datetime_dict_from_system()["weekday"]], todays.size(), "y" if todays.size() == 1 else "ies"]
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
		%HeadlineBody.text = ("Delivered by %s." % who if who else "Delivered.") + \
			(" The pull request is waiting on GitHub." if story.has("pr_url") else "")
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
	%FeedEmpty.text = "Nothing yet today. Stories show up here as residents work." if _filter == "all" \
		else "No %s stories yet." % _filter
	for story in list.slice(0, MAX_FEED):
		%Feed.add_child(_story_row(story))


func _story_row(story: Dictionary) -> Control:
	var rows := VBoxContainer.new()
	rows.add_theme_constant_override("separation", 0)
	var row := HBoxContainer.new()
	row.custom_minimum_size.y = 40
	row.add_theme_constant_override("separation", 12)

	var time := _label(NewsStories.clock(story["when"]), 11, false)
	time.custom_minimum_size.x = 38
	var resident := Net.find_resident(story["agent"])
	var color := Color(str(resident.get("color", "#8A8FA8")))
	var avatar := PanelContainer.new()
	avatar.custom_minimum_size = Vector2(26, 26)
	avatar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var circle: StyleBoxFlat = get_theme_stylebox("panel", "AvatarCircle").duplicate()
	circle.bg_color = color.lerp(Color.WHITE, 0.75)
	circle.set_corner_radius_all(13)
	avatar.add_theme_stylebox_override("panel", circle)
	var initial := _label(str(resident.get("name", "•")).left(1), 13, true)
	initial.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	initial.add_theme_color_override("font_color", color.darkened(0.2))
	avatar.add_child(initial)

	var text := _label(story["text"], 13, true)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	text.tooltip_text = story["text"]
	text.mouse_filter = Control.MOUSE_FILTER_PASS
	var tag := _label(story["tag"], 12, true)
	tag.add_theme_color_override("font_color", story["color"])

	var parts: Array[Control] = [time, avatar, text, tag]
	if story.has("task"):
		parts.append(_replay_button(str(story["task"])))
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


func _replay_button(task_id: String) -> Button:
	var button := Button.new()
	button.icon = REPLAY_ICON
	button.theme_type_variation = &"FlatIconButton"
	button.focus_mode = Control.FOCUS_NONE
	button.tooltip_text = "Replay this package's run on the island (%dx speed, nothing is redone)" % REPLAY_SPEED
	button.pressed.connect(func():
		button.disabled = true
		# The backend answers when the replay is over; the events stream in meanwhile.
		Net.request_json("/replay/%s?speed=%d" % [task_id.uri_encode(), REPLAY_SPEED], func(ok: bool, data: Dictionary):
			if is_instance_valid(button):
				button.disabled = false
			if not ok:
				push_warning("Replay failed: %s" % data["error"]), HTTPClient.METHOD_POST)
		replay_started.emit())
	return button


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
		chip.pressed.connect(func():
			_filter = f[0]
			for c in %Filters.get_children():
				c.theme_type_variation = &"FilterChipActive" if c.get_meta("filter") == _filter else &"FilterChip"
			_show())
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
		var label := _label(w[1], 12, false)
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.custom_minimum_size.x = 1
		rows.add_child(value)
		rows.add_child(label)
		card.add_child(rows)
		%Week.add_child(card)
		_week_values[w[0]] = value


func _label(text: String, size: int, strong: bool) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"LabelStrong" if strong else &"LabelMuted"
	label.add_theme_font_size_override("font_size", size)
	return label


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
	_last_opened_day = str(config.get_value("news", "opened_day", ""))


func _digest_on() -> bool:
	var config := ConfigFile.new()
	config.load(SETTINGS)
	return bool(config.get_value("news", "digest", true))


func _save(key: String, value) -> void:
	var config := ConfigFile.new()
	config.load(SETTINGS)  # keep the other keys
	config.set_value("news", key, value)
	config.save(SETTINGS)
