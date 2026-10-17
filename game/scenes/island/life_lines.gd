class_name LifeLines
extends RefCounted
## What residents say when nobody is watching: canned lines for island_life.gd.
## Keyed by vibe, job, weather and season; `{me}` and `{friend}` are filled in.
## Nothing here is sent anywhere: it is flavour the game makes up locally.

const MUTTER := {
	"happy": ["What a day on the island.", "I like it here.", "Tests green, sky blue."],
	"relaxed": ["Nothing in the mailbox. Perfect.", "I could get used to this.", "Sunny and quiet."],
	"focused": ["Now, where was I...", "One thing at a time."],
	"puzzled": ["Wait, which file was that?", "Hmm. Hmm hmm hmm."],
	"grumpy": ["That test is lying to me.", "Who wrote this? ...oh.", "Sent back AGAIN."],
	"tired": ["I need a nap.", "Five more minutes.", "So many tokens, so little time."],
	"proud": ["Nailed it.", "Did you see that PR? Beautiful.", "Flawless. Mostly."],
	"bored": ["Is the mailbox empty again?", "I could refactor something...", "Anyone? Hello?"],
	"lonely": ["Nobody hands me packages.", "I should visit someone.", "It's quiet over here."],
	"excited": ["A package! A package!", "Ooh, something to plan.", "Let me at it."],
	"sleepy": ["zzz... merge conflict... zzz", "...five... more... minutes..."],
}

const JOB := {
	"planner": ["Step one: think. Step two: more thinking.", "I dreamed about numbered lists."],
	"coder": ["I dreamed about semicolons.", "The Workshop smells like fresh code.", "Just one more commit."],
	"reviewer": ["Everything is a code smell if you squint.", "LGTM. Mostly.", "Nits are love."],
}

const WEATHER := {
	"rainy": ["Rain again. The tests must be failing.", "Good weather for debugging."],
	"cloudy": ["Cloudy. Half the tests are grumpy.", "Is it going to rain?"],
	"sunny": ["Sunny! Everything passes today.", "Great day for a deploy."],
}

const SEASON := {
	"spring": ["The blossoms are back!", "Spring cleaning the repo."],
	"summer": ["It's warm by the Lab.", "Ice cream and integration tests."],
	"autumn": ["Leaves everywhere. Like my TODOs.", "Sweater weather."],
	"winter": ["The pond froze over.", "Brr. Even the CI is cold."],
}

const POND := ["The water looks nice today.", "I wonder if there are fish.", "Ripples. Calming."]
const TREE := ["Apples! Can agents eat apples?", "This tree has been here since Day 1.", "Nice shade."]

## Short conversations: [opener, reply, (optional) opener again].
const CHATS := [
	["Hey {friend}, did your tests pass?", "Eventually.", "Ha! Same."],
	["{friend}! I touched the config loader.", "Of course you did.", "It needed it!"],
	["Any packages coming, {friend}?", "Not that I've seen.", "I'll go check the mailbox."],
	["Nice weather for a review, {friend}.", "Every weather is review weather."],
	["Do you ever dream of PRs, {friend}?", "Only the merged ones."],
	["{friend}, remind me: who runs the Lab?", "You do.", "...right."],
	["Thanks for the plan earlier, {friend}.", "Any time. Numbered lists are my love language."],
	["Psst, {friend}. I think the owner is watching.", "Act natural.", "I AM natural."],
]
const BLUNT_REPLIES := ["Yep.", "No.", "Sure.", "Fine.", "Okay."]


## One line a resident says to nobody in particular.
static func mutter(resident: Dictionary, island: Dictionary) -> String:
	var pool: Array = []
	pool.append_array(MUTTER.get(ResidentData.vibe_word(resident, island), []))
	pool.append_array(JOB.get(str(resident.get("job", "")), []))
	pool.append_array(WEATHER.get(str(island.get("weather", "")), []))
	var season := str(island.get("map", {}).get("season", ""))
	pool.append_array(SEASON.get(season, []))
	var catchphrase := str(resident.get("catchphrase", ""))
	if catchphrase != "" and randf() < 0.25:
		return catchphrase
	if pool.is_empty():
		return "Hmm."
	return _fill(pool[randi() % pool.size()], resident, {})


static func by_the_pond() -> String:
	return POND[randi() % POND.size()]


static func by_a_tree() -> String:
	return TREE[randi() % TREE.size()]


## A short back-and-forth between `a` (who walked over) and `b`. Returns
## [[speaker resident, line], ...] in order. Blunt residents answer in one word.
static func chat(a: Dictionary, b: Dictionary) -> Array:
	var script: Array = CHATS[randi() % CHATS.size()]
	var lines: Array = []
	for i in script.size():
		var speaker: Dictionary = a if i % 2 == 0 else b
		var other: Dictionary = b if i % 2 == 0 else a
		var line: String = script[i]
		if i % 2 == 1 and float(speaker.get("bluntness", 0.5)) > 0.7:
			line = BLUNT_REPLIES[randi() % BLUNT_REPLIES.size()]
		lines.append([speaker, _fill(line, speaker, other)])
	return lines


static func _fill(line: String, me: Dictionary, friend: Dictionary) -> String:
	return line.format({"me": str(me.get("name", "")), "friend": str(friend.get("name", "friend"))})
