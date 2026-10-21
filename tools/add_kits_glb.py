#!/usr/bin/env python3
"""Add health/ammo kit floor markers to a map GLB.

Reads kits.json (entries {class, x, y, z}) and appends one flat plate
(48 x 48 x 2) per kit to the FIRST mesh primitive's buffers:
  health kits       -> bright green  (0.2, 0.9, 0.3)
  ammo / regen pads -> bright amber  (0.95, 0.8, 0.2)
Plates sit at (x, y, z + 1) in Source Z-up coordinates.

Entries without finite coordinates are skipped.

Usage: python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb web/public/maps/kits.json
"""
from __future__ import annotations

import importlib.util
import json
import math
import struct
import sys
from array import array

_spec = importlib.util.spec_from_file_location("addprops", "tools/add_props_glb.py")
# add_props_glb runs main() only under __main__, safe to import for helpers.
_addprops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_addprops)

load_glb = _addprops.load_glb
read_f32 = _addprops.read_f32
read_u32 = _addprops.read_u32
pad4 = _addprops.pad4

HEALTH_COLOR = (0.2, 0.9, 0.3)
AMMO_COLOR = (0.95, 0.8, 0.2)
PLATE = (48.0, 48.0, 2.0)


def kit_color(cls: str):
    n = (cls or "").lower()
    if "health" in n or "regenerate" in n:
        return HEALTH_COLOR
    return AMMO_COLOR


def main(argv) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <map.glb> <kits.json>", file=sys.stderr)
        return 2
    glb_path, kits_path = argv[1], argv[2]

    with open(kits_path, "r", encoding="utf-8") as f:
        kits = json.load(f)

    gltf, bin_blob = load_glb(glb_path)
    prim = gltf["meshes"][0]["primitives"][0]
    positions = read_f32(gltf, bin_blob, prim["attributes"]["POSITION"])
    normals = read_f32(gltf, bin_blob, prim["attributes"]["NORMAL"])
    colors = read_f32(gltf, bin_blob, prim["attributes"]["COLOR_0"])
    indices = read_u32(gltf, bin_blob, prim["indices"])
    vertex_count = len(positions) // 3

    face_normals = (
        (0, 0, 1), (0, 0, -1),
        (0, 1, 0), (0, -1, 0),
        (1, 0, 0), (-1, 0, 0),
    )
    faces = [
        (4, 5, 6, 7),    # top
        (0, 3, 2, 1),    # bottom
        (2, 3, 7, 6),    # +y
        (0, 1, 5, 4),    # -y
        (1, 2, 6, 5),    # +x
        (3, 0, 4, 7),    # -x
    ]

    added = 0
    skipped = 0
    half_x, half_y, h = PLATE[0] / 2.0, PLATE[1] / 2.0, PLATE[2]
    for kit in kits:
        cls = kit.get("class", "")
        x, y, z = kit.get("x"), kit.get("y"), kit.get("z")
        if x is None or y is None or z is None or not all(
                math.isfinite(v) for v in (x, y, z)):
            skipped += 1
            continue
        oz = z + 1.0
        top = oz + h
        color = kit_color(cls)
        corners = [
            (x - half_x, y - half_y), (x + half_x, y - half_y),
            (x + half_x, y + half_y), (x - half_x, y + half_y),
        ]
        verts = [(cx, cy, oz) for cx, cy in corners] + [(cx, cy, top) for cx, cy in corners]
        vbase = vertex_count
        for v in verts:
            positions.extend(v)
            normals.extend((0.0, 0.0, 0.0))
            colors.extend(color)
        for fidx, (a, b, c, d) in enumerate(faces):
            n = face_normals[fidx]
            for vi in (a, b, c, d):
                k = (vbase + vi) * 3
                normals[k:k + 3] = array("f", n)
            indices.extend((vbase + a, vbase + b, vbase + c))
            indices.extend((vbase + a, vbase + c, vbase + d))
        vertex_count += 8
        added += 1

    pos_bytes = pad4(positions.tobytes())
    nrm_bytes = pad4(normals.tobytes())
    col_bytes = pad4(colors.tobytes())
    idx_bytes = pad4(indices.tobytes())
    bin_out = b"".join((pos_bytes, nrm_bytes, col_bytes, idx_bytes))

    # Preserve foliage accessors (4..7) appended by add_props_glb.py: their
    # bufferViews point into the OLD binary chunk, so keep that chunk first.
    mesh_min = [min(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]
    mesh_max = [max(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]

    # Preserve the foliage mesh (appended by add_props_glb.py): its accessors
    # 4..7 point at bufferViews 4..7 which referenced offsets past the OLD main
    # chunk. Re-extract those chunks from the old binary and re-append them with
    # fresh offsets so the foliage mesh stays valid.
    foliage_mesh = next((m for m in gltf["meshes"] if m.get("name") == "foliage"), None)
    foliage_chunks = None
    if foliage_mesh is not None:
        fprim = foliage_mesh["primitives"][0]
        fpos = read_f32(gltf, bin_blob, fprim["attributes"]["POSITION"])
        fnrm = read_f32(gltf, bin_blob, fprim["attributes"]["NORMAL"])
        fcol = read_f32(gltf, bin_blob, fprim["attributes"]["COLOR_0"])
        fidx = read_u32(gltf, bin_blob, fprim["indices"])
        foliage_chunks = tuple(pad4(a.tobytes()) for a in (fpos, fnrm, fcol, fidx))

    gltf["buffers"] = [{"byteLength": len(bin_out)}]
    gltf["bufferViews"] = [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
        {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(nrm_bytes), "target": 34962},
        {"buffer": 0, "byteOffset": len(pos_bytes) + len(nrm_bytes),
         "byteLength": len(col_bytes), "target": 34962},
        {"buffer": 0, "byteOffset": len(pos_bytes) + len(nrm_bytes) + len(col_bytes),
         "byteLength": len(idx_bytes), "target": 34963},
    ]
    acc = gltf["accessors"]
    acc[0]["count"] = vertex_count
    acc[0]["min"] = mesh_min
    acc[0]["max"] = mesh_max
    acc[1]["count"] = vertex_count
    acc[2]["count"] = vertex_count
    acc[3]["count"] = len(indices)

    if foliage_mesh is not None and foliage_chunks is not None:
        fpos_b, fnrm_b, fcol_b, fidx_b = foliage_chunks
        base = len(bin_out)
        bin_out = bin_out + b"".join(foliage_chunks)
        gltf["bufferViews"].extend([
            {"buffer": 0, "byteOffset": base, "byteLength": len(fpos_b), "target": 34962},
            {"buffer": 0, "byteOffset": base + len(fpos_b), "byteLength": len(fnrm_b),
             "target": 34962},
            {"buffer": 0, "byteOffset": base + len(fpos_b) + len(fnrm_b),
             "byteLength": len(fcol_b), "target": 34962},
            {"buffer": 0, "byteOffset": base + len(fpos_b) + len(fnrm_b) + len(fcol_b),
             "byteLength": len(fidx_b), "target": 34963},
        ])
        # Accessor indices stay 4..7 (same as add_props_glb.py emitted); only
        # the bufferView offsets changed. Counts/min/max are unchanged.

    json_blob = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total = 12 + 8 + len(json_blob) + 8 + len(bin_out)
    with open(glb_path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_out), 0x004E4942))
        f.write(bin_out)

    print(f"kits added: {added}, skipped: {skipped} (of {len(kits)})", file=sys.stderr)
    print(f"wrote {glb_path}: {vertex_count} vertices, {len(indices) // 3} triangles",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
