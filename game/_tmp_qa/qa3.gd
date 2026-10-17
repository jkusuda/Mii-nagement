extends Node

func _ready() -> void:
	var game = load("res://scenes/game.tscn").instantiate()
	add_child(game)
	await get_tree().create_timer(2.0).timeout
	print("online=", Net.is_online)
	game.show_screen("residents")
	await get_tree().create_timer(1.0).timeout
	var cards: Array = game.residents_screen.find_children("*", "ResidentCard", true, false)
	print("offline cards=", cards.size())
	game._open_creator({})
	await get_tree().create_timer(0.4).timeout
	var c = game.creator
	c.get_node("%NameEdit").text = "Offy"
	c.get_node("%NameEdit").text_changed.emit("Offy")
	c._part_tiles["hair"][10].button_pressed = true
	c.get_node("%MoveInButton").pressed.emit()
	await get_tree().create_timer(1.0).timeout
	var names := []
	for card in game.residents_screen.find_children("*", "ResidentCard", true, false):
		names.append(card.resident.get("name"))
	print("after offline create: screen=", game.residents_screen.visible, " names=", names)
	get_viewport().get_texture().get_image().save_png(OS.get_cmdline_user_args()[0])
	get_tree().quit()
