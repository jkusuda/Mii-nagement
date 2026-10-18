extends Node
## Everything you hear: one song per place (crossfading when you move), the sea,
## and short sound effects. An autoload like Net, so any script can call
## Audio.sfx("popup_open") or Audio.play_place("island").
##
## It also listens by itself for:
## - every button press (a click), through the scene tree's `node_added`;
## - the island's moments on Net: a help bubble, a bubble answered, a package
##   arriving, a task done or failed, a new resident.
## Display only: nothing here goes to the backend. The Settings jukebox picks a
## song (or "follows the island") and sets the two volumes, saved on this computer.
##
## Godot concepts:
## - An autoload is a node Godot adds before the first scene and keeps for the whole run.
## - An AudioStreamPlayer plays one sound. Its `bus` is a mixer channel; we make two,
##   Music and Sounds, so each gets its own volume slider.
## - A ConfigFile is a small settings file; `user://` is this game's own folder on
##   the computer (not the project), so the volumes survive restarts.

signal song_changed

## Songs: id -> [name on the jukebox, file]. The order is the jukebox's order.
const MUSIC := {
	"island_day": ["Island Day", "res://assets/audio/music/island_day.mp3"],
	"island_night": ["Island Night", "res://assets/audio/music/island_night.mp3"],
	"home": ["The Archipelago", "res://assets/audio/music/home.mp3"],
	"resident_creator": ["New Friend", "res://assets/audio/music/resident_creator.mp3"],
	"build_mode": ["Building Day", "res://assets/audio/music/build_mode.mp3"],
}
## Which song each place plays ("island" is day or night, see _auto_song).
const PLACE_MUSIC := {"home": "home", "creator": "resident_creator", "build": "build_mode"}
## Sound effects: name -> [file, volume in dB, seconds before the same one may play again].
const SFX := {
	"click": ["res://assets/audio/sfx/click.mp3", -6.0, 0.05],
	"popup_open": ["res://assets/audio/sfx/popup_open.mp3", -6.0, 0.2],
	"bubble_permission": ["res://assets/audio/sfx/bubble_permission.mp3", -2.0, 1.0],
	"bubble_answered": ["res://assets/audio/sfx/bubble_answered.mp3", -4.0, 0.5],
	"package_arrived": ["res://assets/audio/sfx/package_arrived.mp3", -4.0, 1.5],
	"task_done": ["res://assets/audio/sfx/task_done.mp3", -2.0, 2.0],
	"task_failed": ["res://assets/audio/sfx/task_failed.mp3", -4.0, 2.0],
	"resident_moved_in": ["res://assets/audio/sfx/resident_moved_in.mp3", -3.0, 1.5],
	"island_created": ["res://assets/audio/sfx/island_created.mp3", -2.0, 1.5],
}
const SEA := "res://assets/audio/ambience/ocean_waves.mp3"
const SEA_DB := -14.0
const FADE := 1.5  # seconds a song takes to fade in / out
const SILENT := -40.0  # dB: quiet enough to stop
const VOICES := 8  # sound effects that can play at once
const PREFS := "user://audio.cfg"

## Walking on sand, looped: each resident plays it while they walk (island_resident.gd).
var footsteps: AudioStream = load("res://assets/audio/sfx/footstep_sand.mp3")
var music_volume := 0.6  # 0-1
var sound_volume := 0.8  # 0-1
var paused := false
## No sound in headless runs (make check, tests): nothing hears it, and sounds still
## playing when Godot quits show up as leaks.
var silent := DisplayServer.get_name() == "headless"
var pinned := ""  # a song picked on the jukebox; "" = follow the place
var playing := ""  # the song playing now

var _place := "home"
var _night := false
var _players: Array[AudioStreamPlayer] = []  # two, so one fades out while the other fades in
var _current := 0
var _fade: Tween
var _voices: Array[AudioStreamPlayer] = []
var _next_voice := 0
var _sounds := {}  # name -> AudioStream
var _last_played := {}  # name -> time it last played
var _sea := AudioStreamPlayer.new()


func _ready() -> void:
	# Keep playing while the game is paused, and never be paused by it.
	process_mode = Node.PROCESS_MODE_ALWAYS
	_make_bus("Music")
	_make_bus("Sounds")
	_load_prefs()
	_apply_volumes()

	for i in 2:
		var player := AudioStreamPlayer.new()
		player.bus = &"Music"
		player.volume_db = SILENT
		add_child(player)
		_players.append(player)
	for i in VOICES:
		var voice := AudioStreamPlayer.new()
		voice.bus = &"Sounds"
		add_child(voice)
		_voices.append(voice)
	for sound in SFX:
		_sounds[sound] = load(SFX[sound][0])
	_loop(footsteps)

	_sea.stream = _loop(load(SEA))
	_sea.bus = &"Sounds"
	_sea.volume_db = SEA_DB
	add_child(_sea)
	if not silent:
		_sea.play()

	get_tree().node_added.connect(_on_node_added)
	Net.event_received.connect(_on_event)
	# Day turns to night on the island: check once a second (DayNight.night, 0-1).
	var timer := Timer.new()
	timer.wait_time = 1.0
	timer.timeout.connect(_check_night)
	add_child(timer)
	timer.start()
	_refresh()


