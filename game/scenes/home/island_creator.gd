class_name IslandCreator
extends Control
## Found a new island, in two steps:
##   1. Name & repo: a name, and where the code comes from (REPO_MODES): a new
##      empty repository, a GitHub repo to clone (listed by the backend through
##      the `gh` CLI, or typed as owner/name) and the branch residents start
##      from, or a git folder already on this computer. Also the island's shape
##      and season. A live 3D preview shows the island.
##   2. Town & residents: which buildings (workflow steps) the island has
##      (the town plans are quick picks), the residents you make for it, and
##      house rules.
## "Found" sends POST /islands, then game.gd visits the new island and moves
## the residents in (create_resident, one by one, once its snapshot arrives).
## Everything about the layout can change later in build mode.

signal closed
signal founded(island: Dictionary, residents: Array)
## "Make your own" / editing a resident: game.gd opens the resident creator and
## hands the result back with put_resident().
signal resident_requested(resident: Dictionary, index: int)

const NAMES := [
	"Fern Harbor",
	"Maple Hollow",
	"Driftwood Isle",
	"Sunny Shoals",
	"Pebble Cove",
	"Coral Key",
	"Willow Reach",
	"Mossy Point",
	"Tidepool Isle",
	"Lantern Bay"
]
## plan id -> [title, description, building types]: quick picks of steps.
## Must match PLANS in backend/mii/islands.py.
const PLANS := {
	"classic":
	[
		"Classic town",
		"Mailbox · Workshop · Lab · Courthouse · Post Office",
		["mailbox", "workshop", "lab", "courthouse", "post_office"]
	],
	"quick_fixes":
	[
		"Quick fixes",
		"Mailbox · Workshop · Post Office\nSmall chores, no tests or review stop.",
		["mailbox", "workshop", "post_office"]
	],
}
## [rule, label, ask by default]. Must match RULES in backend/mii/islands.py.
const RULES := [
	["merge_pr", "Merging a PR", true],
	["install_packages", "Installing packages", true],
	["shell", "Running shell commands", false]
]
## [season, swatch color]: the island's season (changeable later in build mode).
const LOOKS := [["spring", "F4A6C8"], ["summer", "9BD77A"], ["autumn", "E0A85C"], ["winter", "DDE6F2"]]
const TOGGLE_ON := preload("res://assets/ui/toggle_on.svg")
const TOGGLE_OFF := preload("res://assets/ui/toggle_off.svg")
const CHECK := preload("res://assets/icons/check.svg")
const REPO_PATTERN := "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
## [mode, chip label]: where the island's code comes from (POST /islands sends
## nothing, "repo", or "existing_repo").
const REPO_MODES := [
	["new", "New empty repository"], ["github", "Clone from GitHub"], ["folder", "Use a folder on this computer"]
]

var _step := 1
var _repos: Array = []  # [{"repo", "private", "updated_at"}] from the backend
var _repo_mode := "new"  # a REPO_MODES id
var _repo := ""  # chosen "owner/name" ("" = none)
var _mode_row: HFlowContainer  # the REPO_MODES chips (built in code)
var _folder_edit: LineEdit  # the path, for "folder" mode
var _mode_hint: Label  # one line about the chosen mode
var _branch := "main"
var _plan := "classic"  # the plan card that matches _steps ("" = a custom mix)
var _steps: Array = IslandLayout.STEP_ORDER.duplicate()
var _shape := "classic"
var _season := "summer"
var _step_toggles := {}  # building type -> Button
var _rules := {}
var _residents: Array = []  # drafts from the resident creator
var _busy := false
var _repo_request := 0  # only the latest GET /github/repos reply counts
var _branch_request := 0  # same, for GET /github/branches


