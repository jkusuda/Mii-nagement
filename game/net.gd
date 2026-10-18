extends Node
## Net: the game's only connection to the backend (an autoload, see project.godot).
##
## It connects to the WebSocket, turns each JSON event into a Godot *signal*, and
## reconnects automatically if the server goes away. Every message is described
## in docs/protocol.md.
##
## Godot concept: a signal is Godot's version of an event or callback. This
## script *emits* signals, and other scripts *connect* functions to them:
##     Net.mood_changed.connect(_on_mood_changed)
## so they don't need to know anything about WebSockets.
##
## Net also keeps `island`: the last snapshot, updated by every event after it.
## Screens draw from `island` and redraw on `island_changed`, so everything they
## show comes from the backend. Signals like `agent_moved` are for things that
## need the moment itself (starting a walk animation).

signal connected
signal disconnected
signal snapshot_received(island: Dictionary)
signal agent_state_changed(agent: String, state: String, building: String)
signal agent_moved(agent: String, from_building: String, to_building: String)
signal bubble_resolved(bubble_id: String)
signal handoff_happened(from_agent: String, to_agent: String, task: String, outcome: String)
signal mood_changed(agent: String, mood: String)
## One resident said something to another (a help request, the helper's reply, a note).
signal chat_said(from_agent: String, to_agent: String, task: String, text: String)
signal task_updated(task: Dictionary)
signal server_error(message: String)
## A resident was created or edited (by this client or another one).
signal resident_updated(resident: Dictionary)
## A resident moved away (`resident_removed`).
signal resident_removed(resident_id: String)
## More of a package's train of thought arrived (Net.thinking[task_id]).
signal thinking_received(task_id: String)
## Answers to send_talk_open() / send_talk() (talk_popup.gd): the conversation is open,
## a tool line while the resident looks something up, the next piece of their reply as
## it's written, the whole reply, why they couldn't answer, or the server ended the talk.
signal talk_ready(conversation_id: String)
signal talk_thinking(talk_id: String, text: String)
signal talk_partial(talk_id: String, text: String)
signal talk_replied(talk_id: String, text: String)
signal talk_failed(talk_id: String, message: String)
signal talk_closed(conversation_id: String, message: String)
## Every event from the server, as it arrived (the News tab turns them into stories).
signal event_received(event: Dictionary)
## Something in `island` changed (after any of the signals above).
signal island_changed

const RECONNECT_SECONDS := 2.0
const CONNECT_TIMEOUT_SECONDS := 5.0  # give up on a connection attempt that gets no answer
const THINKING_KEEP := 400  # entries per task (Net.thinking)
## The resident fields the server accepts (docs/protocol.md, create_resident).
const RESIDENT_FIELDS := [
	"id",
	"name",
	"job",
	"color",
	"catchphrase",
	"brain",
	"model",
	"bluntness",
	"caution",
	"chattiness",
	"creativity",
	"keys",
	"gifts",
	"appearance",
	"buildings"
]

## Task id -> the residents' train of thought while carrying it: entries
## {"agent", "kind" ("thought" | "reply" | "tool"), "text"}, oldest first. Live
## `thinking` events add to it; building_panel.gd loads older ones on demand.
var thinking := {}

## Set MII_SERVER (e.g. "127.0.0.1:8001") to use another backend.
var url := _server_url()

var is_online := false
## The latest island from the server: the last snapshot, kept up to date with
## every event since. Empty until the first snapshot arrives. Each open bubble
## also gets `"since"`: when it was asked (Unix seconds), for "2m ago".
var island := {}

## Which island to watch. "" = let the server pick (its first island).
## Change it with visit_island().
var island_id := ""

var _socket := WebSocketPeer.new()
var _seconds_until_retry := 0.0
var _seconds_connecting := 0.0
## True while visit_island() swaps sockets: the backend is still there, so the
## game doesn't show "offline" unless the new connection fails.
var _switching := false