## Play the song for a place: "home", "island", "creator" or "build" (game.gd).
func play_place(place: String) -> void:
	_place = place
	_refresh()


## A short sound effect from SFX, unless the same one just played.
func sfx(sound: String) -> void:
	if silent or not _sounds.has(sound):
		return
	var now := Time.get_ticks_msec() / 1000.0
	if now - float(_last_played.get(sound, -100.0)) < float(SFX[sound][2]):
		return
	_last_played[sound] = now
	var voice := _voices[_next_voice]
	_next_voice = (_next_voice + 1) % VOICES
	voice.stream = _sounds[sound]
	voice.volume_db = SFX[sound][1]
	voice.play()


# --- The jukebox (Settings) -------------------------------------------------------


## The next (or previous, by = -1) choice: "follow the island", then each song.
func step_song(by: int) -> void:
	var choices: Array = [""] + MUSIC.keys()
	pinned = choices[posmod(choices.find(pinned) + by, choices.size())]
	_refresh()
	song_changed.emit()


func toggle_paused() -> void:
	paused = not paused
	for player in _players:
		player.stream_paused = paused
	song_changed.emit()


func song_name() -> String:
	return MUSIC[playing][0] if MUSIC.has(playing) else ""


func set_music_volume(value: float) -> void:
	music_volume = clampf(value, 0.0, 1.0)
	_apply_volumes()
	_save_prefs()


func set_sound_volume(value: float) -> void:
	sound_volume = clampf(value, 0.0, 1.0)
	_apply_volumes()
	_save_prefs()


# --- Music ------------------------------------------------------------------------


func _auto_song() -> String:
	if _place == "island":
		return "island_night" if _night else "island_day"
	return PLACE_MUSIC.get(_place, "home")


## Crossfade to the song that should play now, if it isn't playing already.
func _refresh() -> void:
	var song := pinned if pinned else _auto_song()
	if song == playing or silent:
		return
	playing = song
	var fading := _players[_current]
	_current = 1 - _current
	var rising := _players[_current]
	rising.stream = _loop(load(MUSIC[song][1]))
	rising.volume_db = SILENT
	rising.play()
	rising.stream_paused = paused
	if _fade:
		_fade.kill()
	# Godot concept: set_parallel makes the tween run both fades at the same time.
	_fade = create_tween().set_parallel()
	_fade.tween_property(rising, "volume_db", 0.0, FADE)
	_fade.tween_property(fading, "volume_db", SILENT, FADE)
	_fade.chain().tween_callback(fading.stop)
	song_changed.emit()


## Night falls (or day breaks) on the island: a little past halfway, so it doesn't flip back and forth.
func _check_night() -> void:
	var night := DayNight.night > 0.6 if not _night else DayNight.night > 0.4
	if night != _night:
		_night = night
		_refresh()


## Make a song or ambience loop forever (MP3s are imported without looping).
func _loop(stream: AudioStream) -> AudioStream:
	if stream is AudioStreamMP3:
		stream.loop = true
	return stream


# --- Listening ----------------------------------------------------------------------


## Every button anywhere clicks when pressed.
func _on_node_added(node: Node) -> void:
	if node is BaseButton:
		node.pressed.connect(sfx.bind("click"))


## The island's moments. Net emits event_received *before* it updates Net.island,
## so Net still holds what things were like before this event.
func _on_event(event: Dictionary) -> void:
	match event.get("type", ""):
		"bubble":
			sfx("bubble_permission")
		"bubble_resolved":
			if event.get("answered", true):
				sfx("bubble_answered")
		"task":
			var before := Net.find_task(event.get("id"))
			var status := str(event.get("status", ""))
			if before.is_empty() and status == "queued":
				sfx("package_arrived")
			elif not before.is_empty() and status == "done":
				sfx("task_done")
			elif not before.is_empty() and status == "failed":
				sfx("task_failed")
		"resident":
			var resident: Dictionary = event.get("resident", {})
			if Net.find_resident(str(resident.get("id", ""))).is_empty():
				sfx("resident_moved_in")


# --- Volumes ------------------------------------------------------------------------


func _make_bus(bus_name: String) -> void:
	if AudioServer.get_bus_index(bus_name) != -1:
		return
	AudioServer.add_bus()
	AudioServer.set_bus_name(AudioServer.bus_count - 1, bus_name)
	AudioServer.set_bus_send(AudioServer.bus_count - 1, &"Master")


func _apply_volumes() -> void:
	for pair in [["Music", music_volume], ["Sounds", sound_volume]]:
		var bus := AudioServer.get_bus_index(pair[0])
		AudioServer.set_bus_volume_db(bus, linear_to_db(maxf(pair[1], 0.0001)))
		AudioServer.set_bus_mute(bus, pair[1] <= 0.0)


func _load_prefs() -> void:
	var prefs := ConfigFile.new()
	if prefs.load(PREFS) == OK:
		music_volume = float(prefs.get_value("volume", "music", music_volume))
		sound_volume = float(prefs.get_value("volume", "sounds", sound_volume))


func _save_prefs() -> void:
	var prefs := ConfigFile.new()
	prefs.set_value("volume", "music", music_volume)
	prefs.set_value("volume", "sounds", sound_volume)
	prefs.save(PREFS)
