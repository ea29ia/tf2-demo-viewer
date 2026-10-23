#!/usr/bin/env python3
"""Add health/ammo kit floor markers to a map GLB.

Reads kits.json (entries {class, x, y, z}) and APPENDS a new mesh ("kits")
with one flat plate (48 x 48 x 2) per kit:
  health kits       -> bright green  (0.2, 0.9, 0.3)
  ammo / regen pads -> bright amber  (0.95, 0.8, 0.2)
Plates sit at (x, y, z + 1) in Source Z-up coordinates.

The kit geometry is written at the END of the existing BIN chunk: new
bufferViews/accessors are appended with offsets past the current chunk
length, so the world mesh (one primitive per material) and the prop/foliage
meshes appended by add_props_glb.py keep their byteOffsets and indices.

Entries without finite coordinates are skipped.

Usage: python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb web/public/maps/kits.json
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from array import array

_spec = importlib.util.spec_from_file_location("addprops", "tools/add_props_glb.py")
# add_props_glb runs main() only under __main__, safe to import for helpers.
_addprops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_addprops)

load_glb = _addprops.load_glb
write_glb = _addprops.write_glb
append_material = _addprops.append_material
append_mesh = _addprops.append_mesh

HEALTH_COLOR = (0.2, 0.9, 0.3)
AMMO_COLOR = (0.95, 0.8, 0.2)
PLATE = (48.0, 48.0, 2.0)

FACE_NORMALS = (
    (0, 0, 1), (0, 0, -1),
    (0, 1, 0), (0, -1, 0),
    (1, 0, 0), (-1, 0, 0),
)
FACES = [
    (4, 5, 6, 7),    # top
    (0, 3, 2, 1),    # bottom
    (2, 3, 7, 6),    # +y
    (0, 1, 5, 4),    # -y
    (1, 2, 6, 5),    # +x
    (3, 0, 4, 7),    # -x
]


def kit_color(cls: str):
    n = (cls or "").lower()
    if "health" in n or "regenerate" in n:
        return HEALTH_COLOR
    return AMMO_COLOR


def emit_plate(positions: array, normals: array, colors: array, indices: array,
               x: float, y: float, oz: float, color) -> None:
    """Append one flat plate (8 vertices, 12 triangles) to the given arrays."""
    hx, hy, h = PLATE[0] / 2.0, PLATE[1] / 2.0, PLATE[2]
    top = oz + h
    corners = [
        (x - hx, y - hy), (x + hx, y - hy),
        (x + hx, y + hy), (x - hx, y + hy),
    ]
    verts = [(cx, cy, oz) for cx, cy in corners] + [(cx, cy, top) for cx, cy in corners]
    vbase = len(positions) // 3
    for v in verts:
        positions.extend(v)
        normals.extend((0.0, 0.0, 0.0))  # filled per-face below
        colors.extend(color)
    for fidx, (a, b, c, d) in enumerate(FACES):
        n = FACE_NORMALS[fidx]
        for vi in (a, b, c, d):
            k = (vbase + vi) * 3
            normals[k:k + 3] = array("f", n)
        indices.extend((vbase + a, vbase + b, vbase + c))
        indices.extend((vbase + a, vbase + c, vbase + d))


def main(argv) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <map.glb> <kits.json>", file=sys.stderr)
        return 2
    glb_path, kits_path = argv[1], argv[2]

    with open(kits_path, "r", encoding="utf-8") as f:
        kits = json.load(f)

    gltf, bin_blob = load_glb(glb_path)
    if not gltf.get("meshes"):
        raise SystemExit(f"{glb_path}: no meshes (run bsp2glb.py first)")

    positions, normals, colors, indices = array("f"), array("f"), array("f"), array("I")

    # Click-to-inspect metadata: kit entries go into the world node's extras,
    # next to the prop entries add_props_glb.py wrote there.
    main_node = next((n for n in gltf["nodes"] if n.get("mesh") == 0), None)
    kit_entries = []

    added = 0
    skipped = 0
    for kit in kits:
        cls = kit.get("class", "")
        x, y, z = kit.get("x"), kit.get("y"), kit.get("z")
        if x is None or y is None or z is None or not all(
                math.isfinite(v) for v in (x, y, z)):
            skipped += 1
            continue
        oz = z + 1.0
        emit_plate(positions, normals, colors, indices, x, y, oz, kit_color(cls))
        added += 1
        kit_entries.append({
            "n": f"kit:{cls}",
            "p": [round(x, 1), round(y, 1), round(oz + PLATE[2] / 2.0, 1)],
            "s": [PLATE[0], PLATE[1], PLATE[2]],
        })

    # Append the kit mesh at the end of the BIN chunk; nothing existing moves.
    if indices:
        material = append_material(gltf, "kits")
        bin_blob = append_mesh(gltf, bin_blob, "kits", material,
                               positions, normals, colors, indices)

    if main_node is not None and kit_entries:
        extras = main_node.setdefault("extras", {})
        props = list(extras.get("props", []))
        props.extend(kit_entries)
        extras["props"] = props

    write_glb(glb_path, gltf, bin_blob)

    print(f"kits added: {added}, skipped: {skipped} (of {len(kits)})", file=sys.stderr)
    print(f"kit extras entries: {len(kit_entries)}", file=sys.stderr)
    if indices:
        print(f"kits mesh: {len(positions) // 3} vertices, {len(indices) // 3} triangles",
              file=sys.stderr)
    print(f"wrote {glb_path}: {len(gltf['meshes'])} meshes, "
          f"{len(gltf.get('materials', []))} materials", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