## Switch to another island: drop the current connection and reconnect to that
## island's events. Its snapshot arrives as usual (snapshot_received).
func visit_island(id: String) -> void:
	if id == island_id and is_online and island.get("id") == id:
		return
	island_id = id
	island = {}
	_socket.close()
	if is_online:
		is_online = false  # no sends until the new socket is open
		_switching = true
	island_changed.emit()
	_try_connect()


## Godot concept: _ready() runs once, when the node enters the scene tree.
func _ready() -> void:
	_try_connect()


## MII_SERVER, else 127.0.0.1:8000. "https://host" (or "wss://host") means a server
## behind TLS. "localhost" becomes 127.0.0.1: on Windows it resolves to the IPv6
## address ::1 first, which the backend (uvicorn on 127.0.0.1) never answers, and the
## connection hangs instead of failing.
static func _server_url() -> String:
	var server := OS.get_environment("MII_SERVER") if OS.has_environment("MII_SERVER") else "127.0.0.1:8000"
	var scheme := "ws"
	for prefix in ["https://", "wss://"]:
		if server.begins_with(prefix):
			scheme = "wss"
			server = server.trim_prefix(prefix)
	for prefix in ["http://", "ws://"]:
		server = server.trim_prefix(prefix)
	server = server.trim_suffix("/")
	if server == "localhost" or server.begins_with("localhost:"):
		server = "127.0.0.1" + server.trim_prefix("localhost")
	return "%s://%s/ws" % [scheme, server]


## Godot concept: _process(delta) runs every frame. `delta` is the number of
## seconds since the last frame. WebSocketPeer doesn't work in the background,
## so we have to poll() it every frame to send and receive data.
func _process(delta: float) -> void:
	_socket.poll()

	match _socket.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not is_online:
				is_online = true
				_switching = false
				connected.emit()
			while _socket.get_available_packet_count() > 0:
				_handle_message(_socket.get_packet().get_string_from_utf8())

		WebSocketPeer.STATE_CONNECTING:
			# A connection attempt can hang (no answer at all) instead of failing: start over.
			_seconds_connecting += delta
			if _seconds_connecting >= CONNECT_TIMEOUT_SECONDS:
				_socket.close()
				_try_connect()

		WebSocketPeer.STATE_CLOSED:
			if is_online or _switching:
				is_online = false
				_switching = false
				disconnected.emit()
			_seconds_until_retry -= delta
			if _seconds_until_retry <= 0.0:
				_try_connect()


## Create a resident (no "id") or edit one (with "id"). The server answers with a
## `resident` event (-> resident_updated) or an `error` (-> server_error).
func send_resident(resident: Dictionary) -> bool:
	var data := {}
	for field in RESIDENT_FIELDS:
		if resident.has(field):
			data[field] = resident[field]
	return _send({"type": "update_resident" if data.has("id") else "create_resident", "resident": data})


## The backend's HTTP address for `path`, e.g. http_url("/brains").
func http_url(path: String) -> String:
	return url.replace("wss://", "https://").replace("ws://", "http://").trim_suffix("/ws") + path


