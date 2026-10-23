#!/usr/bin/env python3
"""Add static props (sprp game lump) as simple coloured boxes to a map GLB.

Reads a map GLB produced by bsp2glb.py (one mesh, one primitive per material),
parses the BSP's static-prop game lump, and APPENDS two new meshes:

  * "props"   -- oriented prop boxes (material "props_boxes")
  * "foliage" -- non-walkable plants, hideable in the viewer (same material)

The new geometry is written at the END of the existing BIN chunk: new
bufferViews and accessors are appended with byteOffsets past the current
chunk length, so every existing bufferView/accessor keeps its byteOffset and
index. Nothing in the world mesh is renumbered or rewritten.

Each prop box vertex also gets a COLOR_0 tint: the palette colour multiplied
by the baked average light of the nearest lightmapped face. This approximates
Source's baked static-prop lighting cheaply (props in tunnels/under overhangs
are no longer lit like outdoor props).

Click-to-inspect metadata (`extras.props`) is attached to the node whose
mesh is 0 (the world node) and stays in Source coordinates (Z-up). Besides the
short name / box centre / box size it carries the full model id, the sprp base
origin, the sprp angles and the uniform scale so the viewer can instance a real
prop mesh at the exact transform.

An optional third argument points at a props manifest (a JSON object with a
"models" map keyed by full model id). Props whose model has a real mesh are
left out of the box meshes (the viewer draws the real mesh instead), but their
extras entry is still written.

Usage: python3 tools/add_props_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb [manifest.json]
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


def model_id_for(name: str) -> str:
    """Full sprp model id: the name-table string without the .mdl suffix."""
    if name.lower().endswith(".mdl"):
        return name[:-4]
    return name


def load_mesh_models(path):
    """Return the set of model ids that have a real mesh, or an empty set.

    A missing / malformed manifest is not fatal: the tool then keeps emitting
    every box (the pre-manifest behaviour) and says so on stderr.
    """
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        models = manifest["models"]
        if not isinstance(models, dict):
            raise ValueError('"models" is not an object')
        return {str(key) for key in models}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"warning: {path}: unusable props manifest ({exc}); "
              f"emitting boxes for all props", file=sys.stderr)
        return set()


# ---------------------------------------------------------------------------
# Baked static-prop lighting (approximation)
# ---------------------------------------------------------------------------
# StaticPropLump_t (TF2, game lump version 10, 72-byte stride). Byte offsets
# verified against assets/pl_upward.bsp: m_LightingOrigin at 44, the single
# m_Flags byte at 31 (offset 64 is the DX level) and m_flUniformScale at 56.
PROP_LIGHTING_ORIGIN_OFFSET = 44
PROP_FLAGS_OFFSET = 31
PROP_UNIFORM_SCALE_OFFSET = 56
STATIC_PROP_USE_LIGHTING_ORIGIN = 0x2
# A valid uniform scale is finite and positive; zero/garbage values (present in
# older or authored-off entries) fall back to 1.0.
SCALE_MIN = 0.01
SCALE_MAX = 100.0

# Coarse spatial grid for nearest-face queries. 256 Source units keeps each
# shell small; the expanding-ring search stays exact.
LIGHT_GRID_CELL = 256.0
LIGHT_GRID_MAX_RING = 64


def decode_rgbexp32(lm: bytes, off: int, lut: bytes):
    """Decode one ColorRGBExp32 and return sRGB floats in [0, 1].

    Same formula as bsp2glb.build_lightmap_atlas: mantissa * 2**exponent / 255
    followed by a gamma-1/2.2 encode, via bsp2glb's byte LUT. The exponent is
    a signed int8.
    """
    e = lm[off + 3]
    if e > 127:
        e -= 256
    base = (e + 128) * 256
    return (lut[base + lm[off]] / 255.0,
            lut[base + lm[off + 1]] / 255.0,
            lut[base + lm[off + 2]] / 255.0)


def face_light_centroids(bsp):
    """Return [(x, y, z, r, g, b)] per lightmapped face.

    Source stores the face's baked average light as a 4-byte ColorRGBExp32
    immediately before the lightmap block, i.e. at `face.lightofs - 4`.
    Centroid comes from the face polygon (surfedges), in Source coordinates.
    """
    lm = bsp.lump(probe.m.LUMP_LIGHTING)
    lut = probe.m._lm_decode_lut()
    vertices = bsp.vertices()
    edges = bsp.edges()
    surfedges = bsp.surfedges()
    out = []
    for face in bsp.faces():
        lightofs = face[12]
        if lightofs < 4 or lightofs + 4 > len(lm):
            continue
        first_edge, num_edges = face[3], face[4]
        if num_edges < 3 or num_edges > 1024 or first_edge < 0:
            continue
        sx = sy = sz = 0.0
        count = 0
        ok = True
        for j in range(num_edges):
            se = surfedges[first_edge + j]
            edge_index = se if se >= 0 else -se
            if edge_index >= len(edges) // 2:
                ok = False
                break
            vi = edges[edge_index * 2] if se >= 0 else edges[edge_index * 2 + 1]
            if vi >= len(vertices) // 3:
                ok = False
                break
            sx += vertices[vi * 3]
            sy += vertices[vi * 3 + 1]
            sz += vertices[vi * 3 + 2]
            count += 1
        if not ok or count == 0:
            continue
        r, g, b = decode_rgbexp32(lm, lightofs - 4, lut)
        out.append((sx / count, sy / count, sz / count, r, g, b))
    return out


class FaceLightGrid:
    """Nearest face-centroid lookup over a coarse uniform 3D grid."""

    def __init__(self, cells, cell=LIGHT_GRID_CELL):
        self.cell = cell
        self.bins = {}
        for c in cells:
            key = (int(math.floor(c[0] / cell)),
                   int(math.floor(c[1] / cell)),
                   int(math.floor(c[2] / cell)))
            self.bins.setdefault(key, []).append(c)

    def nearest(self, p):
        """Return (r, g, b) of the nearest centroid, or None if no faces."""
        cell = self.cell
        cx = int(math.floor(p[0] / cell))
        cy = int(math.floor(p[1] / cell))
        cz = int(math.floor(p[2] / cell))
        best = None
        best_d2 = float("inf")

        def scan(dx, dy, dz):
            nonlocal best, best_d2
            for c in self.bins.get((cx + dx, cy + dy, cz + dz), ()):
                d2 = ((c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2
                      + (c[2] - p[2]) ** 2)
                if d2 < best_d2:
                    best_d2 = d2
                    best = c

        for ring in range(LIGHT_GRID_MAX_RING + 1):
            if ring == 0:
                scan(0, 0, 0)
            else:
                # Cells at Chebyshev distance `ring` from the query cell.
                for dx in range(-ring, ring + 1):
                    for dy in range(-ring, ring + 1):
                        scan(dx, dy, ring)
                        scan(dx, dy, -ring)
                for dx in range(-ring + 1, ring):
                    for dz in range(-ring + 1, ring):
                        scan(dx, ring, dz)
                        scan(dx, -ring, dz)
            # Any cell not visited yet is at least ring*cell away.
            if best is not None and best_d2 <= (ring * cell) ** 2:
                break
        return None if best is None else (best[3], best[4], best[5])


def luminance(rgb) -> float:
    """Rec. 709 relative luminance of a colour triple."""
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


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


# ---------------------------------------------------------------------------
# GLB in/out
# ---------------------------------------------------------------------------
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
    if json_blob is None or bin_blob is None:
        raise SystemExit(f"{path}: missing JSON or BIN chunk")
    return json.loads(json_blob.decode("utf-8")), bin_blob


def pad4(data: bytes, fill: bytes = b"\0") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def write_glb(path: str, gltf: dict, bin_blob: bytes) -> None:
    json_blob = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_blob), 0x004E4942))
        f.write(bin_blob)


def append_material(gltf: dict, name: str) -> int:
    """Append a plain vertex-colour material and return its index."""
    materials = gltf.setdefault("materials", [])
    materials.append({
        "name": name,
        "doubleSided": True,
        "pbrMetallicRoughness": {
            "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.92,
        },
    })
    return len(materials) - 1


def append_mesh(gltf: dict, bin_blob: bytes, name: str, material: int,
                positions: array, normals: array, colors: array,
                indices: array) -> bytes:
    """Append a single-primitive mesh whose data sits at the end of the BIN.

    Existing bufferViews/accessors are only ever *added to*: the new views
    start at `len(bin_blob)` (4-byte aligned), so all previously written
    byteOffsets, accessor indices and mesh indices stay valid.
    """
    if len(bin_blob) % 4:
        bin_blob = bin_blob + b"\0" * (4 - len(bin_blob) % 4)

    views = gltf.setdefault("bufferViews", [])
    accessors = gltf.setdefault("accessors", [])
    acc_base = len(accessors)
    view_ids = []
    cursor = len(bin_blob)
    blobs = []

    chunks = (
        (positions.tobytes(), 34962),   # ARRAY_BUFFER
        (normals.tobytes(), 34962),
        (colors.tobytes(), 34962),
        (indices.tobytes(), 34963),     # ELEMENT_ARRAY_BUFFER
    )
    for data, target in chunks:
        views.append({
            "buffer": 0,
            "byteOffset": cursor,
            "byteLength": len(data),
            "target": target,
        })
        view_ids.append(len(views) - 1)
        padded = pad4(data)
        blobs.append(padded)
        cursor += len(padded)

    vertex_count = len(positions) // 3
    mesh_min = [min(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]
    mesh_max = [max(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]
    accessors.extend([
        {"bufferView": view_ids[0], "componentType": 5126, "count": vertex_count,
         "type": "VEC3", "min": mesh_min, "max": mesh_max},
        {"bufferView": view_ids[1], "componentType": 5126, "count": vertex_count,
         "type": "VEC3"},
        {"bufferView": view_ids[2], "componentType": 5126, "count": vertex_count,
         "type": "VEC3"},
        {"bufferView": view_ids[3], "componentType": 5125, "count": len(indices),
         "type": "SCALAR"},
    ])

    gltf.setdefault("meshes", []).append({
        "name": name,
        "primitives": [{
            "attributes": {"POSITION": acc_base, "NORMAL": acc_base + 1,
                           "COLOR_0": acc_base + 2},
            "indices": acc_base + 3,
            "material": material,
            "mode": 4,
        }],
    })
    mesh_index = len(gltf["meshes"]) - 1

    gltf.setdefault("nodes", []).append({"mesh": mesh_index, "name": name})
    node_index = len(gltf["nodes"]) - 1
    scenes = gltf.setdefault("scenes", [{"nodes": []}])
    scenes[0].setdefault("nodes", []).append(node_index)

    buffers = gltf.setdefault("buffers", [{}])
    if not buffers:
        buffers.append({})
    buffers[0]["byteLength"] = cursor

    return bin_blob + b"".join(blobs)


def emit_box(positions: array, normals: array, colors: array, indices: array,
             ox: float, oy: float, oz: float, rot, sx: float, sy: float, h: float,
             color) -> None:
    """Append one oriented box (8 vertices, 12 triangles) to the given arrays."""
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
    vbase = len(positions) // 3
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


def world_stats(gltf: dict):
    """Return (vertex_count, triangle_count) of the world mesh (mesh 0)."""
    vertices = tris = 0
    for prim in gltf["meshes"][0]["primitives"]:
        vertices += gltf["accessors"][prim["attributes"]["POSITION"]]["count"]
        tris += gltf["accessors"][prim["indices"]]["count"] // 3
    return vertices, tris


def main(argv) -> int:
    if len(argv) not in (3, 4):
        print(f"usage: {argv[0]} <input.bsp> <map.glb> [props_manifest.json]",
              file=sys.stderr)
        return 2
    bsp_path, glb_path = argv[1], argv[2]
    manifest_path = argv[3] if len(argv) == 4 else None
    mesh_models = load_mesh_models(manifest_path)

    bsp = probe.m.Bsp(bsp_path)
    data, _version = probe.decompress_sprp(bsp)
    names, _leaf_count, prop_count, entry = probe.parse(data)

    # Playable-world bounds (same rule as bsp2glb.py): worldspawn
    # world_mins/world_maxs intersected with sane constants. 3D-skybox props
    # live far outside these bounds and must not be drawn as world geometry.
    import re
    world_min = [WORLD[0], WORLD[2], WORLD[4]]
    world_max = [WORLD[1], WORLD[3], WORLD[5]]
    entities = bsp.lump(probe.m.LUMP_ENTITIES).decode("utf-8", errors="replace")

    def grab_bound(key):
        match = re.search(r'"%s"\s+"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)"' % key, entities)
        return [float(match.group(i)) for i in (1, 2, 3)] if match else None

    mins, maxs = grab_bound("world_mins"), grab_bound("world_maxs")
    if mins and maxs:
        world_min = [max(a, b) for a, b in zip(world_min, mins)]
        world_max = [min(a, b) for a, b in zip(world_max, maxs)]

    gltf, bin_blob = load_glb(glb_path)
    if not gltf.get("meshes"):
        raise SystemExit(f"{glb_path}: no meshes (run bsp2glb.py first)")

    # Separate accumulators: regular prop boxes and hideable foliage boxes.
    p_positions, p_normals, p_colors, p_indices = array("f"), array("f"), array("f"), array("I")
    f_positions, f_normals, f_colors, f_indices = array("f"), array("f"), array("f"), array("I")

    # Baked lighting lookup: nearest lightmapped face centroid per prop.
    face_lights = face_light_centroids(bsp)
    light_grid = FaceLightGrid(face_lights)

    added = 0
    skipped = 0
    mesh_skipped = 0
    scale_fallback = 0
    f_added = 0
    tinted = 0
    untinted = 0
    lighting_origin_used = 0
    tint_lums = []

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
        if not (world_min[0] - 64 <= ox <= world_max[0] + 64
                and world_min[1] - 64 <= oy <= world_max[1] + 64
                and world_min[2] - 64 <= oz <= world_max[2] + 64):
            skipped += 1
            continue
        sx, sy, h = size_for(name)
        # m_flUniformScale scales the model about its origin; apply it to the
        # stand-in box dimensions as well so the overlay follows the real mesh.
        uniform_scale = struct.unpack_from(
            "<f", data, base + PROP_UNIFORM_SCALE_OFFSET)[0]
        if not (math.isfinite(uniform_scale)
                and SCALE_MIN <= uniform_scale <= SCALE_MAX):
            uniform_scale = 1.0
            scale_fallback += 1
        sx *= uniform_scale
        sy *= uniform_scale
        h *= uniform_scale

        color = color_for(name)
        rot = rotation_matrix(pitch, yaw, roll)

        # Baked light position: an explicitly authored lighting origin (only
        # valid when the flag is set; otherwise the bytes are garbage), else
        # the prop origin.
        light_pos = (ox, oy, oz)
        flags = struct.unpack_from("<B", data, base + PROP_FLAGS_OFFSET)[0]
        if flags & STATIC_PROP_USE_LIGHTING_ORIGIN:
            lx, ly, lz = struct.unpack_from("<3f", data, base + PROP_LIGHTING_ORIGIN_OFFSET)
            if all(math.isfinite(v) for v in (lx, ly, lz)):
                light_pos = (lx, ly, lz)
                lighting_origin_used += 1
        tint = light_grid.nearest(light_pos)
        if tint is None:
            tint = (1.0, 1.0, 1.0)
            untinted += 1
        else:
            tinted += 1
            tint_lums.append(luminance(tint))
        # COLOR_0 = palette colour * baked tint, both display-referred sRGB.
        lit_color = (color[0] * tint[0], color[1] * tint[1], color[2] * tint[2])

        # Compact entry for the web click-to-inspect lookup and for instancing
        # the real prop mesh. Name keeps the legacy short form; m is the full
        # model id (no .mdl); p is the box centre (Source coords, Z-up); s is
        # the full box size; o/a/k are the sprp origin, angles and scale.
        short = name
        for prefix in ("models/props_mining/", "models/"):
            if short.startswith(prefix):
                short = short[len(prefix):]
                break
        model_id = model_id_for(name)
        prop_entries.append({
            "n": short,
            "p": [round(ox, 1), round(oy, 1), round(oz + h / 2.0, 1)],
            "s": [sx, sy, h],
            "o": [ox, oy, oz],
            "a": [pitch, yaw, roll],
            "k": uniform_scale,
            "m": model_id,
        })

        # Models exported as real meshes are drawn by the viewer, not as boxes.
        if model_id in mesh_models:
            mesh_skipped += 1
            continue

        if is_foliage(name):
            emit_box(f_positions, f_normals, f_colors, f_indices,
                     ox, oy, oz, rot, sx, sy, h, lit_color)
            f_added += 1
            added += 1
            continue

        emit_box(p_positions, p_normals, p_colors, p_indices,
                 ox, oy, oz, rot, sx, sy, h, lit_color)
        added += 1

    # Append new meshes at the end of the BIN chunk; the existing world mesh
    # (all 72 primitives and their accessors) is left untouched.
    props_mesh = None
    if p_indices:
        material = append_material(gltf, "props_boxes")
        bin_blob = append_mesh(gltf, bin_blob, "props", material,
                               p_positions, p_normals, p_colors, p_indices)
        props_mesh = gltf["meshes"][-1]

    foliage_mesh = None
    if f_indices:
        # The viewer hides foliage by node name, so sharing the prop material
        # is fine and keeps the material table small.
        material = len(gltf["materials"]) - 1 if p_indices else append_material(
            gltf, "props_boxes")
        bin_blob = append_mesh(gltf, bin_blob, "foliage", material,
                               f_positions, f_normals, f_colors, f_indices)
        foliage_mesh = gltf["meshes"][-1]

    # Click-to-inspect metadata: attach compact prop entries as glTF extras on
    # the world node (the one whose mesh is 0). GLTFLoader surfaces
    # node.extras as object.userData.
    if prop_entries:
        main_node = next((n for n in gltf["nodes"] if n.get("mesh") == 0), None)
        if main_node is not None:
            main_node["extras"] = {"props": prop_entries}

    write_glb(glb_path, gltf, bin_blob)

    _wv, _wt = world_stats(gltf)
    print(f"props added: {added}, skipped: {skipped} (of {prop_count})", file=sys.stderr)
    print(f"props with a real mesh (boxes omitted): {mesh_skipped}", file=sys.stderr)
    if scale_fallback:
        print(f"uniform scale out of range, fell back to 1.0: {scale_fallback}",
              file=sys.stderr)
    print(f"foliage props: {f_added} (separate mesh)", file=sys.stderr)
    print(f"prop lighting: {tinted} props tinted from {len(face_lights)} "
          f"lightmapped faces, {untinted} without a nearby face (white), "
          f"{lighting_origin_used} using a lighting origin", file=sys.stderr)
    if tint_lums:
        print(f"prop tint luminance: min={min(tint_lums):.3f} "
              f"max={max(tint_lums):.3f} "
              f"mean={sum(tint_lums) / len(tint_lums):.3f}", file=sys.stderr)
    print(f"extras entries: {len(prop_entries)}", file=sys.stderr)
    print(f"world mesh untouched: {_wv} vertices, {_wt} triangles "
          f"({len(gltf['meshes'][0]['primitives'])} primitives)", file=sys.stderr)
    if props_mesh is not None:
        print(f"props mesh: {len(p_positions) // 3} vertices, {len(p_indices) // 3} triangles",
              file=sys.stderr)
    if foliage_mesh is not None:
        print(f"foliage mesh: {len(f_positions) // 3} vertices, {len(f_indices) // 3} triangles",
              file=sys.stderr)
    print(f"wrote {glb_path}: {len(gltf['meshes'])} meshes, "
          f"{len(gltf.get('materials', []))} materials", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
