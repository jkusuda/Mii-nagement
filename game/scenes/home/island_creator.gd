class_name IslandCreator
extends Control
## Found a new island, in two steps:
##   1. Name & repo: a name, a GitHub repo (listed by the backend through the
##      `gh` CLI, or typed as owner/name), and the branch residents start from.
##      A live 3D preview shows the island in the sea.
##   2. Town & residents: the town plan (which buildings and roads), the
##      residents you make for it, and house rules.
## "Found" sends POST /islands, then game.gd visits the new island and moves
## the residents in (create_resident, one by one, once its snapshot arrives).
##
## Island look (seasons) is shown but not built yet.

signal closed
signal founded(island: Dictionary, residents: Array)
## "Make your own" / editing a resident: game.gd opens the resident creator and
## hands the result back with put_resident().
signal resident_requested(resident: Dictionary, index: int)

const NAMES := ["Fern Harbor", "Maple Hollow", "Driftwood Isle", "Sunny Shoals", "Pebble Cove",
	"Coral Key", "Willow Reach", "Mossy Point", "Tidepool Isle", "Lantern Bay"]
## plan id -> [title, description, building types]. Must match TOWN_PLANS in backend/mii/islands.py.
const PLANS := {
	"classic": ["Classic town", "Mailbox · Workshop · Lab · Courthouse · Post Office",
		["mailbox", "workshop", "lab", "courthouse", "post_office"]],
	"quick_fixes": ["Quick fixes", "Mailbox · Workshop · Post Office\nSmall chores, no tests or review stop.",
		["mailbox", "workshop", "post_office"]],
}
## [rule, label, ask by default]. Must match RULES in backend/mii/islands.py.
const RULES := [["merge_pr", "Merging a PR", true], ["install_packages", "Installing packages", true],
	["shell", "Running shell commands", false]]
const LOOKS := [["Spring", "9BD77A"], ["Autumn", "E0A85C"], ["Tropic", "7FD3B5"], ["Snowy", "DDE6F2"]]
const TOGGLE_ON := preload("res://assets/ui/toggle_on.svg")
const TOGGLE_OFF := preload("res://assets/ui/toggle_off.svg")
const CHECK := preload("res://assets/icons/check.svg")
const DashedButton := preload("res://scenes/ui/dashed_button.gd")
const REPO_PATTERN := "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"

var _step := 1
var _repos: Array = []    # [{"repo", "private", "updated_at"}] from the backend
var _repo := ""           # chosen "owner/name" ("" = none)
var _branch := "main"
var _plan := "classic"
var _rules := {}
var _residents: Array = []  # drafts from the resident creator
var _busy := false
var _repo_http := HTTPRequest.new()
var _branch_http := HTTPRequest.new()
var _found_http := HTTPRequest.new()


func _ready() -> void:
	for http in [_repo_http, _branch_http, _found_http]:
		add_child(http)
	_repo_http.request_completed.connect(_on_repos)
	_branch_http.request_completed.connect(_on_branches)
	_found_http.request_completed.connect(_on_founded)

	%Close.pressed.connect(closed.emit)
	%Cancel.pressed.connect(func(): closed.emit() if _step == 1 else _go_to(1))
	%Next.pressed.connect(func(): _go_to(2) if _step == 1 else _found())
	%Shuffle.pressed.connect(func():
		%NameEdit.text = NAMES.pick_random()
		_refresh())
	%NameEdit.text_changed.connect(func(_t): _refresh())
	%SearchEdit.text_changed.connect(func(_t): _show_repos())
	%BranchEdit.text_changed.connect(func(text: String): _branch = text.strip_edges())
	_build_looks()
	_build_plans()
	_build_rules()


## Start fresh (called each time the creator opens).
func open() -> void:
	_step = 1
	_repo = ""
	_branch = "main"
	_plan = "classic"
	_residents = []
	_busy = false
	%NameEdit.text = ""
	%SearchEdit.text = ""
	for r in RULES:
		_rules[r[0]] = r[2]
	_build_rules()
	_style_plans()
	_show_branches([])
	_go_to(1)
	%GhStatus.text = "Asking GitHub…"
	_repo_http.cancel_request()
	_repo_http.request(Net.http_url("/github/repos"))
	%NameEdit.grab_focus()


