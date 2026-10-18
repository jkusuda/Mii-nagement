class_name ResidentData
## Shared lists and helpers for the Residents screen and the resident creator.
##
## A resident here is a plain Dictionary in the same shape as
## `snapshot.island.residents[]` in docs/protocol.md, plus `bubble` (their open
## bubble, added by the Residents screen).
##
## Godot concept: a script with only `const`s and `static func`s works like a
## Python module. `class_name` makes it reachable by name from any script, e.g.
## `ResidentData.JOBS`.

## Must match JOBS in backend/mii/personality.py.
const JOBS := ["planner", "coder", "reviewer"]

## Where each job starts working (job -> building name), for the creator footer.
const JOB_HOMES := {"planner": "Mailbox", "coder": "Workshop", "reviewer": "Courthouse"}

## Brain id -> label. Must match BRAINS in backend/mii/agents/acp.py, plus
## `openrouter` (backend/mii/agents/openrouter.py).
const BRAINS := {
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
## Same names and defaults as DEFAULT_KEYS in backend/mii/models.py. Each gift's tools
## follow one key too (GET /gifts `key`, backend/mii/gifts.py): a gift whose key is "no" stays home.
## [key, label, what it covers, default]
const HOUSE_KEYS := [
	["read", "Read the code", "reading and searching files", "yes"],
	["edit", "Edit files", "editing, deleting and moving files", "ask"],
	["execute", "Run commands", "running shell commands, and the gifts that do (Terminal, Database)", "ask"],
	["fetch", "Use the internet", "fetching web pages, and the gifts that go online (Browser, GitHub, Docs)", "no"],
	["tools", "Other tools", "gifts from the gift shop, and any tool the keys above don't cover", "ask"],
]
## The master key ("yes" | "no"): every door opens and nothing is ever asked
## (permissions, reviews, questions). The keys above don't count while it's on.
const MASTER_KEY_HINT := "Every door opens: nothing is asked, reviews are approved, questions are left to them."
const KEY_CHOICES := {"yes": "Yes", "ask": "Ask me", "no": "No"}

## Gifts: extra MCP servers handed to the agent in ACP `session/new` (backend/mii/gifts.py).
## The built-in ones, [id, label, icon]; gifts added from the gift shop come from GET /gifts.
const GIFTS := [
	["browser", "Browser", preload("res://assets/icons/globe.svg")],
	["github", "GitHub", preload("res://assets/icons/git-pull-request.svg")],
	["docs", "Docs", preload("res://assets/icons/book-open.svg")],
	["database", "Database", preload("res://assets/icons/database.svg")],
	["terminal", "Terminal", preload("res://assets/icons/terminal.svg")],
]

## How a resident feels, as the island shows it: the backend's six moods (docs/protocol.md)
## plus a few "vibes" the game reads off the snapshot itself (see vibe()). word, colour,
## Lucide icon (white, tinted with the colour where it's shown).
const VIBES := {
	"happy": ["happy", Color("2FA37A"), preload("res://assets/icons/smile.svg")],
	"focused": ["focused", Color("3E7FCB"), preload("res://assets/icons/target.svg")],
	"confused": ["puzzled", Color("7553DF"), preload("res://assets/icons/circle-help.svg")],
	"frustrated": ["grumpy", Color("E36F1E"), preload("res://assets/icons/angry.svg")],
	"tired": ["tired", Color("C57722"), preload("res://assets/icons/battery-low.svg")],
	"proud": ["proud", Color("009D85"), preload("res://assets/icons/trophy.svg")],
	"sleepy": ["sleepy", Color("8A8FA8"), preload("res://assets/icons/moon.svg")],
	"bored": ["bored", Color("8A8FA8"), preload("res://assets/icons/meh.svg")],
	"lonely": ["lonely", Color("7553DF"), preload("res://assets/icons/frown.svg")],
	"excited": ["excited", Color("E39537"), preload("res://assets/icons/sparkles.svg")],
	"relaxed": ["relaxed", Color("2FA37A"), preload("res://assets/icons/coffee.svg")],
}
const BORED_AFTER := 180.0  # seconds idle before a resident looks bored

## The creator's colour swatches.
const COLORS := ["#9B87F5", "#EE7C93", "#2FA37A", "#3E7FCB", "#E39537", "#F2C230", "#2A9D8F", "#8A8FA8"]


## A fresh resident for the creator.
static func blank() -> Dictionary:
	return {
		"name": "",
		"job": "coder",
		"color": COLORS[0],
		"brain": "",  # none until the player picks one
		"model": null,
		"catchphrase": "",
		"bluntness": 0.5,
		"caution": 0.5,
		"chattiness": 0.5,
		"creativity": 0.5,
		"keys": default_keys(),
		"gifts": [],
		"appearance": MiiParts.DEFAULT.duplicate(),
	}


static func default_keys() -> Dictionary:
	var keys := {"master": "no"}
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


## The icon shown beside the name on the island.
static func vibe_icon(resident: Dictionary, island: Dictionary = {}) -> Texture2D:
	var v := vibe(resident, island)
	return VIBES[v][2] if VIBES.has(v) else VIBES["happy"][2]


## Is this resident inside this building right now? Only while working there: everyone
## else (idle, asleep, walking) is out on the island. The building count, the building
## panel and Talk all go by this (and the backend's `talk` checks the same).
static func inside(resident: Dictionary, building_id: String) -> bool:
	return resident.get("state") == "working" and str(resident.get("building", "")) == building_id


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
		return Protocol.bubble(str(bubble["kind"]))["tag"]
	match resident.get("state"):
		"working":
			return "Working"
		"walking":
			return "Walking"
		"sleeping":
			return "Asleep"
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
