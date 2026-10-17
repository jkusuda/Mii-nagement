"""Turn the raw Mii Maker rips into the game's Mii parts.

    python tools/build_mii_parts.py "<folder with the rips>"

Needs Pillow, NumPy and SciPy (`pip install pillow numpy scipy`). Reads from the rip folder:

    Wii U - Wii U Menu - Mii Maker - Mii Eyes.png   eyes, 3 x 20 grid
    eyebrows.png                                     brows, 4 x 6 grid
    noses.png                                        noses, 3 x 6 grid
    mouth.png                                        mouths, 3 x 12 grid
    Hair.obj                                         68 hairstyles, one object each
    special.obj (+ its .mtl and two tex1_*.png)      a cap and a beanie
    Meshy_AI_rigged_blank_slate_mi_All_Animations*.glb   the rigged Mii

and writes into game/assets/mii/ (and game/assets/models/mii.glb):

    eyes.png, brows.png, noses.png, mouths.png
        one atlas per part kind, CELL x CELL cells, COLS cells per row, in the
        sheet's reading order. RGBA: black ink, white where the sheet had white
        *inside* a shape (eye whites, teeth), transparent outside.
    hair/hair_NN.obj
        one mesh per hairstyle, turned to face +Z (the rips face -X). Units are
        the rip's; mii.gd scales them onto the head.
    hair/hat_cap.obj, hair/hat_beanie.obj (+ .mtl + textures)
    hair_icons.png
        a front-view thumbnail per hairstyle (same atlas layout), for the creator.
    ../models/mii.glb
        the rigged Mii without its 16 MB grey texture (mii.gd sets the material).
"""

import json
import math
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

CELL = 128  # px per atlas cell
PAD = 6  # empty px around each part, so mipmaps don't bleed into the next cell
COLS = 8

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "game" / "assets" / "mii"

# name: (file, x lines, y lines). The lines are the grid's pixel columns/rows,
# found by looking for rows/columns of grid colour.
SHEETS = {
    "eyes": ("Wii U - Wii U Menu - Mii Maker - Mii Eyes.png", [0, 131, 262, 393], list(range(118, 2339, 111))),
    "brows": ("eyebrows.png", [2, 55, 108, 161, 214], [85, 130, 175, 220, 265, 310, 355]),
    "noses": ("noses.png", [2, 55, 108, 161], [2, 47, 92, 137, 182, 227, 272]),
    "mouths": ("mouth.png", [0, 77, 154, 231], [71, 137, 203, 269, 335, 401, 467, 532, 598, 664, 730, 796, 861]),
}
# Which kinds keep white that's enclosed by ink (eye whites, teeth).
FILL_INSIDE = {"eyes", "mouths"}
# How much of a cell the source square covers: one scale per kind, so a big eye
# stays bigger than a small one. Source px per side of the square.
SOURCE_SIDE = {"eyes": 130, "brows": 52, "noses": 52, "mouths": 76}


def cut_cells(path: Path, xs: list[int], ys: list[int]) -> list[np.ndarray]:
    """Grey (0 = ink, 1 = paper) square crops, one per grid cell, row by row."""
    sheet = Image.open(path).convert("RGBA")
    paper = Image.new("RGBA", sheet.size, (255, 255, 255, 255))
    paper.alpha_composite(sheet)  # the eye sheet is transparent where the others are white
    img = np.asarray(paper.convert("L"), dtype=np.float32) / 255.0
    cells = []
    for y0, y1 in zip(ys, ys[1:]):
        for x0, x1 in zip(xs, xs[1:]):
            inner = img[y0 + 2 : y1 - 1, x0 + 2 : x1 - 1]  # skip the grid line itself
            cells.append(inner)
    return cells