## A resident from the resident creator: add it (index -1) or replace one.
func put_resident(resident: Dictionary, index: int) -> void:
	if index >= 0 and index < _residents.size():
		_residents[index] = resident
	else:
		_residents.append(resident)
	_show_residents()


func island_name() -> String:
	var typed: String = %NameEdit.text.strip_edges()
	return typed if typed else "your island"


# --- Steps -------------------------------------------------------------------

func _go_to(step: int) -> void:
	_step = step
	%Step1.visible = step == 1
	%Step2.visible = step == 2
	%Title.text = "Found a new island" if step == 1 else "Plan %s" % island_name()
	%Cancel.text = "Cancel" if step == 1 else "Back"
	_show_steps()
	_show_residents()
	_refresh()


func _refresh() -> void:
	var named: bool = %NameEdit.text.strip_edges() != ""
	%PlateName.text = island_name().capitalize() if not named else %NameEdit.text.strip_edges()
	%Preview.show_islands([{"id": "new", "types": PLANS[_plan][2]}])
	%PreviewText.text = "Preview · %s" % PLANS[_plan][0].to_lower()
	%Next.disabled = not named or _busy
	if _step == 1:
		%Next.text = "Next: town plan"
		_set_note("Residents only read and push to the repo you link here." if _repo
			else "No repo yet? You can found the island and link one later.")
	else:
		var short := island_name() if island_name().length() <= 14 else "island"
		%Next.text = "Founding…" if _busy else "Found %s" % short
		_set_note("Each resident brings their own keys. Fine-tune them anytime from Residents.")


func _set_note(text: String, error := false) -> void:
	%Note.text = text
	if error:
		%Note.add_theme_color_override("font_color", Color("C0392B"))
	else:
		%Note.remove_theme_color_override("font_color")


func _show_steps() -> void:
	for child in %Steps.get_children():
		child.queue_free()
	for i in 2:
		var pill := PanelContainer.new()
		pill.theme_type_variation = &"TagPill"
		var style: StyleBoxFlat = get_theme_stylebox("panel", "TagPill").duplicate()
		style.bg_color = Color("FFEC01") if _step == i + 1 else Color.WHITE
		pill.add_theme_stylebox_override("panel", style)
		var label := Label.new()
		label.theme_type_variation = &"TagText"
		label.text = ("✓ " if _step > i + 1 else "%d · " % (i + 1)) + ["Name & repo", "Town & residents"][i]
		pill.add_child(label)
		%Steps.add_child(pill)


func _process(_delta: float) -> void:
	# Keep the name plate under the preview island.
	if %Step1.visible:
		var shore: Vector2 = %Preview.screen_point("new", Vector3(0, 0, SeaView.ISLAND_HALF.y - 1.0))
		%NamePlate.position = (shore - Vector2(%NamePlate.size.x / 2.0, 0)).round()


# --- Step 1: repos and branches --------------------------------------------------

func _on_repos(result: int, code: int, _headers, body: PackedByteArray) -> void:
	var data = JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and typeof(data) == TYPE_DICTIONARY:
		_repos = data.get("repos", [])
		%GhStatus.text = "From your GitHub (gh) · %d repos" % _repos.size()
	else:
		_repos = []
		var why: String = data.get("detail", "") if typeof(data) == TYPE_DICTIONARY else "backend offline"
		%GhStatus.text = "Couldn't list repos (%s). Type owner/name." % why
	_show_repos()


