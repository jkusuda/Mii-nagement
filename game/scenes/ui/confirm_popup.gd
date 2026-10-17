class_name ConfirmPopup
extends PopupPanel
## "Are you sure?" before something that can't be undone (taking a package
## back, a resident moving away, abandoning an island).
##
##   ConfirmPopup.ask(self, "Abandon Fern Harbor?", "Its residents leave...", "Abandon", func(): ...)
##
## Built in code, like SettingsPopup. Each ask() makes a fresh popup on the
## window's root (so it shows even when the caller is inside another popup)
## and frees it when it closes.

const THEME := preload("res://ui/theme.tres")


## Show the question; `on_yes` runs only if the player presses the orange button.
static func ask(from: Node, title: String, text: String, yes_label: String, on_yes: Callable) -> void:
	var popup := ConfirmPopup.new()
	popup._build(title, text, yes_label, on_yes)
	from.get_tree().root.add_child(popup)
	popup.popup_hide.connect(popup.queue_free)
	popup.popup_centered(Vector2i(420, 160))
	popup._fit.call_deferred()


## Wrapped labels only know their height once they have a width, so after one
## frame shrink to fit and center again.
func _fit() -> void:
	await get_tree().process_frame
	if visible:
		popup_centered(Vector2i(get_contents_minimum_size()))


func _build(title: String, text: String, yes_label: String, on_yes: Callable) -> void:
	theme = THEME  # a popup is its own window, so it doesn't inherit the game's theme
	var style := StyleBoxFlat.new()
	style.bg_color = Color("FFFDF2")
	style.border_color = Color("E8DFC0")
	style.set_border_width_all(2)
	style.set_corner_radius_all(20)
	style.set_content_margin_all(22)
	add_theme_stylebox_override("panel", style)

	var box := VBoxContainer.new()
	box.custom_minimum_size = Vector2(380, 0)
	box.add_theme_constant_override("separation", 12)
	add_child(box)
	var heading := Label.new()
	heading.text = title
	heading.theme_type_variation = &"SectionTitle"
	heading.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(heading)
	var body := Label.new()
	body.text = text
	body.theme_type_variation = &"BodyText"
	body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(body)

	var buttons := HBoxContainer.new()
	buttons.alignment = BoxContainer.ALIGNMENT_END
	buttons.add_theme_constant_override("separation", 8)
	var no := Button.new()
	no.text = "Never mind"
	no.theme_type_variation = &"OutlineButton"
	no.custom_minimum_size.y = 38
	no.pressed.connect(hide)
	var yes := Button.new()
	yes.text = yes_label
	yes.theme_type_variation = &"AnswerButton"
	yes.custom_minimum_size = Vector2(maxf(120, 24 + 9 * yes_label.length()), 38)
	yes.set_script(preload("res://scenes/ui/answer_button.gd"))  # the wavy orange pill
	yes.pressed.connect(func():
		hide()
		on_yes.call())
	buttons.add_child(no)
	buttons.add_child(yes)
	box.add_child(buttons)
