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

signal connected
signal disconnected
signal snapshot_received(island: Dictionary)
signal agent_state_changed(agent: String, state: String, building: String)
signal agent_moved(agent: String, from_building: String, to_building: String)
signal bubble_shown(agent: String, kind: String, bubble_id: String, text: String)
signal bubble_resolved(bubble_id: String)
signal handoff_happened(from_agent: String, to_agent: String, task: String, outcome: String)
signal mood_changed(agent: String, mood: String)
signal task_updated(task: Dictionary)
signal server_error(message: String)

const URL := "ws://localhost:8000/ws"
const RECONNECT_SECONDS := 2.0

var is_online := false

var _socket := WebSocketPeer.new()
var _seconds_until_retry := 0.0


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


## Answer a resident's bubble. The server replies with `bubble_resolved`.
func send_answer(bubble_id: String, text: String) -> void:
	if not is_online:
		push_warning("Not connected; answer not sent.")
		return
	_socket.send_text(JSON.stringify({"type": "answer_bubble", "id": bubble_id, "response": text}))


func _try_connect() -> void:
	_seconds_until_retry = RECONNECT_SECONDS
	_socket = WebSocketPeer.new()  # a fresh peer for each attempt
	var error := _socket.connect_to_url(URL)
	if error != OK:
		push_warning("Could not start connecting to %s (error %d)" % [URL, error])


func _handle_message(text: String) -> void:
	var event = JSON.parse_string(text)
	if typeof(event) != TYPE_DICTIONARY:
		push_warning("Ignoring a message that isn't a JSON object: %s" % text)
		return

	# Unknown event types are ignored on purpose (see the rules in docs/protocol.md).
	match event.get("type", ""):
		"snapshot":
			snapshot_received.emit(event["island"])
		"agent_state":
			agent_state_changed.emit(str(event["agent"]), str(event["state"]), str(event.get("building", "")))
		"move":
			agent_moved.emit(str(event["agent"]), str(event["from"]), str(event["to"]))
		"bubble":
			bubble_shown.emit(str(event["agent"]), str(event["kind"]), str(event["id"]), str(event["text"]))
		"bubble_resolved":
			bubble_resolved.emit(str(event["id"]))
		"handoff":
			handoff_happened.emit(str(event["from"]), str(event["to"]), str(event["task"]), str(event["outcome"]))
		"mood":
			mood_changed.emit(str(event["agent"]), str(event["mood"]))
		"task":
			task_updated.emit(event)
		"error":
			server_error.emit(str(event["message"]))