func _show_repos() -> void:
	for child in %Repos.get_children():
		child.queue_free()
	var query: String = %SearchEdit.text.strip_edges()
	var shown := _repos.filter(func(r): return query.to_lower() in str(r["repo"]).to_lower())
	# Typed a full owner/name that isn't listed? Offer it as is.
	var typed_ok := RegEx.create_from_string(REPO_PATTERN).search(query) != null
	if typed_ok and not shown.any(func(r): return r["repo"] == query):
		shown.push_front({"repo": query, "typed": true})
	if _repo and not shown.any(func(r): return r["repo"] == _repo):
		shown.push_front({"repo": _repo, "typed": true})  # keep the chosen one in sight
	for r in shown.slice(0, 30):
		%Repos.add_child(_repo_row(r))
	if shown.is_empty():
		var empty := _label("No matching repos." if _repos else "Type a repo as owner/name.", 13, false)
		%Repos.add_child(empty)


func _repo_row(r: Dictionary) -> Control:
	var repo := str(r["repo"])
	var row := Button.new()
	row.theme_type_variation = &"IslandRowHere" if repo == _repo else &"IslandRow"
	row.custom_minimum_size.y = 40
	row.focus_mode = Control.FOCUS_NONE
	row.pressed.connect(func():
		_repo = "" if _repo == repo else repo  # click again to unlink
		_show_repos()
		_show_branches([])
		if _repo:
			_branch_http.cancel_request()
			_branch_http.request(Net.http_url("/github/branches?repo=%s" % _repo.uri_encode()))
		_refresh())
	var line := HBoxContainer.new()
	line.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	line.offset_left = 12
	line.offset_right = -12
	line.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var name_label := _label(repo, 14, true)
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var detail := "typed in" if r.get("typed") else "%s · updated %s" % [
		"Private" if r.get("private") else "Public", _ago(str(r.get("updated_at", "")))]
	line.add_child(name_label)
	line.add_child(_label(detail, 11, false))
	if repo == _repo:
		var check := TextureRect.new()
		check.texture = CHECK
		check.custom_minimum_size = Vector2(14, 14)
		check.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		check.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		check.modulate = Color("7553DF")
		line.add_child(check)
	for part in line.get_children():
		part.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		part.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(line)
	return row


func _on_branches(_result: int, code: int, _headers, body: PackedByteArray) -> void:
	var data = JSON.parse_string(body.get_string_from_utf8()) if code == 200 else null
	_show_branches(data.get("branches", []) if typeof(data) == TYPE_DICTIONARY else [])


## Branch chips: main first, a few others, then "Other…" to type one.
func _show_branches(names: Array) -> void:
	for child in %Branches.get_children():
		child.queue_free()
	var chips: Array = ["main"] if names.is_empty() or "main" in names else []
	chips.append_array(names.filter(func(n): return n != "main").slice(0, 3))
	if names and "main" not in names:
		_branch = str(names[0])
	%BranchEdit.visible = _branch not in chips and _branch != ""
	for b in chips + ["Other…"]:
		var chip := Button.new()
		chip.text = b
		chip.focus_mode = Control.FOCUS_NONE
		var on: bool = (b == _branch) or (b == "Other…" and %BranchEdit.visible)
		chip.theme_type_variation = &"FilterChipActive" if on else &"FilterChip"
		chip.pressed.connect(func():
			if b == "Other…":
				%BranchEdit.visible = true
				%BranchEdit.text = ""
				_branch = ""
				%BranchEdit.grab_focus()
			else:
				_branch = b
				%BranchEdit.visible = false
			_show_branches(names))
		%Branches.add_child(chip)


# --- Step 2: plan, residents, rules -------------------------------------------

