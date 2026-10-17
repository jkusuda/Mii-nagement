class_name BuildingData
## What the game knows about each building type: label, icon, color, a tagline
## for its panel, and how a package at that stage is described.
##
## Types match the backend (docs/protocol.md). The 3D node in island_3d.tscn
## is the type in PascalCase ("post_office" -> PostOffice).
##
## Like ResidentData: only constants and static functions, reachable by name.

## type -> details. `stage`: the status pill of a package there; `doing`:
## "Mo is testing at the Lab".
const TYPES := {
	"mailbox": {
		"name": "Mailbox", "icon": preload("res://assets/icons/mail.svg"), "color": Color("F5708F"),
		"tagline": "Little issues. Big things for your residents to do.", "stage": "Planning", "doing": "planning",
	},
	"workshop": {
		"name": "Workshop", "icon": preload("res://assets/icons/code.svg"), "color": Color("E39537"),
		"tagline": "Where residents write the code.", "stage": "Coding", "doing": "coding",
	},
	"lab": {
		"name": "Lab", "icon": preload("res://assets/icons/flask-conical.svg"), "color": Color("00B8A0"),
		"tagline": "Every package gets its tests run here.", "stage": "Testing", "doing": "testing",
	},
	"courthouse": {
		"name": "Courthouse", "icon": preload("res://assets/icons/scale.svg"), "color": Color("7553DF"),
		"tagline": "A second pair of eyes before anything ships.", "stage": "In review", "doing": "reviewing",
	},
	"post_office": {
		"name": "Post Office", "icon": preload("res://assets/icons/git-pull-request.svg"), "color": Color("D9A321"),
		"tagline": "Finished work leaves here as a pull request.", "stage": "Shipping", "doing": "shipping",
	},
}
const QUEUED_COLOR := Color("E3A008")


## "post_office" -> "PostOffice", the node name in island_3d.tscn.
static func node_name(type: String) -> String:
	return type.to_pascal_case()


## The building type with this 3D node name ("PostOffice" -> "post_office"), or "".
static func type_of_node(node: String) -> String:
	for type in TYPES:
		if node_name(type) == node:
			return type
	return ""
