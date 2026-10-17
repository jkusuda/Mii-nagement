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
## show comes from the backend. Signals like `agent_moved` are still there for
## things that need the moment itself (starting a walk animation).

signal connected
signal disconnected
signal snapshot_received(island: Dictionary)
signal agent_state_changed(agent: String, state: String, building: String)
signal agent_moved(agent: String, from_building: String, to_building: String)
signal bubble_shown(bubble: Dictionary)
signal bubble_resolved(bubble_id: String)
signal handoff_happened(from_agent: String, to_agent: String, task: String, outcome: String)
signal mood_changed(agent: String, mood: String)
## One resident said something to another (a help request, the helper's reply, a note).
signal chat_said(from_agent: String, to_agent: String, task: String, text: String)
## The backend's weather for the island changed (sunny / cloudy / rainy / night).
signal weather_changed(kind: String)
signal task_updated(task: Dictionary)
signal server_error(message: String)
## A resident was created or edited (by this client or another one).
signal resident_updated(resident: Dictionary)
## A resident moved away (`resident_removed`).
signal resident_removed(resident_id: String)
signal activity_reported(agent: String, text: String)
## More of a package's train of thought arrived (Net.thinking[task_id]).
signal thinking_received(task_id: String)
## Every event from the server, as it arrived (the News tab turns them into stories).
signal event_received(event: Dictionary)
## Task id -> the residents' train of thought while carrying it: entries
## {"agent", "kind" ("thought" | "reply" | "tool"), "text"}, oldest first. Live
## `thinking` events add to it; building_panel.gd loads older ones on demand.
var thinking := {}
const THINKING_KEEP := 400  # entries per task

## Something in `island` changed (after any of the signals above).
signal island_changed

## Set MII_SERVER (e.g. "127.0.0.1:8001") to use another backend. The default is
## 127.0.0.1, not "localhost": on Windows, localhost can resolve to the IPv6
## address ::1 first, which the backend (uvicorn on 127.0.0.1) doesn't answer.
var url := "ws://%s/ws" % (OS.get_environment("MII_SERVER") if OS.has_environment("MII_SERVER") else "127.0.0.1:8000")
const RECONNECT_SECONDS := 2.0

var is_online := false
## The latest island from the server: the last snapshot, kept up to date with
## every event since. Empty until the first snapshot arrives. Each open bubble
## also gets `"since"`: when we first saw it (Unix seconds), for "2m ago".
var island := {}

## Which island to watch. "" = let the server pick (its first island).
## Change it with visit_island().
var island_id := ""

var _socket := WebSocketPeer.new()
var _seconds_until_retry := 0.0


## Switch to another island: drop the current connection and reconnect to that
## island's events. Its snapshot arrives as usual (snapshot_received).
func visit_island(id: String) -> void:
	if id == island_id and is_online and island.get("id") == id:
		return
	island_id = id
	island = {}
	_socket.close()
	if is_online:
		is_online = false
		disconnected.emit()
	island_changed.emit()
	_try_connect()


## Godot concept: _ready() runs once, when the node enters the scene tree.
func _ready() -> void:
	_try_connect()


## Godot concept: _process(delta) runs every frame. `delta` is the number of
## seconds since the last frame. WebSocketPeer doesn't work in the background,
## so we have to poll() it every frame to send and receive data.
func _process(delta: float) -> void:
	_socket.poll()

	match _socket.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not is_online:
				is_online = true
				connected.emit()
			while _socket.get_available_packet_count() > 0:
				_handle_message(_socket.get_packet().get_string_from_utf8())

		WebSocketPeer.STATE_CLOSED:
			if is_online:
				is_online = false
				disconnected.emit()
			_seconds_until_retry -= delta
			if _seconds_until_retry <= 0.0:
				_try_connect()


## The resident fields the server accepts (docs/protocol.md, create_resident).
const RESIDENT_FIELDS := ["id", "name", "job", "color", "catchphrase", "brain", "model",
	"bluntness", "caution", "chattiness", "creativity", "keys", "gifts", "appearance", "buildings"]


## Create a resident (no "id") or edit one (with "id"). The server answers with a
## `resident` event (-> resident_updated) or an `error` (-> server_error).
func send_resident(resident: Dictionary) -> bool:
	if not is_online:
		push_warning("Not connected; resident not sent.")
		return false
	var data := {}
	for field in RESIDENT_FIELDS:
		if resident.has(field):
			data[field] = resident[field]
	var kind := "update_resident" if data.has("id") else "create_resident"
	_socket.send_text(JSON.stringify({"type": kind, "resident": data}))
	return true


## The backend's HTTP address for `path`, e.g. http_url("/brains").
func http_url(path: String) -> String:
	return url.replace("ws://", "http://").trim_suffix("/ws") + path