def to_rgba(grey: np.ndarray, fill_inside: bool, side: int) -> Image.Image:
    """Ink -> black with alpha; enclosed white -> opaque white; paper -> transparent."""
    ink = 1.0 - grey
    alpha = ink.copy()
    rgb = np.zeros_like(grey)
    if fill_inside:
        solid = ink > 0.5
        # Close small gaps in outlines before flood-filling the paper from the edges.
        sealed = ndimage.binary_dilation(solid, iterations=2)
        labels, _ = ndimage.label(~sealed)
        edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        paper = np.isin(labels, edge[edge > 0])
        paper = ndimage.binary_dilation(paper, iterations=3) & ~solid
        inside = ~paper
        alpha = np.where(inside, 1.0, ink)
        rgb = np.where(inside, grey, 0.0)
    # Centre the crop on a transparent square of `side` px.
    h, w = grey.shape
    canvas = np.zeros((side, side, 4), dtype=np.float32)
    oy, ox = (side - h) // 2, (side - w) // 2
    sy, sx = max(-oy, 0), max(-ox, 0)
    hh, ww = min(h - sy, side), min(w - sx, side)
    oy, ox = max(oy, 0), max(ox, 0)
    for c in range(3):
        canvas[oy : oy + hh, ox : ox + ww, c] = rgb[sy : sy + hh, sx : sx + ww]
    canvas[oy : oy + hh, ox : ox + ww, 3] = alpha[sy : sy + hh, sx : sx + ww]
    # Transparent pixels stay black (the ink colour), so filtering at edges blends toward ink.
    return Image.fromarray((canvas * 255).round().astype(np.uint8), "RGBA")


