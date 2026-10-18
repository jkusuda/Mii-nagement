class_name InsightsScreen
extends PanelContainer
## The Insights tab: four stat cards, packages delivered per day, where packages
## are waiting, and a comfort report.
##
## Numbers come from the backend's GET /islands/{id}/insights (shape in
## docs/http-api.md, "Insights payload"). A missing field shows a dash and
## "Not tracked yet".
##
## "Where packages are waiting" and the residents' moods come straight from the
## live snapshot (Net.island), so those are always current.

signal see_residents

const RANGES := [["today", "Today"], ["week", "This week"], ["month", "This month"]]
const RANGE_NAMES := {
	"today": ["today", "yesterday"], "week": ["this week", "last week"], "month": ["this month", "last month"]
}

## [key, title, card color, note color]
const STATS := [
	["done", "Issues closed", "FBF3CF", "1E9E6A"],
	["prs", "PRs opened", "E3F5EC", "1E9E6A"],
	["answer", "Your answer time", "EEEAFE", "7553DF"],
	["tests", "Tests passing", "FFF0DD", "C57722"],
]
const WEEKDAYS := ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
const BAR_COLOR := Color("6F55DB")
const TODAY_COLOR := Color("B3A6EE")
const BUSIEST_COLOR := Color("C57A2C")
const QUIET_COLOR := Color("A0A6BE")
const BAR_MAX_HEIGHT := 150.0

var _range := "week"
var _data := {}  # the last insights response ({} = none yet)
var _reachable := true  # did the last request work?
var _request := 0  # counts requests: only the newest answer is shown (the range or island may change)
var _stats := {}  # stat key -> [value label, note label]


func _ready() -> void:
	_build_ranges()
	_build_stats()
	%SeeResidents.pressed.connect(see_residents.emit)

	# Refresh when the screen opens and when a task changes while it's open.
	visibility_changed.connect(
		func():
			if visible:
				refresh()
	)
	Net.task_updated.connect(
		func(_task):
			if is_visible_in_tree():
				refresh()
	)
	Net.island_changed.connect(
		func():
			if is_visible_in_tree():
				_show_live()
	)
	_show()


## Ask the backend for fresh numbers (and redraw the live parts right away).
func refresh() -> void:
	_show_live()
	if Net.island.is_empty():
		return
	_request += 1
	var path := "/islands/%s/insights?range=%s" % [str(Net.island["id"]).uri_encode(), _range]
	Net.request_json(path, _on_response.bind(_request))


func _on_response(ok: bool, data: Dictionary, request: int) -> void:
	if request != _request:
		return  # an older request: a newer one is on its way
	_reachable = ok
	_data = data if ok else {}
	_show()


## Redraw everything.
func _show() -> void:
	_show_stats()
	_show_per_day()
	_show_live()


## The parts that come from Net.island: subtitle, waiting packages, tip, comfort.
func _show_live() -> void:
	var island_name := str(Net.island.get("name", "the island"))
	%Subtitle.text = "How %s is doing" % island_name
	if not Net.island.is_empty() and not _reachable:
		%Subtitle.text += " · couldn't reach the backend"
	_show_waiting()
	_show_comfort()


# --- Stat cards ----------------------------------------------------------------


func _show_stats() -> void:
	var island: Dictionary = _data.get("island", {})
	var last: String = RANGE_NAMES[_range][1]

	_set_stat("done", _count(island, "tasks_done"), _change(island, "tasks_done", last))
	_set_stat("prs", _count(island, "prs_opened"), _change(island, "prs_opened", last))

	var seconds = island.get("answer_seconds")
	var before = island.get("answer_seconds_before")
	var answer_note := "Not tracked yet"
	if seconds != null:
		answer_note = "How fast you answer bubbles"
		if before != null:
			answer_note = "Quicker than %s" % last if seconds < before else "Slower than %s" % last
	_set_stat("answer", _duration(seconds) if seconds != null else "—", answer_note)

	var rate = island.get("tests_pass_rate")
	var flaky = island.get("flaky_tests")
	var tests_note := "Not tracked yet"
	if rate != null:
		if flaky == null:
			tests_note = "Lab test runs that pass"
		else:
			tests_note = "%d flaky in the Lab" % flaky if flaky else "No flaky tests"
	_set_stat("tests", "%d%%" % roundi(rate * 100.0) if rate != null else "—", tests_note)


