class_name OpenPrs
extends VBoxContainer
## The Post Office's "Pull requests to merge": delivered packages whose pull request is
## still open on GitHub, each with a link to it and a Merge button. The backend merges it
## (POST /islands/{id}/tasks/{task_id}/merge-pr, through its `gh` login), so nobody has
## to visit GitHub. building_panel.gd shows it above its list, and only at the Post Office.

const PR_ICON := preload("res://assets/icons/git-pull-request.svg")
const LIMIT := 50  # the newest delivered packages checked for an open pull request

var _tasks: Array = []  # delivered packages whose PR is still open, newest first
var _merging := {}  # task id -> true while its Merge request is on its way
var _active := false
var _card_row: Callable  # the panel's card builder: (left, middle, right) -> Control
var _icon_tile: Callable  # the panel's tinted icon square: (texture, color) -> Control


func _init() -> void:
	add_theme_constant_override("separation", 10)
	visible = false
	# A package just delivered, or merged somewhere else: the list changes.
	Net.task_updated.connect(
		func(task: Dictionary):
			if _active and task.get("pr_url"):
				_load()
	)


## The panel opened on a building: load and show the open PRs at the Post Office, hide elsewhere.
func show_for_post_office(on: bool, card_row: Callable, icon_tile: Callable) -> void:
	_active = on
	_card_row = card_row
	_icon_tile = icon_tile
	visible = false
	if on:
		_load()


func _load() -> void:
	if Net.island.is_empty():
		return
	var path := "/islands/%s/tasks?status=done&limit=%d" % [str(Net.island["id"]).uri_encode(), LIMIT]
	Net.request_json(
		path,
		func(ok: bool, data: Dictionary):
			if ok:
				_tasks = data.get("tasks", []).filter(func(t): return t.get("pr_url") and not t.get("merged"))
				_redraw()
	)


func _redraw() -> void:
	for child in get_children():
		child.queue_free()
	visible = _active and not _tasks.is_empty()
	if not visible:
		return
	add_child(UI.label("Pull requests to merge", 15, true))
	for task in _tasks:
		add_child(_card(task))


## One open pull request: its package's title, a link to it on GitHub, and Merge.
func _card(task: Dictionary) -> Control:
	var task_id := str(task["id"])
	var url := str(task["pr_url"])
	var texts := VBoxContainer.new()
	texts.add_theme_constant_override("separation", 2)
	texts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	texts.add_child(UI.label(str(task.get("title", "A package")), 17, true))
	var link := LinkButton.new()
	link.text = url.trim_prefix("https://github.com/")
	link.add_theme_font_size_override("font_size", 13)
	link.pressed.connect(OS.shell_open.bind(url))
	texts.add_child(link)

	var merge := Button.new()
	merge.text = "Merging…" if _merging.has(task_id) else "Merge"
	merge.icon = PR_ICON
	merge.theme_type_variation = &"AnswerButton"
	merge.focus_mode = Control.FOCUS_NONE
	merge.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	merge.disabled = _merging.has(task_id) or not Net.is_online
	merge.pressed.connect(_merge.bind(task))
	var color: Color = BuildingData.TYPES["post_office"]["color"]
	return _card_row.call(_icon_tile.call(PR_ICON, color), texts, merge)


func _merge(task: Dictionary) -> void:
	var task_id := str(task["id"])
	_merging[task_id] = true
	_redraw()
	var path := "/islands/%s/tasks/%s/merge-pr" % [str(Net.island["id"]).uri_encode(), task_id.uri_encode()]
	Net.request_json(
		path,
		func(ok: bool, data: Dictionary):
			_merging.erase(task_id)
			if ok:
				_tasks = _tasks.filter(func(t): return str(t["id"]) != task_id)
			else:
				Net.server_error.emit("Couldn't merge “%s”: %s" % [task.get("title", "the package"), data["error"]])
			_redraw(),
		HTTPClient.METHOD_POST,
		{}
	)