## One HTTP call to the backend: `done` is called later with (ok, data), where
## `data` is the JSON reply (a Dictionary). When it fails, data["error"] says why
## (the backend's own message when it sent one). `body` is sent as JSON.
##
## Godot concept: HTTPRequest is a node; request() returns right away and the
## `request_completed` signal fires later. This makes a throwaway one per call.
func request_json(path: String, done: Callable, method := HTTPClient.METHOD_GET, body: Variant = null) -> void:
	var http := HTTPRequest.new()
	add_child(http)
	http.request_completed.connect(
		func(result: int, code: int, _headers, raw: PackedByteArray):
			http.queue_free()
			# JSON.new().parse() reports bad input (an HTML error page, say) with an error
			# code instead of printing an engine error.
			var json := JSON.new()
			var data = json.data if json.parse(raw.get_string_from_utf8()) == OK else null
			if typeof(data) != TYPE_DICTIONARY:
				data = {}
			var ok := result == HTTPRequest.RESULT_SUCCESS and code == 200
			if not ok and not data.has("error"):
				data["error"] = "the backend is offline" if result != HTTPRequest.RESULT_SUCCESS else "HTTP %d" % code
			# is_valid() is false once the node that asked (or owns the lambda) was freed.
			if done.is_valid():
				done.call(ok, data)
	)
	var headers := PackedStringArray(["Content-Type: application/json"] if body != null else [])
	var error := http.request(http_url(path), headers, method, JSON.stringify(body) if body != null else "")
	if error != OK:
		http.queue_free()
		done.call(false, {"error": "could not start the request (error %d)" % error})


## Move a resident away. The server replies with `resident_removed`, or an `error`
## (they're carrying a package, or a building still counts on them).
func send_delete_resident(resident_id: String) -> bool:
	return _send({"type": "delete_resident", "id": resident_id})


## Take a package back. The server replies with a `task` event (status
## `cancelled`), or an `error` if it already finished.
func send_cancel_task(task_id: String) -> bool:
	return _send({"type": "cancel_task", "id": task_id})


func _send(message: Dictionary) -> bool:
	if not is_online:
		push_warning("Not connected; %s not sent." % message["type"])
		return false
	_socket.send_text(JSON.stringify(message))
	return true


## Answer a resident's bubble. The server replies with `bubble_resolved`.
func send_answer(bubble_id: String, text: String) -> bool:
	return _send({"type": "answer_bubble", "id": bubble_id, "response": text})


## Start a conversation with a resident inside a building: they stay inside until
## send_talk_close(). Answer: talk_ready (or talk_failed) with the same id.
func send_talk_open(conversation_id: String, resident_id: String, building_id: String) -> bool:
	return _send({"type": "talk_open", "id": conversation_id, "resident": resident_id, "building": building_id})


## The conversation is over: the resident may go back to work.
func send_talk_close(conversation_id: String) -> bool:
	return _send({"type": "talk_close", "id": conversation_id})


## Say something in an open conversation. `history` is the conversation so far
## ([{"from": "you" | "resident", "text"}]). The answer comes back to this client
## only, as talk_thinking / talk_partial / talk_replied / talk_failed with the same `talk_id`.
func send_talk(
	talk_id: String, conversation_id: String, resident_id: String, building_id: String, text: String, history: Array
) -> bool:
	return _send(
		{
			"type": "talk",
			"id": talk_id,
			"conversation": conversation_id,
			"resident": resident_id,
			"building": building_id,
			"message": text,
			"history": history,
		}
	)


func _try_connect() -> void:
	_seconds_until_retry = RECONNECT_SECONDS
	_seconds_connecting = 0.0
	_socket = WebSocketPeer.new()  # a fresh peer for each attempt
	var error := _socket.connect_to_url(url + ("?island=%s" % island_id.uri_encode() if island_id else ""))
	if error != OK:
		push_warning("Could not start connecting to %s (error %d)" % [url, error])


