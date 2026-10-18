class_name Protocol
## The vocabulary the game shares with the backend, in one place. It mirrors
## docs/protocol.md (the backend's copy is backend/mii/vocab.py): change both together.
##
## Godot concept: a script with only `const`s and `static func`s works like a
## Python module; `class_name` makes it reachable from any script as `Protocol`.

## Workflow steps (building types), in the order the roads run.
const STEP_ORDER := ["mailbox", "workshop", "lab", "courthouse", "post_office"]
const REQUIRED_STEPS := ["mailbox", "workshop", "post_office"]
const STEP_NAMES := {
	"mailbox": "Mailbox", "workshop": "Workshop", "lab": "Lab", "courthouse": "Courthouse", "post_office": "Post Office"
}
const SEASONS := ["spring", "summer", "autumn", "winter"]
## A `thinking` event's tool sorts (vocab.TOOL_SORTS), worded for the dropdown's summary:
## [verb, noun]: "read 3 files", "ran 2 commands".
const TOOL_WORDS := {
	"read": ["read", "file"],
	"edit": ["edited", "file"],
	"delete": ["deleted", "file"],
	"move": ["moved", "file"],
	"search": ["searched", "time"],
	"execute": ["ran", "command"],
	"fetch": ["fetched", "page"],
	"think": ["thought", "time"],
	"other": ["used", "tool"],
}

const HELP_ICON := preload("res://assets/icons/circle-help.svg")

## Bubble kind -> how the game shows it: the tag word and icon, the card's main
## button, the tag colour, the popup header tint, and the home screen's line.
const BUBBLES := {
	"confused":
	{
		"tag": "Confused",
		"icon": HELP_ICON,
		"button": "Answer",
		"color": Color("7553DF"),
		"tint": Color("F0ECFF"),
		"ask": "%s has a question",
	},
	"struggling":
	{
		"tag": "Stuck",
		"icon": HELP_ICON,
		"button": "Answer",
		"color": Color("E36F1E"),
		"tint": Color("FFEDE0"),
		"ask": "%s is stuck",
	},
	"permission":
	{
		"tag": "Needs OK",
		"icon": HELP_ICON,
		"button": "Answer",
		"color": Color("D6457A"),
		"tint": Color("FFF6D6"),
		"ask": "%s wants a key",
	},
	"ready":
	{
		"tag": "Ready",
		"icon": preload("res://assets/icons/check.svg"),
		"button": "Review",
		"color": Color("009D85"),
		"tint": Color("E3F6EF"),
		"ask": "%s finished something",
	},
	"tired":
	{
		"tag": "Tired",
		"icon": preload("res://assets/icons/moon.svg"),
		"button": "Tuck in",
		"color": Color("C57722"),
		"tint": Color("FCEFD9"),
		"ask": "%s needs a nap",
	},
}
## A kind this game doesn't know yet (protocol.md: show unknown values neutrally).
const UNKNOWN_BUBBLE := {
	"tag": "Needs you",
	"icon": HELP_ICON,
	"button": "Answer",
	"color": Color("7553DF"),
	"tint": Color("F0ECFF"),
	"ask": "%s needs you",
}


## How to show a bubble of this kind (see BUBBLES).
static func bubble(kind: String) -> Dictionary:
	return BUBBLES.get(kind, UNKNOWN_BUBBLE)
