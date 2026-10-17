class_name ResidentData
## Shared lists and helpers for the Residents screen and the resident creator.
##
## A resident here is a plain Dictionary in the same shape as
## `snapshot.island.residents[]` in docs/protocol.md, plus a few display-only
## extras the protocol doesn't send yet (`bubble`, `activity`, `notebook`).
##
## Godot concept: a script with only `const`s and `static func`s works like a
## Python module. `class_name` makes it reachable by name from any script, e.g.
## `ResidentData.JOBS`.

## Must match JOBS in backend/mii/personality.py.
const JOBS := ["planner", "coder", "reviewer"]

## Where each job starts working (building id -> label), for the creator footer.
const JOB_HOMES := {"planner": "Mailbox", "coder": "Workshop", "reviewer": "Courthouse"}

## Brain id -> label. Must match BRAINS in backend/mii/agents/acp.py, plus
## `mock` and `openrouter` (backend/mii/agents/openrouter.py).
const BRAINS := {
	"mock": "Mock · free",
	"claude-code": "Claude Code",
	"codex": "Codex",
	"gemini": "Gemini CLI",
	"openrouter": "OpenRouter",
}

## The four personality sliders (backend/mii/personality.py TRAITS):
## [field, title, low end, high end]
const TRAITS := [
	["bluntness", "Bluntness", "gentle", "blunt"],
	["caution", "Caution", "bold", "careful"],
	["chattiness", "Chattiness", "quiet", "chatty"],
	["creativity", "Creativity", "by the book", "inventive"],
]

## House keys: what the resident may do without asking. Each one covers some
## ACP tool kinds; when the agent asks permission for one of them, the backend
## answers by itself ("yes"), shows a ✋ bubble ("ask"), or refuses ("no").
## [key, label, ACP tool kinds, default]
const HOUSE_KEYS := [
	["read", "Read the code", "read, search", "yes"],
	["edit", "Edit files", "edit, delete, move", "ask"],
	["execute", "Run commands", "execute", "ask"],
	["fetch", "Use the internet", "fetch", "no"],
]
const KEY_CHOICES := {"yes": "Yes", "ask": "Ask me", "no": "No"}

## Gifts: extra MCP servers handed to the agent in ACP `session/new`.
## [id, label, icon]
const GIFTS := [
	["browser", "Browser", preload("res://assets/icons/globe.svg")],
	["github", "GitHub", preload("res://assets/icons/git-pull-request.svg")],
	["docs", "Docs", preload("res://assets/icons/book-open.svg")],
	["database", "Database", preload("res://assets/icons/database.svg")],
	["terminal", "Terminal", preload("res://assets/icons/terminal.svg")],
	["godot", "Godot", preload("res://assets/icons/code.svg")],
	["video", "Video", preload("res://assets/icons/smile.svg")],
]

## How a resident feels, as the island shows it: the backend's six moods (docs/protocol.md)
## plus a few "vibes" the game reads off the snapshot itself (see vibe()). word, colour.
const VIBES := {
	"happy": ["happy", Color("2FA37A")], "focused": ["focused", Color("3E7FCB")],
	"confused": ["puzzled", Color("7553DF")], "frustrated": ["grumpy", Color("E36F1E")],
	"tired": ["tired", Color("C57722")], "proud": ["proud", Color("009D85")],
	"sleepy": ["sleepy", Color("8A8FA8")], "bored": ["bored", Color("8A8FA8")],
	"lonely": ["lonely", Color("7553DF")], "excited": ["excited", Color("E39537")],
	"relaxed": ["relaxed", Color("2FA37A")],
}
const BORED_AFTER := 180.0  # seconds idle before a resident looks bored

## The creator's colour swatches.
const COLORS := ["#9B87F5", "#EE7C93", "#2FA37A", "#3E7FCB", "#E39537", "#F2C230", "#2A9D8F", "#8A8FA8"]

## bubble kind -> [tag text, icon, primary button]
const BUBBLE_TAGS := {
	"confused": ["Confused", preload("res://assets/icons/circle-help.svg"), "Answer"],
	"struggling": ["Stuck", preload("res://assets/icons/circle-help.svg"), "Answer"],
	"permission": ["Needs OK", preload("res://assets/icons/circle-help.svg"), "Answer"],
	"ready": ["Ready", preload("res://assets/icons/check.svg"), "Review"],
	"tired": ["Tired", preload("res://assets/icons/moon.svg"), "Tuck in"],
}

## Placeholder residents (the same faces as the Bubbles sidebar), shown until the
## backend sends a snapshot.
const MOCK_RESIDENTS := [
	{
		"id": "mo", "name": "Mo", "job": "coder", "mood": "confused", "color": "#7553DF",
		"brain": "claude-code", "state": "working", "building": "lab",
		"catchphrase": "A little progress, a little joy.",
		"bubble": {"kind": "confused", "text": "Two auth tests fail only on Tuesdays."},
		"notebook": 0.64,
	},
	{
		"id": "bea", "name": "Bea", "job": "reviewer", "mood": "proud", "color": "#2FA37A",
		"brain": "codex", "state": "idle", "building": "courthouse",
		"catchphrase": "A little progress, a little joy.",
		"bubble": {"kind": "ready", "text": "Just checked PR #142. Looking tidy!"},
		"notebook": 0.28,
	},
	{
		"id": "otto", "name": "Otto", "job": "planner", "mood": "tired", "color": "#C57722",
		"brain": "gemini", "state": "idle", "building": "mailbox",
		"catchphrase": "Wants a good nap.",
		"bubble": {"kind": "tired", "text": "My notebook is full. Ready for a nap."},
		"notebook": 0.92,
	},
	{
		"id": "pip", "name": "Pip", "job": "coder", "mood": "happy", "color": "#EE7C93",
		"brain": "claude-code", "state": "working", "building": "workshop",
		"catchphrase": "Ship it, but safely!",
		"activity": "Borrow a package key for the login form?",
		"notebook": 0.48,
	},
	{
		"id": "juniper", "name": "Juniper", "job": "coder", "mood": "focused", "color": "#3E7FCB",
		"brain": "mock", "state": "working", "building": "post_office",
		"catchphrase": "A little progress, a little joy.",
		"activity": "I'm tidying #88 for the Post Office.",
		"notebook": 0.46,
	},
]