func _build_plans() -> void:
	var group := ButtonGroup.new()
	for id in PLANS:
		var card := Button.new()
		card.theme_type_variation = &"PlanCard"
		card.toggle_mode = true
		card.button_group = group
		card.focus_mode = Control.FOCUS_NONE
		card.custom_minimum_size = Vector2(0, 92)
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		card.set_meta("plan", id)
		card.toggled.connect(func(on: bool):
			if on:
				_plan = id
				_refresh())
		var rows := VBoxContainer.new()
		rows.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		rows.offset_left = 16
		rows.offset_top = 12
		rows.offset_right = -16
		rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var top := HBoxContainer.new()
		top.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var title := _label(PLANS[id][0], 17, true)
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		top.add_child(title)
		if id == "classic":
			var pill := PanelContainer.new()
			pill.theme_type_variation = &"TagPill"
			var style: StyleBoxFlat = get_theme_stylebox("panel", "TagPill").duplicate()
			style.bg_color = Color("7553DF")
			pill.add_theme_stylebox_override("panel", style)
			var text := _label("Recommended", 11, true)
			text.add_theme_color_override("font_color", Color.WHITE)
			pill.add_child(text)
			top.add_child(pill)
		var description := _label(PLANS[id][1], 12, false)
		description.add_theme_color_override("font_color", Color("292E50"))
		rows.add_child(top)
		rows.add_child(description)
		card.add_child(rows)
		%Plans.add_child(card)


func _style_plans() -> void:
	for card in %Plans.get_children():
		card.set_pressed_no_signal(card.get_meta("plan") == _plan)


func _show_residents() -> void:
	for child in %Residents.get_children():
		child.queue_free()
	for i in _residents.size():
		%Residents.add_child(_resident_tile(_residents[i], i))
	var add := Button.new()
	add.set_script(DashedButton)
	add.theme_type_variation = &"FlatIconButton"
	add.custom_minimum_size = Vector2(170, 140)
	add.tooltip_text = "Make a resident for %s" % island_name()
	add.pressed.connect(func(): resident_requested.emit({}, -1))
	var content := VBoxContainer.new()
	content.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	content.alignment = BoxContainer.ALIGNMENT_CENTER
	content.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var plus := _label("+", 26, true)
	plus.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	var text := _label("Make your own", 14, true)
	text.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	content.add_child(plus)
	content.add_child(text)
	add.add_child(content)
	%Residents.add_child(add)
	var n := _residents.size()
	%ResNote.text = ("%d moving in · you can invite more later" % n) if n else "Nobody yet · you can invite more later"


func _resident_tile(resident: Dictionary, index: int) -> Control:
	var color := Color(str(resident.get("color", "#9B87F5")))
	var tile := Button.new()
	tile.theme_type_variation = &"PlanCard"
	tile.custom_minimum_size = Vector2(170, 140)
	tile.focus_mode = Control.FOCUS_NONE
	tile.tooltip_text = "Edit %s" % resident.get("name", "")
	tile.pressed.connect(func(): resident_requested.emit(resident, index))
	var rows := VBoxContainer.new()
	rows.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	rows.alignment = BoxContainer.ALIGNMENT_CENTER
	rows.add_theme_constant_override("separation", 4)
	rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var avatar := PanelContainer.new()
	avatar.custom_minimum_size = Vector2(44, 44)
	avatar.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	avatar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var circle: StyleBoxFlat = get_theme_stylebox("panel", "AvatarCircle").duplicate()
	circle.bg_color = color.lerp(Color.WHITE, 0.7)
	avatar.add_theme_stylebox_override("panel", circle)
	var initial := _label(str(resident.get("name", "?")).left(1), 22, true)
	initial.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	initial.add_theme_color_override("font_color", color.darkened(0.2))
	avatar.add_child(initial)
	var name_label := _label(str(resident.get("name", "")), 16, true)
	name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	var job := _label(str(resident.get("job", "")).capitalize(), 12, true)
	job.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	job.add_theme_color_override("font_color", color.darkened(0.2))
	rows.add_child(avatar)
	rows.add_child(name_label)
	rows.add_child(job)
	tile.add_child(rows)

	var remove := Button.new()
	remove.theme_type_variation = &"FlatIconButton"
	remove.icon = preload("res://assets/icons/x.svg")
	remove.tooltip_text = "Not moving in after all"
	remove.focus_mode = Control.FOCUS_NONE
	remove.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	remove.offset_left = -30
	remove.offset_top = 6
	remove.offset_right = -6
	remove.pressed.connect(func():
		_residents.remove_at(index)
		_show_residents())
	tile.add_child(remove)
	return tile


