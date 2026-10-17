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
]

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
	var mood := str(resident.get("mood", "happy"))
	return "%s · %s" % [str(resident.get("job", "")).capitalize(), mood.capitalize()]


## "2 happy · 1 tired": the two most common moods (sleeping counts as sleepy).
static func mood_summary(residents: Array) -> String:
	var moods := {}
	for r in residents:
		var mood := "sleepy" if r.get("state") == "sleeping" else str(r.get("mood", "happy"))
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