def atlas(parts: list[Image.Image]) -> Image.Image:
    rows = math.ceil(len(parts) / COLS)
    sheet = Image.new("RGBA", (COLS * CELL, rows * CELL), (0, 0, 0, 0))
    for i, part in enumerate(parts):
        part = part.resize((CELL - 2 * PAD, CELL - 2 * PAD), Image.LANCZOS)
        sheet.paste(part, ((i % COLS) * CELL + PAD, (i // COLS) * CELL + PAD))
    return sheet


# --- Hair -----------------------------------------------------------------------


def read_obj(path: Path) -> tuple[list, list, list, list]:
    """(objects, v, vt, vn). objects = [(name, [(material, [face, ...]), ...])], face = [(vi, ti, ni), ...] 0-based."""
    v, vt, vn, objects = [], [], [], []
    material = None
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            v.append([float(p) for p in parts[1:4]])
        elif parts[0] == "vt":
            vt.append([float(p) for p in parts[1:3]])
        elif parts[0] == "vn":
            vn.append([float(p) for p in parts[1:4]])
        elif parts[0] == "o":
            objects.append((parts[1], []))
        elif parts[0] == "usemtl":
            material = parts[1]
            objects[-1][1].append((material, []))
        elif parts[0] == "f":
            if not objects[-1][1] or objects[-1][1][-1][0] != material:
                objects[-1][1].append((material, []))
            face = []
            for corner in parts[1:]:
                idx = (corner.split("/") + ["", ""])[:3]
                face.append(tuple(int(i) - 1 if i else -1 for i in idx))
            objects[-1][1][-1][1].append(face)
    return objects, v, vt, vn


def face_front(p: list[float]) -> list[float]:
    """The rips face -X with +Z to their left. Turn them to face +Z (the Mii's front)."""
    return [p[2], p[1], -p[0]]


def write_obj(path: Path, groups: list, v: list, vt: list, vn: list, mtllib: str | None = None, flip=False) -> None:
    """One object with only the vertices it uses, renumbered. flip=True turns it inside out."""
    vmap, tmap, nmap = {}, {}, {}
    out_v, out_t, out_n, body = [], [], [], []
    for material, faces in groups:
        if mtllib:
            body.append(f"usemtl {material}")
        for face in faces:
            corners = []
            for vi, ti, ni in face:
                if vi not in vmap:
                    vmap[vi] = len(out_v) + 1
                    out_v.append(face_front(v[vi]))
                c = str(vmap[vi])
                t = ""
                if ti >= 0:
                    if ti not in tmap:
                        tmap[ti] = len(out_t) + 1
                        out_t.append(vt[ti])
                    t = str(tmap[ti])
                if ni >= 0:
                    if ni not in nmap:
                        nmap[ni] = len(out_n) + 1
                        n = face_front(vn[ni])
                        out_n.append([-x for x in n] if flip else n)
                    c += f"/{t}/{nmap[ni]}"
                elif t:
                    c += f"/{t}"
                corners.append(c)
            body.append("f " + " ".join(reversed(corners) if flip else corners))
    lines = [f"# {path.stem}, made by tools/build_mii_parts.py"]
    if mtllib:
        lines.append(f"mtllib {mtllib}")
    lines += [f"v {x:.5f} {y:.5f} {z:.5f}" for x, y, z in out_v]
    lines += [f"vt {a:.5f} {b:.5f}" for a, b in out_t]
    lines += [f"vn {x:.4f} {y:.4f} {z:.4f}" for x, y, z in out_n]
    lines += ["o " + path.stem, "s 1"] + body
    path.write_text("\n".join(lines) + "\n")


# Where the head sits inside the rips' hair (after face_front): a ball this big.
# mii.gd uses the same numbers to fit the hair onto the Mii's head.
HAIR_HEAD_CENTER = (0.0, 0.36, -0.02)
HAIR_HEAD_RADIUS = 0.27


def hair_icon(groups: list, v: list) -> Image.Image:
    """A small front view (turned a little) of one hairstyle on a head, drawn with a z-buffer."""
    size = 2 * (CELL - 2 * PAD)  # draw big, shrink for smooth edges
    yaw = math.radians(-20)
    rot = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
    centre = np.array(HAIR_HEAD_CENTER) + [0, 0.02, 0]
    scale = size / 0.95

    def to_screen(p):  # -> (px x, px y, depth toward the viewer)
        x, y, z = rot @ (np.array(face_front(p)) - centre)
        return np.array([size / 2 + x * scale, size / 2 - y * scale, z])

    colour = np.zeros((size, size, 4))
    depth = np.full((size, size), -np.inf)
    ys, xs = np.mgrid[0:size, 0:size] + 0.5

    # The head: a skin-coloured ball.
    r = HAIR_HEAD_RADIUS * scale
    d2 = ((xs - size / 2) ** 2 + (ys - size / 2) ** 2) / r**2
    ball = d2 < 1
    depth[ball] = np.sqrt(1 - d2[ball]) * HAIR_HEAD_RADIUS
    colour[ball] = [245, 214, 186, 255]

    light = np.array([0.3, 0.6, 0.75]) / np.linalg.norm([0.3, 0.6, 0.75])
    base = np.array([92, 62, 44])
    for _, faces in groups:
        for face in faces:
            pts = [to_screen(v[vi]) for vi, _, _ in face]
            for i in range(1, len(pts) - 1):
                a, b, c = pts[0], pts[i], pts[i + 1]
                n = np.cross(rot @ face_front(v[face[i][0]]) - rot @ face_front(v[face[0][0]]),
                             rot @ face_front(v[face[i + 1][0]]) - rot @ face_front(v[face[0][0]]))
                if not np.linalg.norm(n):
                    continue
                shade = 0.4 + 0.6 * abs(float(n / np.linalg.norm(n) @ light))
                x0, x1 = int(max(min(a[0], b[0], c[0]), 0)), int(min(max(a[0], b[0], c[0]) + 1, size))
                y0, y1 = int(max(min(a[1], b[1], c[1]), 0)), int(min(max(a[1], b[1], c[1]) + 1, size))
                if x0 >= x1 or y0 >= y1:
                    continue
                px, py = xs[y0:y1, x0:x1], ys[y0:y1, x0:x1]
                area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if abs(area) < 1e-9:
                    continue
                w1 = ((px - a[0]) * (c[1] - a[1]) - (py - a[1]) * (c[0] - a[0])) / area
                w2 = ((b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])) / area
                w0 = 1 - w1 - w2
                inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
                z = w0 * a[2] + w1 * b[2] + w2 * c[2]
                win = inside & (z > depth[y0:y1, x0:x1])
                depth[y0:y1, x0:x1][win] = z[win]
                colour[y0:y1, x0:x1][win] = [*(base * shade), 255]
    img = Image.fromarray(colour.clip(0, 255).astype(np.uint8), "RGBA")
    return img.resize((CELL - 2 * PAD, CELL - 2 * PAD), Image.LANCZOS)


# --- The rigged Mii ---------------------------------------------------------------


def strip_textures(src: Path, dst: Path) -> None:
    """Copy a .glb without its images (mii.gd draws the Mii with its own shader)."""
    data = src.read_bytes()
    json_len = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20 : 20 + json_len])
    bin_start = 20 + json_len + 8
    blob = data[bin_start : bin_start + struct.unpack("<I", data[20 + json_len : 24 + json_len])[0]]

    drop = {img["bufferView"] for img in gltf.get("images", [])}
    for key in ("images", "textures", "samplers"):
        gltf.pop(key, None)
    for mat in gltf.get("materials", []):
        mat.pop("emissiveTexture", None)
        mat.pop("emissiveFactor", None)
        mat.get("pbrMetallicRoughness", {}).pop("baseColorTexture", None)

    # Rebuild the binary buffer without the dropped views, renumbering the rest.
    new_blob, remap, views = bytearray(), {}, []
    for i, view in enumerate(gltf["bufferViews"]):
        if i in drop:
            continue
        chunk = blob[view.get("byteOffset", 0) : view.get("byteOffset", 0) + view["byteLength"]]
        while len(new_blob) % 4:
            new_blob.append(0)
        view = dict(view, byteOffset=len(new_blob))
        new_blob += chunk
        remap[i] = len(views)
        views.append(view)
    gltf["bufferViews"] = views
    for acc in gltf["accessors"]:
        if "bufferView" in acc:
            acc["bufferView"] = remap[acc["bufferView"]]
    while len(new_blob) % 4:
        new_blob.append(0)
    gltf["buffers"] = [{"byteLength": len(new_blob)}]
    rest = json.dumps({k: val for k, val in gltf.items() if k != "extensionsUsed"})
    gltf["extensionsUsed"] = [e for e in gltf.get("extensionsUsed", []) if e in rest]

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(new_blob)
    out = struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(new_blob), 0x004E4942) + bytes(new_blob)
    dst.write_bytes(out)


