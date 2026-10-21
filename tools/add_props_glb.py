#!/usr/bin/env python3
"""Add static props (sprp game lump) as simple coloured boxes to a map GLB.

Reads the existing web/public/maps/pl_upward.glb (single primitive built by
bsp2glb.py), parses the BSP's static-prop game lump, appends one rotated box
per prop to the same primitive's buffers and rewrites the GLB in place.

Usage: python3 tools/add_props_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
"""
from __future__ import annotations

import importlib.util
import json
import math
import struct
import sys
from array import array

spec = importlib.util.spec_from_file_location("probe", "tools/probe_props.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

WORLD = (-12000.0, 12000.0, -12000.0, 12000.0, -2500.0, 3500.0)
# (size_x, size_y, height) per keyword, matched against the model name.
SIZES = [
    (("ladder",), (8, 32, 96)),
    (("fence", "rail", "handrail"), (256, 8, 48)),
    (("tree", "foliage", "shrub", "bush", "grass"), (64, 64, 128)),
    (("rock", "boulder", "stone", "cliff"), (128, 96, 96)),
    (("stair", "step",), (128, 64, 32)),
    (("crate", "box", "barrel", "drum", "oildrum"), (32, 32, 48)),
    (("track", "railroad", "beam", "pipe"), (256, 16, 24)),
    (("lamp", "light",), (16, 16, 48)),
]
DEFAULT_SIZE = (64, 64, 64)

# Mine-cart track props: exact length along local X, uniform 34 wide x 16 high
# rail profile. Matched before the generic SIZES table (checked first).
TRACK_SIZES = [
    ("track_straight_256", (256, 34, 16)),
    ("track_straight_128", (128, 34, 16)),
    ("track_straight_64", (64, 34, 16)),
    ("track_s_curve_96", (96, 34, 16)),
    ("track_s_curve_48", (48, 34, 16)),
    ("track_arc_90degree_256", (256, 34, 16)),
    ("track_arc_90degree_128", (128, 34, 16)),
    ("track_y_joint", (128, 48, 16)),
]

# Non-walkable foliage props go into a separate mesh ("foliage") so the web
# viewer can hide them with a checkbox.
FOLIAGE_KEYWORDS = ("grass", "bush", "shrub", "weed", "foliage", "plant",
                    "reeds", "cattail", "ivy", "vine")


def is_foliage(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in FOLIAGE_KEYWORDS)


def color_for(name: str):
    n = name.lower()
    if any(k in n for k in ("rock", "boulder", "stone", "cliff")):
        return (0.32, 0.31, 0.30)  # dark gray
    if any(k in n for k in ("wood", "crate", "fence", "plank", "log")):
        return (0.46, 0.29, 0.14)  # brown
    if any(k in n for k in ("metal", "rail", "handrail", "track", "pipe", "ladder",
                            "steel", "iron")):
        return (0.55, 0.57, 0.60)  # metal gray
    if any(k in n for k in ("grass", "foliage", "shrub", "tree", "bush")):
        return (0.34, 0.49, 0.22)  # green
    return (0.50, 0.50, 0.50)  # default mid gray


def size_for(name: str):
    n = name.lower()
    for key, size in TRACK_SIZES:
        if key in n:
            return size
    for keys, size in SIZES:
        if any(k in n for k in keys):
            return size
    return DEFAULT_SIZE


def rotation_matrix(pitch: float, yaw: float, roll: float):
    """Source QAngle (degrees) -> 3x3 rotation matrix R = Rz(yaw)*Ry(pitch)*Rx(roll).

    Source convention: pitch about Y, yaw about Z (CCW seen from above in XY),
    roll about X.
    """
    p, y, r = (math.radians(a) for a in (pitch, yaw, roll))
    cp, sp, cy, sy, cr, sr = (
        math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r))
    rz = ((cy, -sy, 0.0), (sy, cy, 0.0), (0.0, 0.0, 1.0))
    ry = ((cp, 0.0, sp), (0.0, 1.0, 0.0), (-sp, 0.0, cp))
    rx = ((1.0, 0.0, 0.0), (0.0, cr, -sr), (0.0, sr, cr))

    def mul(a, b):
        return tuple(
            tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
            for i in range(3))

    return mul(mul(rz, ry), rx)


def apply_rot(m, v):
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def load_glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise SystemExit(f"{path}: not a valid GLB")
    json_blob = None
    bin_blob = None
    pos = 12
    while pos < len(data):
        length, kind = struct.unpack_from("<II", data, pos)
        pos += 8
        chunk = data[pos:pos + length]
        pos += length
        if kind == 0x4E4F534A:
            json_blob = chunk
        elif kind == 0x004E4942:
            bin_blob = chunk
    return json.loads(json_blob.decode("utf-8")), bin_blob


