extends Control
## The main screen (UI mockup): top bar, the 3D island in a rounded frame, the
## bottom nav, and the Bubbles sidebar.
##
## Layout lives in game.tscn (containers, so you can rearrange it in the
## editor) and every color/style lives in ui/theme.tres. This script only wires
## up the few live parts: building labels, zoom, and the connection pill.
##
## Godot concept: the island is rendered inside a SubViewport, a "screen inside
## the screen". That's what lets the 3D world sit in a rounded frame next to
## normal 2D UI instead of filling the whole window.
##
## TODO(mvp): Build / Residents / News / Insights screens, the back button, and the island picker.
## TODO(mvp): fill the sidebar and the "3 need you" count from Net bubbles; make the tabs filter them.
## TODO(mvp): real day/time, weather, resident count, and island mood from the backend.

const BuildingLabelScene := preload("res://scenes/ui/building_label.tscn")

## Building node (in island_3d.tscn), label text, icon, color, mock task count.
const BUILDINGS := [
	["Mailbox", "Mailbox", preload("res://assets/icons/mail.svg"), Color("F5708F"), 4],
	["Workshop", "Workshop", preload("res://assets/icons/code.svg"), Color("E39537"), 1],
	["Lab", "Lab", preload("res://assets/icons/flask-conical.svg"), Color("00B8A0"), 1],
	["PostOffice", "Post Office", preload("res://assets/icons/git-pull-request.svg"), Color("D9A321"), 2],
	["Courthouse", "Courthouse", preload("res://assets/icons/scale.svg"), Color("7553DF"), 1],
]

@onready var island: Node3D = %Island3D
@onready var rig: CameraRig = %Island3D.get_node("CameraRig")
@onready var labels_root: Control = %Labels


func _ready() -> void:
	for info in BUILDINGS:
		var label: BuildingLabel = BuildingLabelScene.instantiate()
		labels_root.add_child(label)
		label.setup(island.get_node("Buildings/" + info[0]), rig.camera, info[1], info[2], info[3], info[4])

	%ZoomIn.pressed.connect(func(): rig.zoom_by(CameraRig.ZOOM_STEP))
	%ZoomOut.pressed.connect(func(): rig.zoom_by(-CameraRig.ZOOM_STEP))
	rig.zoom_changed.connect(func(percent: int): %ZoomLabel.text = "%d%%" % percent)

	# The connection pill is real: it follows the Net autoload.
	Net.connected.connect(_show_connection.bind(true))
	Net.disconnected.connect(_show_connection.bind(false))
	_show_connection(Net.is_online)


func _show_connection(online: bool) -> void:
	%StatusLabel.text = "Connected" if online else "Reconnecting…"
	%SyncedLabel.text = "synced just now" if online else "backend offline"
	var dot: StyleBoxFlat = get_theme_stylebox("panel", "StatusDot").duplicate()
	dot.bg_color = Color("39C16F") if online else Color("F2994A")
	%StatusDot.add_theme_stylebox_override("panel", dot)