func _ready() -> void:
	%Close.pressed.connect(closed.emit)
	%Cancel.pressed.connect(
		func():
			if _step == 1:
				closed.emit()
			else:
				_go_to(1)
	)
	%Next.pressed.connect(
		func():
			if _step == 1:
				_go_to(2)
			else:
				_found()
	)
	%Shuffle.pressed.connect(
		func():
			%NameEdit.text = NAMES.pick_random()
			_refresh()
	)
	%NameEdit.text_changed.connect(func(_t): _refresh())
	%SearchEdit.text_changed.connect(func(_t): _show_repos())
	%BranchEdit.text_changed.connect(func(text: String): _branch = text.strip_edges())
	_build_repo_modes()
	_build_shapes()
	_build_looks()
	_build_plans()
	_build_step_toggles()
	_build_rules()


## Start fresh (called each time the creator opens).
func open() -> void:
	_step = 1
	_repo_mode = "new"
	_repo = ""
	_folder_edit.text = ""
	_branch = "main"
	_plan = "classic"
	_steps = IslandLayout.STEP_ORDER.duplicate()
	_shape = "classic"
	_season = "summer"
	_residents = []
	_busy = false
	%NameEdit.text = ""
	%SearchEdit.text = ""
	for r in RULES:
		_rules[r[0]] = r[2]
	_build_rules()
	_style_plans()
	_style_choices()
	_show_branches([])
	_show_repo_mode()
	_go_to(1)
	%GhStatus.text = "Asking GitHub…"
	_repo_request += 1
	Net.request_json("/github/repos", _on_repos.bind(_repo_request))
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
	var layout := IslandLayout.from_shape(_shape, _steps, "new")
	layout.season = _season
	%Preview.show_islands([{"id": "new", "layout": layout}])
	%PreviewText.text = "Preview · %s · %s" % [IslandLayout.SHAPES[_shape]["title"].to_lower(), _season]
	%Next.disabled = not named or _busy or not _code_chosen()
	if _step == 1:
		%Next.text = "Next: town plan"
		match _repo_mode:
			"github":
				_set_note(
					(
						"Residents work on a copy of %s and send their work back as pull requests." % _repo
						if _repo
						else "Pick the repo to clone."
					)
				)
			"folder":
				_set_note(
					(
						"Residents work in their own git worktrees; your checkout stays as it is."
						if _folder_path()
						else "Paste the path of a git folder."
					)
				)
			_:
				_set_note("A fresh repository with a README. Clone or pick a folder to work on code you have.")
	else:
		var short := island_name() if island_name().length() <= 14 else "island"
		%Next.text = "Founding…" if _busy else "Found %s" % short
		if _busy and _repo_mode == "github":
			_set_note("Cloning %s… big repositories take a minute." % _repo)
		else:
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


# --- Step 1: where the code comes from -------------------------------------------


## The REPO_MODES chips, a path field for "folder" mode, and a hint line, built
## in code just under the name row. An HFlowContainer is like an HBoxContainer
## that wraps its children onto a new line when they don't fit.
func _build_repo_modes() -> void:
	var right: Control = %SearchEdit.get_parent()
	var at: int = right.get_node("RepoHeader").get_index()
	var label := Label.new()
	label.theme_type_variation = &"FieldLabel"
	label.text = "Code"
	_mode_row = HFlowContainer.new()
	_mode_row.add_theme_constant_override("h_separation", 4)
	_mode_row.add_theme_constant_override("v_separation", 4)
	_folder_edit = LineEdit.new()
	_folder_edit.placeholder_text = "/path/to/your/project (a git folder)"
	_folder_edit.clear_button_enabled = true
	_folder_edit.text_changed.connect(func(_t): _refresh())
	_mode_hint = UI.label("", 12, false)
	_mode_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	var nodes: Array[Control] = [label, _mode_row, _folder_edit, _mode_hint]
	for i in nodes.size():
		right.add_child(nodes[i])
		right.move_child(nodes[i], at + i)