def read_f32(gltf, bin_blob, accessor_index) -> array:
    acc = gltf["accessors"][accessor_index]
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    a = array("f")
    a.frombytes(bin_blob[off:off + acc["count"] * 12])
    return a


def read_u32(gltf, bin_blob, accessor_index) -> array:
    acc = gltf["accessors"][accessor_index]
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    a = array("I")
    a.frombytes(bin_blob[off:off + acc["count"] * 4])
    return a


def pad4(data: bytes, fill: bytes = b"\0") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def main(argv) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input.bsp> <map.glb>", file=sys.stderr)
        return 2
    bsp_path, glb_path = argv[1], argv[2]

    bsp = probe.m.Bsp(bsp_path)
    data, _version = probe.decompress_sprp(bsp)
    names, _leaf_count, prop_count, entry = probe.parse(data)

    gltf, bin_blob = load_glb(glb_path)
    prim = gltf["meshes"][0]["primitives"][0]
    positions = read_f32(gltf, bin_blob, prim["attributes"]["POSITION"])
    normals = read_f32(gltf, bin_blob, prim["attributes"]["NORMAL"])
    colors = read_f32(gltf, bin_blob, prim["attributes"]["COLOR_0"])
    indices = read_u32(gltf, bin_blob, prim["indices"])
    vertex_count = len(positions) // 3

    added = 0
    skipped = 0
    # Foliage props are accumulated into separate buffers and written as a
    # second mesh named "foliage" (hideable in the viewer).
    f_positions = array("f")
    f_normals = array("f")
    f_colors = array("f")
    f_indices = array("I")
    f_vertex_count = 0
    f_added = 0

    def emit_box(positions, normals, colors, indices, vertex_count,
                 ox, oy, oz, rot, sx, sy, h, color):
        # Full rotation: local box corners transformed by R(pitch, yaw, roll).
        # Length axis is local X; the matrix orients the box in world space.
        hx, hy = sx / 2.0, sy / 2.0
        local = [
            (-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0),
            (-hx, -hy, h), (hx, -hy, h), (hx, hy, h), (-hx, hy, h),
        ]
        verts = [
            tuple((ox, oy, oz)[k] + apply_rot(rot, c)[k] for k in range(3))
            for c in local
        ]
        # Local face normals (bottom corners 0-3, top corners 4-7; same face
        # winding as before), rotated into world space and normalized.
        face_dirs = (
            (0, 0, 1), (0, 0, -1),
            (0, 1, 0), (0, -1, 0),
            (1, 0, 0), (-1, 0, 0),
        )
        face_normals = []
        for d in face_dirs:
            n = apply_rot(rot, d)
            ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
            face_normals.append((n[0] / ln, n[1] / ln, n[2] / ln))
        faces = [
            (4, 5, 6, 7),    # top
            (0, 3, 2, 1),    # bottom
            (2, 3, 7, 6),    # +y
            (0, 1, 5, 4),    # -y
            (1, 2, 6, 5),    # +x
            (3, 0, 4, 7),    # -x
        ]
        vbase = vertex_count
        for v in verts:
            positions.extend(v)
            normals.extend((0.0, 0.0, 0.0))  # filled per-face below
            colors.extend(color)
        for fidx, (a, b, cc, d) in enumerate(faces):
            n = face_normals[fidx]
            for vi in (a, b, cc, d):
                k = (vbase + vi) * 3
                normals[k:k + 3] = array("f", n)
            indices.extend((vbase + a, vbase + b, vbase + cc))
            indices.extend((vbase + a, vbase + cc, vbase + d))
        return vertex_count + 8

    prop_entries = []

    for i in range(prop_count):
        base = entry + i * probe.PROP_STRIDE
        ox, oy, oz = struct.unpack_from("<3f", data, base)
        pitch, yaw, roll = struct.unpack_from("<3f", data, base + 12)
        pt = struct.unpack_from("<H", data, base + 24)[0]
        name = names[pt] if pt < len(names) else ""
        if pt >= len(names) or not all(
                math.isfinite(v) for v in (ox, oy, oz, pitch, yaw, roll)):
            skipped += 1
            continue
        # Skip props that would blow up the bounding box (3D skybox props).
        if not (WORLD[0] <= ox <= WORLD[1] and WORLD[2] <= oy <= WORLD[3]
                and WORLD[4] <= oz <= WORLD[5]):
            skipped += 1
            continue
        sx, sy, h = size_for(name)
        color = color_for(name)
        rot = rotation_matrix(pitch, yaw, roll)

        # Compact entry for the web click-to-inspect lookup. Name keeps the
        # full path minus the models/props_mining/ prefix; p is the box center
        # (Source coords, Z-up); s is the full size.
        short = name
        for prefix in ("models/props_mining/", "models/"):
            if short.startswith(prefix):
                short = short[len(prefix):]
                break
        prop_entries.append({
            "n": short,
            "p": [round(ox, 1), round(oy, 1), round(oz + h / 2.0, 1)],
            "s": [sx, sy, h],
        })

        if is_foliage(name):
            f_vertex_count = emit_box(
                f_positions, f_normals, f_colors, f_indices, f_vertex_count,
                ox, oy, oz, rot, sx, sy, h, color)
            f_added += 1
            added += 1
            continue

        vertex_count = emit_box(
            positions, normals, colors, indices, vertex_count,
            ox, oy, oz, rot, sx, sy, h, color)
        added += 1

    # Rewrite the GLB with extended buffers (single primitive preserved).
    pos_bytes = pad4(positions.tobytes())
    nrm_bytes = pad4(normals.tobytes())
    col_bytes = pad4(colors.tobytes())
    idx_bytes = pad4(indices.tobytes())
    bin_out = b"".join((pos_bytes, nrm_bytes, col_bytes, idx_bytes))

    mesh_min = [min(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]
    mesh_max = [max(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]

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

    # Foliage mesh (optional): appended as mesh 1 + node, sharing the buffer.
    if f_added > 0 and len(f_indices) > 0:
        fpos = pad4(f_positions.tobytes())
        fnrm = pad4(f_normals.tobytes())
        fcol = pad4(f_colors.tobytes())
        fidx = pad4(f_indices.tobytes())
        fbin = b"".join((fpos, fnrm, fcol, fidx))
        base = len(bin_out)
        bin_out = bin_out + fbin
        view_base = len(gltf["bufferViews"])
        gltf["bufferViews"].extend([
            {"buffer": 0, "byteOffset": base, "byteLength": len(fpos), "target": 34962},
            {"buffer": 0, "byteOffset": base + len(fpos), "byteLength": len(fnrm), "target": 34962},
            {"buffer": 0, "byteOffset": base + len(fpos) + len(fnrm),
             "byteLength": len(fcol), "target": 34962},
            {"buffer": 0,
             "byteOffset": base + len(fpos) + len(fnrm) + len(fcol),
             "byteLength": len(fidx), "target": 34963},
        ])
        fmesh_min = [min(f_positions[i] for i in range(k, len(f_positions), 3)) for k in range(3)]
        fmesh_max = [max(f_positions[i] for i in range(k, len(f_positions), 3)) for k in range(3)]
        acc_base = len(gltf["accessors"])
        gltf["accessors"].extend([
            {"bufferView": view_base, "componentType": 5126, "count": f_vertex_count,
             "type": "VEC3", "min": fmesh_min, "max": fmesh_max},
            {"bufferView": view_base + 1, "componentType": 5126, "count": f_vertex_count,
             "type": "VEC3"},
            {"bufferView": view_base + 2, "componentType": 5126, "count": f_vertex_count,
             "type": "VEC3"},
            {"bufferView": view_base + 3, "componentType": 5125, "count": len(f_indices),
             "type": "SCALAR"},
        ])
        gltf["meshes"].append({
            "name": "foliage",
            "primitives": [{
                "attributes": {"POSITION": acc_base, "NORMAL": acc_base + 1,
                               "COLOR_0": acc_base + 2},
                "indices": acc_base + 3, "mode": 4,
            }],
        })
        node_index = len(gltf["nodes"])
        gltf["nodes"].append({"mesh": 1, "name": "foliage"})
        gltf["scenes"][0]["nodes"].append(node_index)

    gltf["buffers"] = [{"byteLength": len(bin_out)}]

    # Click-to-inspect metadata: attach compact prop entries as glTF extras on
    # the main mesh node. GLTFLoader surfaces this as node.userData.extras.
    if prop_entries:
        main_node = next((n for n in gltf["nodes"] if n.get("mesh") == 0), None)
        if main_node is not None:
            main_node["extras"] = {"props": prop_entries}

    json_blob = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total = 12 + 8 + len(json_blob) + 8 + len(bin_out)
    with open(glb_path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_out), 0x004E4942))
        f.write(bin_out)

    print(f"props added: {added}, skipped: {skipped} (of {prop_count})", file=sys.stderr)
    print(f"foliage props: {f_added} (separate mesh)", file=sys.stderr)
    print(f"extras entries: {len(prop_entries)}", file=sys.stderr)
    print(f"wrote {glb_path}: {vertex_count} vertices, {len(indices) // 3} triangles",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
