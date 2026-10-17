class_name NewsStories
extends RefCounted
## Turns backend events (docs/protocol.md) into newspaper stories.
##
## Feed events in oldest-first with add(); read the stories from `stories`
## (oldest first too). Most events are too small to be news (every `activity`
## line, every walk), so only the ones listed in _story_for() make the paper.
##
## A story is a Dictionary:
##   {"when": unix seconds, "agent": resident id or "", "text": "...",
##    "tag": "Confused", "color": Color, "section": "shipped" | "packages" | "residents",
##    "pr_url": "..." and "task": task id (only for shipped tasks), "key": "..." (to skip duplicates)}

const SECTION_COLORS := {
	"shipped": Color("2E8B57"),
	"packages": Color("3E7FCB"),
	"residents": Color("7553DF"),
}

var stories: Array[Dictionary] = []

var _titles := {}  # task id -> title, learned from `task` events
var _task_status := {}  # task id -> last status we saw (so each change is one story)
var _residents_seen := {}  # resident id -> true, for "moved in"
var _keys := {}  # story key -> true


## Remember the residents already living here (from a snapshot), so editing
## one later isn't reported as "moved in".
func know_residents(residents: Array) -> void:
	for r in residents:
		_residents_seen[str(r["id"])] = true


func add(event: Dictionary) -> Dictionary:
	var story := _story_for(event)
	if story.is_empty():
		return {}
	story["when"] = NewsStories.unix_time(str(event.get("ts", "")))
	story["key"] = "%s|%s" % [event.get("ts", ""), story["text"]]  # same for a live event and its stored copy
	if _keys.has(story["key"]):
		return {}
	_keys[story["key"]] = true
	stories.append(story)
	return story


func _story_for(e: Dictionary) -> Dictionary:
	var who := str(e.get("agent", ""))
	match e.get("type", ""):
		"task":
			var id := str(e.get("id", ""))
			var title := str(e.get("title", _titles.get(id, "a package")))
			_titles[id] = title
			var status := str(e.get("status", ""))
			if _task_status.get(id) == status:
				return {}
			_task_status[id] = status
			match status:
				"queued":
					return _make("", "A new package arrived: “%s”." % title, "New", Color("E39537"), "packages")
				"done":
					var story := _make(
						str(e.get("agent", "")),
						"“%s” is done!" % title,
						"PR" if e.get("pr_url") else "Done",
						SECTION_COLORS["shipped"],
						"shipped"
					)
					story["title"] = title
					story["task"] = id
					if e.get("pr_url"):
						story["pr_url"] = str(e["pr_url"])
					return story
				"failed":
					return _make(
						str(e.get("agent", "")),
						"“%s” didn't make it this time." % title,
						"Failed",
						Color("C0392B"),
						"packages"
					)
		"handoff":
			var title := str(_titles.get(str(e.get("task", "")), "the package"))
			var giver := _name(str(e.get("from", "")))
			var taker := _name(str(e.get("to", "")))
			if e.get("outcome") == "fail":
				return _make(
					str(e["to"]),
					"%s sent “%s” back to %s." % [giver, title, taker],
					"Bounced",
					Color("E36F1E"),
					"packages"
				)
			return _make(
				str(e["to"]),
				"%s carried “%s” to %s." % [giver, title, taker],
				"Handoff",
				SECTION_COLORS["packages"],
				"packages"
			)
		"bubble":
			var place := _place(who)
			match e.get("kind", ""):
				"confused":
					return _make(
						who, "%s got puzzled%s." % [_name(who), place], "Confused", Color("7553DF"), "residents"
					)
				"struggling":
					return _make(who, "%s is stuck%s." % [_name(who), place], "Stuck", Color("E36F1E"), "residents")
				"permission":
					return _make(
						who,
						"%s needs a key: %s" % [_name(who), e.get("text", "")],
						"Permission",
						Color("D6457A"),
						"residents"
					)
				"ready":
					return _make(
						who,
						"%s finished something to look at%s." % [_name(who), place],
						"Ready",
						Color("009D85"),
						"residents"
					)
				"tired":
					return _make(who, "%s is worn out%s." % [_name(who), place], "Tired", Color("C57722"), "residents")
		"chat":
			var speaker := _name(str(e.get("from", "")))
			var listener := _name(str(e.get("to", "")))
			var line := str(e.get("text", ""))
			if line.length() > 70:
				line = line.substr(0, 67) + "…"
			return _make(
				str(e.get("from", "")),
				"%s to %s: “%s”" % [speaker, listener, line],
				"Chat",
				Color("3E7FCB"),
				"residents"
			)
		"mood":
			if e.get("mood") == "proud":
				return _make(who, "%s is feeling proud." % _name(who), "Proud", Color("009D85"), "residents")
			if e.get("mood") == "frustrated":
				return _make(who, "%s is a bit frustrated." % _name(who), "Grumpy", Color("E36F1E"), "residents")
		"agent_state":
			if e.get("state") == "sleeping":
				return _make(
					who,
					"%s fell asleep%s." % [_name(who), _place(who, str(e.get("building", "")))],
					"Asleep",
					Color("8A8FA8"),
					"residents"
				)
		"resident":
			var r: Dictionary = e.get("resident", {})
			var id := str(r.get("id", ""))
			if _residents_seen.has(id):
				return {}
			_residents_seen[id] = true
			return _make(
				id, "%s moved to the island!" % r.get("name", "Someone"), "New friend", Color("2FA37A"), "residents"
			)
	return {}


func _make(agent: String, text: String, tag: String, color: Color, section: String) -> Dictionary:
	return {"agent": agent, "text": text, "tag": tag, "color": color, "section": section}


func _name(agent: String) -> String:
	return str(Net.find_resident(agent).get("name", agent.capitalize()))


## " at the Lab", from the resident's building (or the one given).
func _place(agent: String, building := "") -> String:
	if building == "":
		building = str(Net.find_resident(agent).get("building", ""))
	var place := str(Net.find_building(building).get("name", ""))
	return " at the %s" % place if place else ""


## "2026-10-16T09:40:12.123+00:00" (UTC) -> Unix seconds.
static func unix_time(ts: String) -> float:
	if ts.length() < 19:
		return Time.get_unix_time_from_system()
	return Time.get_unix_time_from_datetime_string(ts.left(19))


## Unix seconds -> "09:40" in the player's time zone.
static func clock(when: float) -> String:
	var local: float = when + Time.get_time_zone_from_system()["bias"] * 60
	var t := Time.get_datetime_dict_from_unix_time(int(local))
	return "%02d:%02d" % [t["hour"], t["minute"]]


## Unix seconds -> "2026-10-16" in the player's time zone (to group by day).
static func local_day(when: float) -> String:
	var local: float = when + Time.get_time_zone_from_system()["bias"] * 60
	return Time.get_date_string_from_unix_time(int(local))