func _handle_message(text: String) -> void:
	var event = JSON.parse_string(text)
	if typeof(event) != TYPE_DICTIONARY:
		push_warning("Ignoring a message that isn't a JSON object: %s" % text)
		return

	# A talk is private and never stored: not an island event, so not News either.
	match event.get("type", ""):
		"talk_ready":
			talk_ready.emit(str(event.get("id", "")))
			return
		"talk_thinking":
			talk_thinking.emit(str(event.get("id", "")), str(event.get("text", "")))
			return
		"talk_partial":
			talk_partial.emit(str(event.get("id", "")), str(event.get("text", "")))
			return
		"talk_closed":
			talk_closed.emit(str(event.get("id", "")), str(event.get("message", "")))
			return
		"talk_reply":
			talk_replied.emit(str(event.get("id", "")), str(event.get("text", "")))
			return
		"talk_error":
			talk_failed.emit(str(event.get("id", "")), str(event.get("message", "")))
			return

	if not event.has("ts"):
		event["ts"] = Time.get_datetime_string_from_system(true) + "+00:00"
	event_received.emit(event)

	# Unknown event types are ignored on purpose (see the rules in docs/protocol.md).
	# Each branch updates `island` first, then tells listeners.
	match event.get("type", ""):
		"snapshot":
			island = event["island"]
			island_id = str(island.get("id", ""))
			for r in island.get("residents", []):
				if r.get("state") == "idle":
					r["idle_since"] = Time.get_unix_time_from_system()  # for the "bored" vibe (ResidentData.vibe)
			for bubble in island.get("bubbles", []):
				# asked_at is ISO-8601 UTC; the first 19 characters are what Godot parses.
				var asked := str(bubble.get("asked_at", ""))
				bubble["since"] = (
					Time.get_unix_time_from_datetime_string(asked.left(19))
					if asked
					else Time.get_unix_time_from_system()
				)
			snapshot_received.emit(island)
		"resident":
			_remember_resident(event["resident"])
			resident_updated.emit(event["resident"])
		"agent_state":
			var building := text_or_empty(event.get("building"))
			var changes := {"state": event["state"], "building": building}
			if event["state"] == "idle":
				changes["idle_since"] = Time.get_unix_time_from_system()
			_patch_resident(event["agent"], changes)
			agent_state_changed.emit(str(event["agent"]), str(event["state"]), building)
		"move":
			agent_moved.emit(str(event["agent"]), text_or_empty(event["from"]), text_or_empty(event["to"]))
		"bubble":
			var bubble := {
				"id": str(event["id"]),
				"agent": str(event["agent"]),
				"kind": str(event["kind"]),
				"text": str(event["text"]),
				"options": event.get("options"),
				"task": event.get("task"),
				"detail": event.get("detail"),
				"since": Time.get_unix_time_from_system(),
			}
			_bubbles().append(bubble)
		"bubble_resolved":
			_remove_id(_bubbles(), event["id"])
			bubble_resolved.emit(str(event["id"]))
		"handoff":
			handoff_happened.emit(str(event["from"]), str(event["to"]), str(event["task"]), str(event["outcome"]))
			return  # nothing in `island` changes
		"mood":
			_patch_resident(event["agent"], {"mood": event["mood"]})
			mood_changed.emit(str(event["agent"]), str(event["mood"]))
		"activity":
			_patch_resident(event["agent"], {"activity": event["text"]})
		"chat":
			chat_said.emit(
				str(event["from"]), str(event["to"]), text_or_empty(event.get("task")), str(event.get("text", ""))
			)
			return  # nothing in `island` changes
		"weather":
			island["weather"] = str(event["weather"])
		"island":
			# The header changed (renamed, repo linked, rules edited): replace those fields only.
			island.merge(event["island"], true)
		"resident_removed":
			_remove_id(island.get("residents", []), event["id"])
			resident_removed.emit(str(event["id"]))
		"friendship":
			# Two residents handed a package over: replace that pair's entry.
			var pairs: Array = island.get_or_add("friendships", [])
			for i in range(pairs.size() - 1, -1, -1):
				if pairs[i]["a"] == event["a"] and pairs[i]["b"] == event["b"]:
					pairs.remove_at(i)
			var pair: Dictionary = event.duplicate()
			pair.erase("type")
			pair.erase("ts")
			pairs.append(pair)
		"thinking":
			add_thinking(event)
			return  # only the thinking dropdown cares; nothing in `island` changes
		"notebook":
			# How full the resident's context window is (0-1); resident_card shows it.
			_patch_resident(event["agent"], {"notebook": event["value"]})
		"layout":
			# Build mode or the creator changed who works where: replace buildings + roads.
			island["buildings"] = event["buildings"]
			island["roads"] = event["roads"]
		"task":
			_remember_task(event)
			task_updated.emit(event)
		"error":
			server_error.emit(str(event["message"]))
			return
		_:
			return
	island_changed.emit()


