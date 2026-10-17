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
signal task_updated(task: Dictionary)
signal server_error(message: String)
## A resident was created or edited (by this client or another one).
signal resident_updated(resident: Dictionary)
signal activity_reported(agent: String, text: String)
## Every event from the server, as it arrived (the News tab turns them into stories).
signal event_received(event: Dictionary)
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
	"bluntness", "caution", "chattiness", "creativity", "keys", "gifts"]


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
			for bubble in island.get("bubbles", []):
				bubble["since"] = Time.get_unix_time_from_system()
			snapshot_received.emit(island)
		"resident":
			_remember_resident(event["resident"])
			resident_updated.emit(event["resident"])
		"agent_state":
			var building := _str(event.get("building"))
			_patch_resident(event["agent"], {"state": event["state"], "building": building})
			agent_state_changed.emit(str(event["agent"]), str(event["state"]), building)
		"move":
			agent_moved.emit(str(event["agent"]), _str(event["from"]), _str(event["to"]))
		"bubble":
			var bubble := {
				"id": str(event["id"]), "agent": str(event["agent"]), "kind": str(event["kind"]),
				"text": str(event["text"]), "options": event.get("options"),
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
		"task":
			_remember_task(event)
			task_updated.emit(event)
		"error":
			server_error.emit(str(event["message"]))
			return
		_:
			return
	island_changed.emit()


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