## A fresh resident for the creator.
static func blank() -> Dictionary:
	return {
		"name": "", "job": "coder", "color": COLORS[0], "brain": "mock", "model": null,
		"catchphrase": "", "bluntness": 0.5, "caution": 0.5, "chattiness": 0.5, "creativity": 0.5,
		"keys": default_keys(), "gifts": [], "appearance": MiiParts.DEFAULT.duplicate(),
	}


static func default_keys() -> Dictionary:
	var keys := {}
	for key in HOUSE_KEYS:
		keys[key[0]] = key[3]
	return keys


## "Coder · Puzzled"
static func role_line(resident: Dictionary) -> String:
	return "%s · %s" % [str(resident.get("job", "")).capitalize(), vibe_word(resident).capitalize()]


## The vibe a resident shows right now. The backend sends six moods; the game adds a few
## it can read off the snapshot itself (no backend, no AI): sleepy, excited, lonely, bored,
## relaxed. Unknown moods from a newer server fall through unchanged (protocol rule 1).
static func vibe(resident: Dictionary, island: Dictionary = {}) -> String:
	if island.is_empty():
		island = Net.island
	var state := str(resident.get("state", "idle"))
	var mood := str(resident.get("mood", "happy"))
	if state == "sleeping":
		return "sleepy"
	if state != "idle":
		return mood
	var id := str(resident.get("id", ""))
	# A package waits in the Mailbox and they run it: something to do soon.
	for t in island.get("tasks", []):
		if t.get("status") == "queued" and runs_building_type(id, "mailbox", island):
			return "excited"
	# Everyone else has made a friend and they haven't.
	var friendships: Array = island.get("friendships", [])
	if not friendships.is_empty() and best_friend(id, friendships).is_empty():
		return "lonely"
	var since := float(resident.get("idle_since", 0.0))
	if since > 0.0 and Time.get_unix_time_from_system() - since > BORED_AFTER:
		return "bored"
	if island.get("weather", "") == "sunny" and mood in ["happy", "proud"]:
		return "relaxed"
	return mood


static func vibe_word(resident: Dictionary, island: Dictionary = {}) -> String:
	var v := vibe(resident, island)
	return VIBES[v][0] if VIBES.has(v) else v


static func vibe_color(resident: Dictionary, island: Dictionary = {}) -> Color:
	var v := vibe(resident, island)
	return VIBES[v][1] if VIBES.has(v) else Color("8A8FA8")


## Does this resident run a building of this type on the island?
static func runs_building_type(resident_id: String, type: String, island: Dictionary) -> bool:
	for b in island.get("buildings", []):
		if b.get("type") == type and b.get("resident") == resident_id:
			return true
	return false


## "2 happy · 1 tired": the two most common vibes.
static func mood_summary(residents: Array) -> String:
	var moods := {}
	for r in residents:
		var mood := vibe_word(r)
		moods[mood] = moods.get(mood, 0) + 1
	var common: Array = moods.keys()
	common.sort_custom(func(a, b): return moods[a] > moods[b])
	var parts := []
	for mood in common.slice(0, 2):
		parts.append("%d %s" % [moods[mood], mood])
	return " · ".join(parts) if parts else "nobody home"


## Which filter chip a resident belongs to: "needs", "working", or "resting".
static func filter_group(resident: Dictionary) -> String:
	if resident.get("bubble"):
		return "needs"
	if resident.get("state") in ["working", "walking"]:
		return "working"
	return "resting"


## The status tag text for a card: from the bubble if there is one, else the state.
static func status_text(resident: Dictionary) -> String:
	var bubble = resident.get("bubble")
	if bubble:
		return BUBBLE_TAGS.get(bubble["kind"], ["Needs you"])[0]
	match resident.get("state"):
		"working": return "Working"
		"walking": return "Walking"
		"sleeping": return "Asleep"
	return "Resting"


## The resident's closest friend from `friendships` (snapshot shape: {a, b,
## handoffs, score}): {"id", "score", "handoffs"}, or {} if they have none yet.
## Friendship grows every time two residents hand a package to each other.
static func best_friend(resident_id: String, friendships: Array) -> Dictionary:
	var best := {}
	for f in friendships:
		var other := ""
		if str(f["a"]) == resident_id:
			other = str(f["b"])
		elif str(f["b"]) == resident_id:
			other = str(f["a"])
		if other and float(f["score"]) > float(best.get("score", -1.0)):
			best = {"id": other, "score": float(f["score"]), "handoffs": int(f["handoffs"])}
	return best


## "Best friends" / "Good friends" / "Getting to know each other", by score (0-1).
static func friendship_words(score: float) -> String:
	if score >= 0.75:
		return "Best friends with"
	if score >= 0.4:
		return "Good friends with"
	return "Getting to know"