## Add a `thinking` event (live, or from a task's stored events) to Net.thinking.
## Text of the same resident and kind joins the previous entry; a tool event with
## the `call` id of an earlier one updates that entry (its title and details filled in).
func add_thinking(event: Dictionary) -> void:
	var task_id := str(event.get("task", ""))
	var entries: Array = thinking.get_or_add(task_id, [])
	var call := str(event.get("call", ""))
	if call:
		for i in range(entries.size() - 1, maxi(entries.size() - 50, 0) - 1, -1):
			if entries[i].get("call") == call:
				entries[i]["text"] = str(event["text"])
				entries[i]["tool"] = str(event.get("tool", entries[i]["tool"]))
				entries[i]["detail"] = str(event.get("detail", entries[i]["detail"]))
				thinking_received.emit(task_id)
				return
	var last: Dictionary = entries.back() if not entries.is_empty() else {}
	if last and last["agent"] == event["agent"] and last["kind"] == event["kind"] and event["kind"] != "tool":
		last["text"] += str(event["text"])
	else:
		(
			entries
			. append(
				{
					"agent": str(event["agent"]),
					"kind": str(event["kind"]),
					"text": str(event["text"]),
					"tool": str(event.get("tool", "other")),
					"call": call,
					"detail": str(event.get("detail", "")),
				}
			)
		)
	if entries.size() > THINKING_KEEP:
		entries.pop_front()
	thinking_received.emit(task_id)


## The resident with this id from `island`, or {} if there's none.
func find_resident(agent_id: String) -> Dictionary:
	for r in island.get("residents", []):
		if r["id"] == agent_id:
			return r
	return {}


## The building with this id from `island`, or {} if there's none.
func find_building(building_id: String) -> Dictionary:
	for b in island.get("buildings", []):
		if b["id"] == building_id:
			return b
	return {}


## The open (queued or in-progress) task with this id from `island`, or {}. `task_id` may be null.
func find_task(task_id: Variant) -> Dictionary:
	for t in island.get("tasks", []):
		if task_id != null and str(t["id"]) == str(task_id):
			return t
	return {}


## The open bubble of this resident, or {} if they don't need you right now.
func bubble_of(agent_id: String) -> Dictionary:
	for b in island.get("bubbles", []):
		if b.get("agent") == agent_id:
			return b
	return {}


## JSON null -> "" (instead of the string "<null>"); anything else as text.
func text_or_empty(value: Variant) -> String:
	return "" if value == null else str(value)


func _bubbles() -> Array:
	if not island.has("bubbles"):
		island["bubbles"] = []
	return island["bubbles"]


func _patch_resident(agent_id: Variant, changes: Dictionary) -> void:
	var r := find_resident(str(agent_id))
	if not r.is_empty():
		r.merge(changes, true)


func _remember_resident(data: Dictionary) -> void:
	var r := find_resident(str(data["id"]))
	if r.is_empty():
		if not island.has("residents"):
			island["residents"] = []
		island["residents"].append(data)
	else:
		r.merge(data, true)


## The snapshot only lists queued and in-progress tasks; keep it that way.
func _remember_task(event: Dictionary) -> void:
	var tasks: Array = island.get("tasks", [])
	_remove_id(tasks, event["id"])
	if event["status"] in ["queued", "in_progress"]:
		var t := event.duplicate()
		t.erase("type")
		tasks.append(t)
	island["tasks"] = tasks


func _remove_id(list: Array, id: Variant) -> void:
	for i in range(list.size() - 1, -1, -1):
		if list[i]["id"] == id:
			list.remove_at(i)