## Show the chips, and only the fields the chosen mode needs.
func _show_repo_mode() -> void:
	for child in _mode_row.get_children():
		child.queue_free()
	for mode in REPO_MODES:
		var chip := Button.new()
		chip.text = mode[1]
		chip.focus_mode = Control.FOCUS_NONE
		chip.theme_type_variation = &"FilterChipActive" if mode[0] == _repo_mode else &"FilterChip"
		chip.pressed.connect(
			func():
				_repo_mode = mode[0]
				_show_repo_mode()
				_refresh()
		)
		_mode_row.add_child(chip)
	var github := _repo_mode == "github"
	for node in ["RepoHeader", "SearchEdit", "RepoScroll", "BranchLabel", "BranchRow"]:
		%SearchEdit.get_parent().get_node(node).visible = github
	_folder_edit.visible = _repo_mode == "folder"
	match _repo_mode:
		"new":
			_mode_hint.text = "Starts from scratch on branch main, in your islands folder (see Settings)."
		"github":
			_mode_hint.text = "Cloned with your gh login. Residents open pull requests against the branch below."
		"folder":
			_mode_hint.text = (
				"Residents branch off whatever is checked out there. If it pushes to GitHub, "
				+ "they open pull requests too."
			)
	if _repo_mode == "folder":
		_folder_edit.grab_focus.call_deferred()


## Ready for Next: a repo picked (GitHub) or a path typed (folder).
func _code_chosen() -> bool:
	match _repo_mode:
		"github":
			return _repo != ""
		"folder":
			return _folder_path() != ""
	return true


func _folder_path() -> String:
	return _folder_edit.text.strip_edges() if _folder_edit else ""


# --- Step 1: repos and branches --------------------------------------------------


func _on_repos(ok: bool, data: Dictionary, asked: int) -> void:
	if asked != _repo_request:
		return
	if ok:
		_repos = data.get("repos", [])
		%GhStatus.text = "From your GitHub (gh) · %d repos" % _repos.size()
	else:
		_repos = []
		%GhStatus.text = "Couldn't list repos (%s). Type owner/name." % data["error"]
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
		var empty := UI.label("No matching repos." if _repos else "Type a repo as owner/name.", 13, false)
		%Repos.add_child(empty)


func _repo_row(r: Dictionary) -> Control:
	var repo := str(r["repo"])
	var row := Button.new()
	row.theme_type_variation = &"IslandRowHere" if repo == _repo else &"IslandRow"
	row.custom_minimum_size.y = 40
	row.focus_mode = Control.FOCUS_NONE
	row.pressed.connect(
		func():
			_repo = "" if _repo == repo else repo  # click again to unlink
			_show_repos()
			_show_branches([])
			if _repo:
				_branch_request += 1
				var path := "/github/branches?repo=%s" % _repo.uri_encode()
				Net.request_json(path, _on_branches.bind(_branch_request))
			_refresh()
	)
	var line := HBoxContainer.new()
	line.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	line.offset_left = 12
	line.offset_right = -12
	line.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var name_label := UI.label(repo, 14, true)
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var detail := (
		"typed in"
		if r.get("typed")
		else "%s · updated %s" % ["Private" if r.get("private") else "Public", _ago(str(r.get("updated_at", "")))]
	)
	line.add_child(name_label)
	line.add_child(UI.label(detail, 11, false))
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