func _set_stat(key: String, value: String, note: String) -> void:
	_stats[key][0].text = value
	_stats[key][1].text = note


func _count(island: Dictionary, field: String) -> String:
	return str(int(island[field])) if island.get(field) != null else "—"


## "+4 on last week", from `field` and `field_before`.
func _change(island: Dictionary, field: String, last: String) -> String:
	if island.get(field) == null:
		return "Not tracked yet"
	if island.get(field + "_before") == null:
		var failed := int(island.get("tasks_failed", 0)) if field == "tasks_done" else 0
		return "%d didn't make it" % failed if failed else "All time"
	var diff := int(island[field]) - int(island[field + "_before"])
	if diff == 0:
		return "Same as %s" % last
	return "%+d on %s" % [diff, last]


func _duration(seconds: float) -> String:
	if seconds < 90:
		return "%d s" % roundi(seconds)
	if seconds < 90 * 60:
		return "%d min" % roundi(seconds / 60.0)
	return "%.1f h" % (seconds / 3600.0)


# --- Packages delivered per day ------------------------------------------------


func _show_per_day() -> void:
	for child in %Bars.get_children():
		child.queue_free()
	var days: Array = _data.get("per_day", [])
	%ChartEmpty.visible = days.is_empty()
	if days.is_empty():
		%ChartEmpty.text = (
			"No deliveries recorded yet. They'll show up here." if _reachable else "Couldn't reach the backend."
		)
		return

	var most := 1
	for d in days:
		most = maxi(most, int(d.get("done", 0)))
	var few := days.size() <= 14  # room for a number over every bar?
	# A month is 30 bars: tighter gaps, and a date under every 5th one, so it fits.
	%Bars.add_theme_constant_override("separation", 10 if few else 3)

	for i in days.size():
		var count := int(days[i].get("done", 0))
		var is_today := i == days.size() - 1 and _range != "today"
		var column := VBoxContainer.new()
		column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		column.alignment = BoxContainer.ALIGNMENT_END
		column.add_theme_constant_override("separation", 4)

		var number := UI.label(str(count) if few else "", 12, true)
		number.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		var bar := Panel.new()
		bar.custom_minimum_size = Vector2(0, maxf(4.0, BAR_MAX_HEIGHT * count / most))
		bar.tooltip_text = "%s: %d" % [days[i].get("day", ""), count]
		var style := StyleBoxFlat.new()
		style.bg_color = TODAY_COLOR if is_today else BAR_COLOR
		style.corner_radius_top_left = 8
		style.corner_radius_top_right = 8
		bar.add_theme_stylebox_override("panel", style)
		var day_text := _day_name(str(days[i].get("day", "")), few)
		if not few and (days.size() - 1 - i) % 5 != 0:
			day_text = ""
		var day := UI.label("Today" if is_today else day_text, 12, false)
		day.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER

		column.add_child(number)
		column.add_child(bar)
		column.add_child(day)
		%Bars.add_child(column)


## "2026-10-12" -> "Mon" (or "12" when there are many bars).
func _day_name(date: String, short_list: bool) -> String:
	if date.length() < 10:
		return date
	if not short_list:
		return date.substr(8, 2)
	var parts := Time.get_datetime_dict_from_datetime_string(date.left(10) + "T00:00:00", true)
	return WEEKDAYS[parts["weekday"]]


# --- Where packages are waiting (live) -----------------------------------------