func _build_rules() -> void:
	for child in %Rules.get_children():
		child.queue_free()
	for r in RULES:
		var box := PanelContainer.new()
		box.theme_type_variation = &"StatCard"
		box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var toggle := CheckButton.new()
		toggle.text = r[1]
		toggle.focus_mode = Control.FOCUS_NONE
		toggle.theme_type_variation = &"FlatIconButton"
		toggle.add_theme_icon_override("checked", TOGGLE_ON)
		toggle.add_theme_icon_override("unchecked", TOGGLE_OFF)
		toggle.add_theme_constant_override("icon_max_width", 44)
		for state in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color"]:
			toggle.add_theme_color_override(state, Color("292E50"))
		toggle.button_pressed = _rules.get(r[0], r[2])
		toggle.toggled.connect(func(on: bool): _rules[r[0]] = on)
		box.add_child(toggle)
		%Rules.add_child(box)


## Island look: shown, but only Spring for now.
func _build_looks() -> void:
	for look in LOOKS:
		var tile := Button.new()
		tile.theme_type_variation = &"GiftTile"
		tile.toggle_mode = true
		tile.focus_mode = Control.FOCUS_NONE
		tile.custom_minimum_size = Vector2(82, 70)
		tile.button_pressed = look[0] == "Spring"
		tile.disabled = look[0] != "Spring"
		tile.tooltip_text = "Island looks are coming soon"
		var rows := VBoxContainer.new()
		rows.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		rows.alignment = BoxContainer.ALIGNMENT_CENTER
		rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var swatch := Panel.new()
		swatch.custom_minimum_size = Vector2(40, 18)
		swatch.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		swatch.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var style := StyleBoxFlat.new()
		style.bg_color = Color(look[1])
		style.set_corner_radius_all(9)
		swatch.add_theme_stylebox_override("panel", style)
		var name_label := _label(look[0], 12, true)
		name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		if tile.disabled:
			swatch.modulate = Color(1, 1, 1, 0.5)
			name_label.modulate = Color(1, 1, 1, 0.5)
		rows.add_child(swatch)
		rows.add_child(name_label)
		tile.add_child(rows)
		%Looks.add_child(tile)


# --- Founding -------------------------------------------------------------------

func _found() -> void:
	if _busy:
		return
	_busy = true
	_refresh()
	var body := {"name": %NameEdit.text.strip_edges(), "repo": _repo if _repo else null,
		"branch": _branch if _branch else "main", "plan": _plan, "rules": _rules}
	var error := _found_http.request(Net.http_url("/islands"), ["Content-Type: application/json"],
		HTTPClient.METHOD_POST, JSON.stringify(body))
	if error != OK:
		_busy = false
		_refresh()
		_set_note("Couldn't reach the backend.", true)


func _on_founded(result: int, code: int, _headers, body: PackedByteArray) -> void:
	_busy = false
	var data = JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and typeof(data) == TYPE_DICTIONARY:
		founded.emit(data, _residents.duplicate(true))
		return
	_refresh()
	var why: String = str(data.get("detail", "")) if typeof(data) == TYPE_DICTIONARY else "the backend is offline"
	_set_note("Hmm, that didn't work: %s" % why, true)


# --- Little helpers ---------------------------------------------------------------

func _ago(iso: String) -> String:
	if iso.length() < 19:
		return "a while ago"
	var hours := (Time.get_unix_time_from_system() - Time.get_unix_time_from_datetime_string(iso.left(19))) / 3600.0
	if hours < 1:
		return "just now"
	if hours < 48:
		return "%dh ago" % int(hours)
	return "%dd ago" % int(hours / 24.0)


func _label(text: String, size: int, strong: bool) -> Label:
	var label := Label.new()
	label.text = text
	label.theme_type_variation = &"LabelStrong" if strong else &"LabelMuted"
	label.add_theme_font_size_override("font_size", size)
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return label
