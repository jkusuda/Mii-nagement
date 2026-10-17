extends Node
## Autoload `Portraits`: headshots of residents' Miis, for cards and lists.
##
## Portraits.of(resident) hands back a texture right away. It starts empty and
## fills in a frame or two later, once a hidden 3D stage (a SubViewport with one
## Mii and a camera on its head) has taken the photo. Each look is photographed
## once and cached, so asking again is free.
##
## Godot concept: a SubViewport renders its own little 3D world into a texture.
## `own_world_3d` keeps this stage separate from the island, and
## render_target_update_mode lets us switch rendering off while nothing is queued.

const SIZE := 128  # px, square

var _viewport := SubViewport.new()
var _mii: Mii
var _cache := {}  # look + colour (as JSON) -> ImageTexture
var _queue: Array = []  # [texture, look, colour]
var _busy := false


func _ready() -> void:
	_viewport.size = Vector2i(SIZE, SIZE)
	_viewport.own_world_3d = true
	_viewport.transparent_bg = true
	_viewport.msaa_3d = Viewport.MSAA_4X
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	add_child(_viewport)

	var env := Environment.new()
	env.background_mode = Environment.BG_CLEAR_COLOR
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color.WHITE
	env.ambient_light_energy = 0.7
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var world := WorldEnvironment.new()
	world.environment = env
	_viewport.add_child(world)

	var sun := DirectionalLight3D.new()
	sun.rotation = Vector3(-0.5, 0.4, 0)
	sun.light_energy = 0.8
	_viewport.add_child(sun)

	# Close on the head: the Mii's head spans about y = 1.0 to 1.7, hair a bit more.
	var camera := Camera3D.new()
	camera.position = Vector3(0, 1.37, 2.4)
	camera.fov = 22.0
	_viewport.add_child(camera)

	_mii = preload("res://scenes/mii/mii.tscn").instantiate()
	_mii.rotation.y = 0.25  # a little turned, like a photo
	_viewport.add_child(_mii)
	_mii.set_process(false)  # no blinking mid-photo


## The headshot for a resident (a Dictionary like Net.island's residents).
func of(resident: Dictionary) -> Texture2D:
	var look := MiiParts.for_resident(resident)
	var color := str(resident.get("color", "#FFFFFF"))
	var key := JSON.stringify([look, color], "", true)
	if _cache.has(key):
		return _cache[key]
	var texture := ImageTexture.create_from_image(Image.create(SIZE, SIZE, false, Image.FORMAT_RGBA8))
	_cache[key] = texture
	_queue.append([texture, look, Color(color)])
	if not _busy:
		_take_photos()
	return texture


## Photograph everything in the queue, one look at a time.
func _take_photos() -> void:
	_busy = true
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	while not _queue.is_empty():
		var job: Array = _queue.pop_front()
		_mii.color = job[2]
		_mii.appearance = job[1]
		# Wait until the stage has been drawn with the new look (twice, to be safe:
		# the first frame may still show the previous one).
		await RenderingServer.frame_post_draw
		await RenderingServer.frame_post_draw
		var image := _viewport.get_texture().get_image()
		if image and not image.is_empty():  # (empty when running headless)
			job[0].set_image(image)
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	_busy = false