## One HTTP call to the backend: `done` is called later with (ok, data), where
## `data` is the JSON reply (a Dictionary). When it fails, data["error"] says why
## (the backend's own message when it sent one). `body` is sent as JSON.
##
## Godot concept: HTTPRequest is a node; request() returns right away and the
## `request_completed` signal fires later. This makes a throwaway one per call.
func request_json(path: String, done: Callable, method := HTTPClient.METHOD_GET, body = null) -> void:
	var http := HTTPRequest.new()
	add_child(http)
	http.request_completed.connect(func(result: int, code: int, _headers, raw: PackedByteArray):
		http.queue_free()
		# JSON.new().parse() reports bad input with an error code instead of printing
		# an engine error (an old backend answers unknown paths with an HTML page).
		var json := JSON.new()
		var data = json.data if json.parse(raw.get_string_from_utf8()) == OK else null
		if typeof(data) != TYPE_DICTIONARY:
			data = {}
		var ok := result == HTTPRequest.RESULT_SUCCESS and code == 200
		if not ok and not data.has("error"):
			data["error"] = "the backend is offline" if result != HTTPRequest.RESULT_SUCCESS else "HTTP %d" % code
		done.call(ok, data))
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
func send_answer(bubble_id: String, text: String) -> void:
	if not is_online:
		push_warning("Not connected; answer not sent.")
		return
	_socket.send_text(JSON.stringify({"type": "answer_bubble", "id": bubble_id, "response": text}))


func _try_connect() -> void:
	_seconds_until_retry = RECONNECT_SECONDS
	_socket = WebSocketPeer.new()  # a fresh peer for each attempt
	var error := _socket.connect_to_url(url + ("?island=%s" % island_id.uri_encode() if island_id else ""))
	if error != OK:
		push_warning("Could not start connecting to %s (error %d)" % [url, error])


func _handle_message(text: String) -> void:
	var event = JSON.parse_string(text)
	if typeof(event) != TYPE_DICTIONARY:
		push_warning("Ignoring a message that isn't a JSON object: %s" % text)
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
				bubble["since"] = Time.get_unix_time_from_system()
			snapshot_received.emit(island)
		"resident":
			_remember_resident(event["resident"])
			resident_updated.emit(event["resident"])
		"agent_state":
			var building := _str(event.get("building"))
			var changes := {"state": event["state"], "building": building}
			if event["state"] == "idle":
				changes["idle_since"] = Time.get_unix_time_from_system()
			_patch_resident(event["agent"], changes)
			agent_state_changed.emit(str(event["agent"]), str(event["state"]), building)
		"move":
			agent_moved.emit(str(event["agent"]), _str(event["from"]), _str(event["to"]))
		"bubble":
			var bubble := {
				"id": str(event["id"]), "agent": str(event["agent"]), "kind": str(event["kind"]),
				"text": str(event["text"]), "options": event.get("options"),
				"task": event.get("task"), "detail": event.get("detail"),
				"since": Time.get_unix_time_from_system(),
			}
			_bubbles().append(bubble)
			bubble_shown.emit(bubble)
		"bubble_resolved":
			var open := _bubbles()
			for i in range(open.size() - 1, -1, -1):
				if open[i]["id"] == event["id"]:
					open.remove_at(i)
			bubble_resolved.emit(str(event["id"]))
		"handoff":
			handoff_happened.emit(str(event["from"]), str(event["to"]), str(event["task"]), str(event["outcome"]))
			return  # nothing in `island` changes
		"mood":
			_patch_resident(event["agent"], {"mood": event["mood"]})
			mood_changed.emit(str(event["agent"]), str(event["mood"]))
		"activity":
			_patch_resident(event["agent"], {"activity": event["text"]})
			activity_reported.emit(str(event["agent"]), str(event["text"]))
		"chat":
			chat_said.emit(str(event["from"]), str(event["to"]), _str(event.get("task")), str(event.get("text", "")))
			return  # nothing in `island` changes
		"weather":
			island["weather"] = str(event["weather"])
			weather_changed.emit(str(event["weather"]))
		"island":
			# The header changed (renamed, repo linked, rules edited): replace those fields only.
			island.merge(event["island"], true)
		"resident_removed":
			var residents: Array = island.get("residents", [])
			for i in range(residents.size() - 1, -1, -1):
				if residents[i]["id"] == event["id"]:
					residents.remove_at(i)
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
## Text of the same resident and kind joins the previous entry.
func add_thinking(event: Dictionary) -> void:
	var task_id := str(event.get("task", ""))
	var entries: Array = thinking.get_or_add(task_id, [])
	var last: Dictionary = entries.back() if not entries.is_empty() else {}
	if last and last["agent"] == event["agent"] and last["kind"] == event["kind"] and event["kind"] != "tool":
		last["text"] += str(event["text"])
	else:
		entries.append({"agent": str(event["agent"]), "kind": str(event["kind"]), "text": str(event["text"])})
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


func _bubbles() -> Array:
	if not island.has("bubbles"):
		island["bubbles"] = []
	return island["bubbles"]


func _patch_resident(agent_id, changes: Dictionary) -> void:
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
	for i in range(tasks.size() - 1, -1, -1):
		if tasks[i]["id"] == event["id"]:
			tasks.remove_at(i)
	if event["status"] in ["queued", "in_progress"]:
		var t := event.duplicate()
		t.erase("type")
		tasks.append(t)
	island["tasks"] = tasks


## JSON null -> "" (instead of the string "<null>").
func _str(value) -> String:
	return "" if value == null else str(value)
