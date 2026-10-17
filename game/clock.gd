extends Node
## Clock: the island's time of day, day count, and weather, for the top bar and
## the day/night lighting (scenes/day_night.gd).
##
## Time follows the real clock on this computer. For development there's an
## override (`dev_hour`, set by the little slider on the island view) so you can
## scrub through the day without waiting for sunset.
##
## Godot concept: this is an *autoload* (see project.godot): Godot creates one
## instance at startup and any script can reach it by name, e.g. `Clock.hour()`.

## Emitted when the shown time might have changed: every few seconds, and right
## away when dev_hour moves.
signal ticked

## Weather kinds, with how often each one comes up (out of the total).
const WEATHER := {"sunny": 5, "partly_cloudy": 3, "cloudy": 2, "rainy": 2}
const WEATHER_NAMES := {"sunny": "Sunny", "partly_cloudy": "Partly cloudy", "cloudy": "Cloudy", "rainy": "Rainy"}

## Hour of the day to pretend it is (0.0-24.0), or -1 for the real time.
var dev_hour := -1.0:
	set(value):
		dev_hour = value
		ticked.emit()


func _ready() -> void:
	# Godot concept: a Timer node fires `timeout` on repeat. Adding it as a
	# child of this autoload keeps it alive for the whole game.
	var timer := Timer.new()
	timer.wait_time = 5.0
	timer.autostart = true
	timer.timeout.connect(ticked.emit)
	add_child(timer)


## The hour of the day as a fraction (13.5 = 1:30 pm), local time.
func hour() -> float:
	if dev_hour >= 0.0:
		return dev_hour
	var t := Time.get_time_dict_from_system()
	return t["hour"] + t["minute"] / 60.0 + t["second"] / 3600.0


## "10:42" for the top bar.
func time_text() -> String:
	var minutes := int(hour() * 60.0) % (24 * 60)
	return "%d:%02d" % [minutes / 60, minutes % 60]


func is_night() -> bool:
	var h := hour()
	return h < 6.0 or h >= 18.5


## Day 1 is the day the island was founded (in local time). `created_at` is
## the snapshot's ISO-8601 UTC string; an empty or bad one counts as Day 1.
func day_number(created_at: String) -> int:
	if created_at.length() < 19:
		return 1
	# Godot's parser wants "YYYY-MM-DDTHH:MM:SS", so drop the "+00:00" part.
	var founded := Time.get_unix_time_from_datetime_string(created_at.substr(0, 19))
	return max(1, _local_day(Time.get_unix_time_from_system()) - _local_day(founded) + 1)


## Today's weather on an island: picked from the island id and the date, so it
## stays the same all day and differs between islands.
## TODO(stretch): weather from island health (rain after failing tests?).
func weather(island_id: String) -> String:
	var roll := hash("%s:%d" % [island_id, _local_day(Time.get_unix_time_from_system())])
	var total := 0
	for kind in WEATHER:
		total += WEATHER[kind]
	roll = absi(roll) % total
	for kind in WEATHER:
		roll -= WEATHER[kind]
		if roll < 0:
			return kind
	return "sunny"


## Days since 1970 on the local calendar (UTC seconds shifted by the time zone).
func _local_day(unix_utc: float) -> int:
	var bias_minutes: int = Time.get_time_zone_from_system()["bias"]
	return floori((unix_utc + bias_minutes * 60) / 86400.0)