func _show_waiting() -> void:
	for child in %Waiting.get_children():
		child.queue_free()

	# Open tasks per building, from the live snapshot.
	# A queued package isn't at a building yet: it waits at the Mailbox (on other islands
	# the mailbox's id is "<island>_mailbox", so look it up by type).
	var waiting := {}
	var mailbox := ""
	for b in Net.island.get("buildings", []):
		waiting[b["id"]] = 0
		if b.get("type") == "mailbox":
			mailbox = str(b["id"])
	for t in Net.island.get("tasks", []):
		var where := str(t["building"]) if t.get("building") else mailbox
		waiting[where] = waiting.get(where, 0) + 1

	var order: Array = waiting.keys()
	order.sort_custom(func(a, b): return waiting[a] > waiting[b])
	var most := maxi(1, waiting[order[0]] if order else 0)
	var total := 0
	for id in order:
		total += waiting[id]
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 10)
		var title := UI.label(str(Net.find_building(id).get("name", id.capitalize())), 13, true)
		title.custom_minimum_size.x = 92
		var bar := ProgressBar.new()
		bar.theme_type_variation = &"NotebookBar"
		bar.show_percentage = false
		bar.custom_minimum_size.y = 16
		bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		bar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		bar.max_value = most
		bar.value = waiting[id]
		var fill: StyleBoxFlat = get_theme_stylebox("fill", "NotebookBar").duplicate()
		fill.bg_color = BUSIEST_COLOR if id == order[0] and waiting[id] > 0 else QUIET_COLOR
		fill.set_corner_radius_all(8)
		bar.add_theme_stylebox_override("fill", fill)
		var bg: StyleBoxFlat = get_theme_stylebox("background", "NotebookBar").duplicate()
		bg.bg_color = Color(1, 1, 1, 0)  # the mockup has no track behind the bars
		bar.add_theme_stylebox_override("background", bg)
		var count := UI.label(str(waiting[id]), 13, false)
		count.custom_minimum_size.x = 18
		count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		row.add_child(title)
		row.add_child(bar)
		row.add_child(count)
		%Waiting.add_child(row)

	if _data.get("tip"):
		%Tip.text = str(_data["tip"])
	elif Net.island.is_empty():
		%Tip.text = "Waiting for the island…"
	elif total == 0:
		%Tip.text = "No packages waiting. A calm day on the island."
	elif waiting[order[0]] >= 3:
		%Tip.text = (
			"The %s is looking busy. Another resident could lend a hand."
			% Net.find_building(order[0]).get("name", "island")
		)
	else:
		%Tip.text = "Packages are moving along nicely."


# --- Comfort report (live moods + answered bubbles) ----------------------------


func _show_comfort() -> void:
	var parts := ["Comfort report:"]
	var answered = _data.get("island", {}).get("bubbles_answered")
	if answered != null:
		parts.append("%d bubble%s answered %s." % [answered, "" if answered == 1 else "s", RANGE_NAMES[_range][0]])
	var residents: Array = Net.island.get("residents", [])
	if residents.is_empty():
		parts.append("No residents yet.")
	else:
		parts.append("Residents %s." % ResidentData.mood_summary(residents).replace(" · ", ", "))
	%ComfortLabel.text = " ".join(parts)


# --- Building the controls -----------------------------------------------------


func _build_ranges() -> void:
	for r in RANGES:
		var chip := Button.new()
		chip.text = r[1]
		chip.focus_mode = Control.FOCUS_NONE
		chip.pressed.connect(
			func():
				_range = r[0]
				_style_ranges()
				refresh()
		)
		chip.set_meta("range", r[0])
		%Ranges.add_child(chip)
	_style_ranges()


func _style_ranges() -> void:
	for chip in %Ranges.get_children():
		chip.theme_type_variation = &"FilterChipActive" if chip.get_meta("range") == _range else &"FilterChip"


func _build_stats() -> void:
	for s in STATS:
		var card := PanelContainer.new()
		card.theme_type_variation = &"StatCard"
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var style: StyleBoxFlat = get_theme_stylebox("panel", "StatCard").duplicate()
		style.bg_color = Color(s[2])
		card.add_theme_stylebox_override("panel", style)

		var rows := VBoxContainer.new()
		rows.add_theme_constant_override("separation", 0)
		var title := UI.label(s[1], 14, true)
		var value := Label.new()
		value.theme_type_variation = &"StatValue"
		value.text = "—"
		var note := UI.label("", 12, false)
		note.add_theme_color_override("font_color", Color(s[3]))
		note.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		rows.add_child(title)
		rows.add_child(value)
		rows.add_child(note)
		card.add_child(rows)
		%Stats.add_child(card)
		_stats[s[0]] = [value, note]