func _on_branches(ok: bool, data: Dictionary, asked: int) -> void:
	if asked == _branch_request:
		_show_branches(data.get("branches", []) if ok else [])


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
		chip.pressed.connect(
			func():
				if b == "Other…":
					%BranchEdit.visible = true
					%BranchEdit.text = ""
					_branch = ""
					%BranchEdit.grab_focus()
				else:
					_branch = b
					%BranchEdit.visible = false
				_show_branches(names)
		)
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
		card.toggled.connect(
			func(on: bool):
				if on:
					_plan = id
					_steps = PLANS[id][2].duplicate()
					_style_choices()
					_refresh()
		)
		var rows := VBoxContainer.new()
		rows.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		rows.offset_left = 16
		rows.offset_top = 12
		rows.offset_right = -16
		rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var top := HBoxContainer.new()
		top.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var title := UI.label(PLANS[id][0], 17, true)
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		top.add_child(title)
		if id == "classic":
			var pill := PanelContainer.new()
			pill.theme_type_variation = &"TagPill"
			var style: StyleBoxFlat = get_theme_stylebox("panel", "TagPill").duplicate()
			style.bg_color = Color("7553DF")
			pill.add_theme_stylebox_override("panel", style)
			var text := UI.label("Recommended", 11, true)
			text.add_theme_color_override("font_color", Color.WHITE)
			pill.add_child(text)
			top.add_child(pill)
		var description := UI.label(PLANS[id][1], 12, false)
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
	var plus := UI.label("+", 26, true)
	plus.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	var text := UI.label("Make your own", 14, true)
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
	var initial := UI.label(str(resident.get("name", "?")).left(1), 22, true)
	initial.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	initial.add_theme_color_override("font_color", color.darkened(0.2))
	avatar.add_child(initial)
	var name_label := UI.label(str(resident.get("name", "")), 16, true)
	name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	var job := UI.label(str(resident.get("job", "")).capitalize(), 12, true)
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
	remove.pressed.connect(
		func():
			_residents.remove_at(index)
			_show_residents()
	)
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


## The island's season: a tile per season with a color swatch.
func _build_looks() -> void:
	var group := ButtonGroup.new()
	for look in LOOKS:
		var tile := _choice_tile(look[0].capitalize(), group, func(): _season = look[0])
		tile.set_meta("season", look[0])
		var swatch := Panel.new()
		swatch.custom_minimum_size = Vector2(40, 18)
		swatch.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		swatch.mouse_filter = Control.MOUSE_FILTER_IGNORE
		var style := StyleBoxFlat.new()
		style.bg_color = Color(look[1])
		style.set_corner_radius_all(9)
		swatch.add_theme_stylebox_override("panel", style)
		tile.get_child(0).add_child(swatch)
		tile.get_child(0).move_child(swatch, 0)
		%Looks.add_child(tile)
	%Looks.get_parent().get_node("LookLabel").text = "Season"


## The island's shape: a tile per shape (IslandLayout.SHAPES), with a little map.
## The row goes just above the season row.
func _build_shapes() -> void:
	var left: Control = %Looks.get_parent()
	var label := Label.new()
	label.theme_type_variation = &"FieldLabel"
	label.text = "Island shape"
	var row := HBoxContainer.new()
	row.name = "Shapes"
	row.add_theme_constant_override("separation", 8)
	var at := left.get_node("LookLabel").get_index()
	left.add_child(label)
	left.move_child(label, at)
	left.add_child(row)
	left.move_child(row, at + 1)
	var group := ButtonGroup.new()
	for id in IslandLayout.SHAPES:
		var tile := _choice_tile(IslandLayout.SHAPES[id]["title"], group, func(): _shape = id)
		tile.set_meta("shape", id)
		var map := TextureRect.new()
		map.texture = _shape_map(id)
		map.custom_minimum_size = Vector2(46, 32)
		map.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		map.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		map.mouse_filter = Control.MOUSE_FILTER_IGNORE
		tile.get_child(0).add_child(map)
		tile.get_child(0).move_child(map, 0)
		row.add_child(tile)


## A tiny picture of a shape: green land on clear sea, one pixel per tile.
func _shape_map(id: String) -> ImageTexture:
	var land: Array = IslandLayout.SHAPES[id]["land"]
	var image := Image.create(IslandLayout.GRID_W, IslandLayout.GRID_H, false, Image.FORMAT_RGBA8)
	for r in IslandLayout.GRID_H:
		for c in IslandLayout.GRID_W:
			image.set_pixel(c, r, Color("8CCB5E") if land[r][c] == "#" else Color(0, 0, 0, 0))
	return ImageTexture.create_from_image(image)


