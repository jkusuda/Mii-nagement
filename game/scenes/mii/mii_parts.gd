class_name MiiParts
## Everything a Mii can look like: face parts, hairstyles, skin and hair colours,
## and the `appearance` dictionary a resident carries (docs/mii-customization.md).
##
## The pictures come from the Mii Maker rips, turned into game files by
## tools/build_mii_parts.py:
##   assets/mii/eyes.png, brows.png, noses.png, mouths.png   one atlas per kind
##   assets/mii/hair/hair_NN.obj, hat_cap.obj, hat_beanie.obj   one mesh per style
##   assets/mii/hair_icons.png                                  thumbnails for the creator
## Each atlas is a grid of CELL x CELL squares, COLS per row, one style per square.
##
## Like ResidentData, this is a script of consts and static funcs: use it as
## `MiiParts.random()` from anywhere.

const CELL := 128
const COLS := 8

## kind -> [atlas, number of styles]
const FACE := {
	"eyes": [preload("res://assets/mii/eyes.png"), 60],
	"brows": [preload("res://assets/mii/brows.png"), 24],
	"nose": [preload("res://assets/mii/noses.png"), 18],
	"mouth": [preload("res://assets/mii/mouths.png"), 36],
}

## Hairstyles 0..HAIR_COUNT-1 are hair; the hats come after them. -1 = bald.
const HAIR_COUNT := 68
const HATS := ["hat_cap", "hat_beanie"]
const HAIR_ICONS := preload("res://assets/mii/hair_icons.png")
const NO_HAIR := -1

const SKIN_TONES := ["#F5D0B0", "#F2C6A0", "#E4AE84", "#C98D60", "#A0663F", "#6B412A"]
const HAIR_COLORS := ["#1E1A1A", "#3B2418", "#5E2C17", "#7C4A2B", "#A57A3E", "#D8B160", "#8C8888", "#C2492E"]

## What a brand-new Mii looks like.
const DEFAULT := {
	"skin": "#F2C6A0", "hair": 0, "hair_color": "#3B2418",
	"eyes": 0, "brows": 1, "nose": 0, "mouth": 2,
}


## How many styles a kind has: "eyes", "brows", "nose", "mouth" or "hair" (hats included).
static func count(kind: String) -> int:
	if kind == "hair":
		return HAIR_COUNT + HATS.size()
	return FACE[kind][1]


## The thumbnail for one style, cut out of its atlas.
## Godot concept: an AtlasTexture shows one rectangle of a bigger texture, so all
## 60 eye buttons share one image in memory.
static func icon(kind: String, index: int) -> AtlasTexture:
	var tex := AtlasTexture.new()
	tex.atlas = HAIR_ICONS if kind == "hair" else FACE[kind][0]
	tex.region = Rect2((index % COLS) * CELL, floori(index / float(COLS)) * CELL, CELL, CELL)
	return tex


## The mesh for a hairstyle (or hat), or null for bald.
static func hair_mesh(index: int) -> Mesh:
	if index < 0 or index >= count("hair"):
		return null
	if index < HAIR_COUNT:
		return load("res://assets/mii/hair/hair_%02d.obj" % index)
	return load("res://assets/mii/hair/%s.obj" % HATS[index - HAIR_COUNT])


static func is_hat(index: int) -> bool:
	return index >= HAIR_COUNT


## A random but tidy look: hair only (no hats), any face.
static func random(rng: RandomNumberGenerator = null) -> Dictionary:
	if rng == null:
		rng = RandomNumberGenerator.new()
		rng.randomize()
	var look := {
		"skin": SKIN_TONES[rng.randi() % SKIN_TONES.size()],
		"hair": rng.randi() % HAIR_COUNT,
		"hair_color": HAIR_COLORS[rng.randi() % HAIR_COLORS.size()],
	}
	for kind in FACE:
		look[kind] = rng.randi() % count(kind)
	return look


## The look to draw for a resident. Residents made before faces existed have no
## `appearance`, so they get a random look seeded by their id: the same every time.
static func for_resident(resident: Dictionary) -> Dictionary:
	var saved = resident.get("appearance")
	if typeof(saved) == TYPE_DICTIONARY and not saved.is_empty():
		return DEFAULT.merged(saved, true)
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(str(resident.get("id", resident.get("name", ""))))
	return random(rng)