def main(src: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, (file, xs, ys) in SHEETS.items():
        side = SOURCE_SIDE[kind]
        parts = [to_rgba(cell, kind in FILL_INSIDE, side) for cell in cut_cells(src / file, xs, ys)]
        atlas(parts).save(OUT / f"{kind}.png")
        print(f"{kind}: {len(parts)}")

    hair_dir = OUT / "hair"
    hair_dir.mkdir(exist_ok=True)
    objects, v, vt, vn = read_obj(src / "Hair.obj")
    icons = []
    for i, (_, groups) in enumerate(objects):
        # Hair.obj's faces and normals point into the head; turn them outward.
        write_obj(hair_dir / f"hair_{i:02d}.obj", groups, v, vt, vn, flip=True)
        icons.append(hair_icon(groups, v))
    print(f"hair: {len(objects)}")

    objects, v, vt, vn = read_obj(src / "special.obj")
    for name, groups in objects:
        hat = "hat_" + name.split("_")[0].lower()  # Cap_Mesh... -> hat_cap
        write_obj(hair_dir / f"{hat}.obj", groups, v, vt, vn, mtllib="hats.mtl")
        icons.append(hair_icon(groups, v))
    shutil.copy(src / "special.mtl", hair_dir / "hats.mtl")
    for tex in src.glob("tex1_*.png"):
        shutil.copy(tex, hair_dir / tex.name)
    print(f"hats: {len(objects)}")

    sheet = Image.new("RGBA", (COLS * CELL, math.ceil(len(icons) / COLS) * CELL), (0, 0, 0, 0))
    for i, icon in enumerate(icons):
        sheet.paste(icon, ((i % COLS) * CELL + PAD, (i // COLS) * CELL + PAD))
    sheet.save(OUT / "hair_icons.png")

    glb = next(src.glob("Meshy_AI_rigged_blank_slate_mi*.glb"))
    strip_textures(glb, ROOT / "game" / "assets" / "models" / "mii.glb")
    print("mii.glb: done")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