## A toggle tile (shape or season) with its name; `on_pick` runs when it's chosen.
func _choice_tile(title: String, group: ButtonGroup, on_pick: Callable) -> Button:
	var tile := Button.new()
	tile.theme_type_variation = &"GiftTile"
	tile.toggle_mode = true
	tile.button_group = group
	tile.focus_mode = Control.FOCUS_NONE
	tile.custom_minimum_size = Vector2(76, 70)
	tile.toggled.connect(
		func(on: bool):
			if on:
				on_pick.call()
				_refresh()
	)
	var rows := VBoxContainer.new()
	rows.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	rows.alignment = BoxContainer.ALIGNMENT_CENTER
	rows.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var name_label := UI.label(title, 12, true)
	name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	rows.add_child(name_label)
	tile.add_child(rows)
	return tile


## One toggle per workflow step, under the plan cards. Mailbox, Workshop, and
## Post Office are always needed, so they stay on.
func _build_step_toggles() -> void:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var step2: Control = %Plans.get_parent()
	step2.add_child(row)
	step2.move_child(row, %Plans.get_index() + 1)
	row.add_child(UI.label("Buildings:", 13, true))
	for type in IslandLayout.STEP_ORDER:
		var button := Button.new()
		button.text = IslandLayout.STEP_NAMES[type]
		button.toggle_mode = true
		button.focus_mode = Control.FOCUS_NONE
		# Required steps stay on (clicking them does nothing); they keep the
		# normal "on" look instead of the faded disabled one.
		if type in IslandLayout.REQUIRED_STEPS:
			button.tooltip_text = "Every island needs this one."
		else:
			button.tooltip_text = "A check step: work that fails goes back to the Workshop."
		button.toggled.connect(func(on: bool): _toggle_step(type, on))
		_step_toggles[type] = button
		row.add_child(button)


func _toggle_step(type: String, on: bool) -> void:
	if type in IslandLayout.REQUIRED_STEPS:
		_style_choices()  # stays on
		return
	if on and type not in _steps:
		_steps.append(type)
	elif not on:
		_steps.erase(type)
	_steps = IslandLayout.STEP_ORDER.filter(func(t): return t in _steps)
	_plan = ""
	for id in PLANS:
		if PLANS[id][2] == _steps:
			_plan = id
	_style_plans()
	_style_choices()
	_refresh()


## Show the current shape, season, and steps on their tiles and toggles.
func _style_choices() -> void:
	for tile in %Looks.get_children():
		tile.set_pressed_no_signal(tile.get_meta("season") == _season)
	for tile in %Looks.get_parent().get_node("Shapes").get_children():
		tile.set_pressed_no_signal(tile.get_meta("shape") == _shape)
	for type in _step_toggles:
		var button: Button = _step_toggles[type]
		button.set_pressed_no_signal(type in _steps)
		button.theme_type_variation = &"TabActive" if type in _steps else &"TabInactive"


# --- Founding -------------------------------------------------------------------


func _found() -> void:
	if _busy:
		return
	_busy = true
	_refresh()
	var body := {
		"name": %NameEdit.text.strip_edges(), "shape": _shape, "steps": _steps, "season": _season, "rules": _rules
	}
	match _repo_mode:
		"github":  # the backend clones it (can take a while; Net.request_json has no timeout)
			body["repo"] = _repo
			body["branch"] = _branch if _branch else null
		"folder":  # adopt it; the backend reads owner/name from its origin and uses its branch
			body["existing_repo"] = _folder_path()
	Net.request_json("/islands", _on_founded, HTTPClient.METHOD_POST, body)


func _on_founded(ok: bool, data: Dictionary) -> void:
	_busy = false
	if ok:
		founded.emit(data, _residents.duplicate(true))
		return
	var why := str(data["error"])
	# Repo problems (gh not logged in, a bad path, a missing branch) are fixed on step 1.
	if _repo_mode != "new":
		_go_to(1)
	else:
		_refresh()
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
