#!/usr/bin/env python3
"""Append brush-model geometry (func_door spawn gates) and prop_dynamic
resupply lockers to a textured map GLB.

Upward's spawn doors are func_door brush models (entity lump `model` = `*N`)
plus a static door visual prop; only the brush carries the door's actual
geometry (METALDOOR01_192 / IBEAM001B faces). bsp2glb.py emits worldspawn
geometry only, so the door slabs were invisible. This tool:

1. parses the entity lump for func_door / func_door_rotating entities,
2. builds one mesh per (model, material) from the BSP faces exactly like
   bsp2glb.build() does (same texinfo UVs, palette color, white lightmap
   cell — the doors are sealed rooms, VRAD never lights them),
3. appends them to the GLB as a new mesh + nodes (append-only: existing
   bufferViews/accessors/materials keep their indices),
4. reuses an existing GLB material when the texdata name matches one already
   embedded by add_textures_glb.py (PROPS/METALDOOR01_192, METAL/IBEAM001B),
   and only falls back to a new material when nothing matches,
5. appends prop_dynamic entities whose model exists in the props manifest
   (resupply_locker) to the extras.props table with the standard
   o/a/k/m fields, so the existing viewer prop loader instances them.

Usage: python3 tools/add_brushmodels_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb web/public/models/props/manifest.json
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import bsp2glb  # noqa: E402

# Brush models whose faces are the door visuals. The func_tracktrain (*10)
# has zero faces (the cart visual is props). Doors keep their CLOSED state;
# opening animation is a replay-data feature (TODO).
FACE_STRUCT = bsp2glb.FACE_STRUCT
TEXINFO_STRUCT = bsp2glb.TEXINFO_STRUCT


def load_glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    json_len = struct.unpack_from("<II", data, 12)[0]
    gltf = json.loads(data[20:20 + json_len])
    bin_off = 20 + json_len
    bin_len = struct.unpack_from("<II", data, bin_off)[0]
    blob = bytearray(data[bin_off + 8: bin_off + 8 + bin_len])
    return gltf, blob


def write_glb(path: str, gltf: dict, blob: bytes) -> None:
    json_blob = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_blob += b" " * ((4 - len(json_blob) % 4) % 4)
    blob = blob + b"\0" * ((4 - len(blob) % 4) % 4)
    total = 12 + 8 + len(json_blob) + 8 + len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(blob), 0x004E4942))
        f.write(blob)


def parse_entities(bsp: bsp2glb.Bsp) -> list[dict]:
    text = bsp.lump(bsp2glb.LUMP_ENTITIES).decode("utf-8", errors="replace")
    out = []
    for block in re.findall(r"\{([^{}]*)\}", text):
        ent: dict[str, str] = {}
        for key, value in re.findall(r'"([^"]+)"\s*"([^"]*)"', block):
            ent[key] = value
        if ent.get("classname"):
            out.append(ent)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} <input.bsp> <map.glb> <props_manifest.json>",
              file=sys.stderr)
        return 2
    bsp_path, glb_path, manifest_path = argv[1], argv[2], argv[3]

    bsp = bsp2glb.Bsp(bsp_path)
    gltf, blob = load_glb(glb_path)

    material_names: dict[str, int] = {}
    for index, mat in enumerate(gltf.get("materials", [])):
        texdata = (mat.get("extras") or {}).get("texdata")
        if texdata:
            material_names.setdefault(texdata, index)

    new_primitives: list[dict] = []
    new_nodes: list[dict] = []
    doors_added = 0

    # --- func_door brush geometry -----------------------------------------
    entities = parse_entities(bsp)
    door_models: dict[int, dict] = {}
    for ent in entities:
        classname = ent.get("classname", "")
        if classname not in ("func_door", "func_door_rotating"):
            continue
        model = ent.get("model", "")
        match = re.fullmatch(r"\*(\d+)", model)
        if not match:
            continue
        model_index = int(match.group(1))
        entry = door_models.setdefault(model_index, {
            "class": classname,
            "origin": [0.0, 0.0, 0.0],
            "names": [],
        })
        entry["names"].append(ent.get("targetname", ""))
        origin = ent.get("origin")
        if origin:
            entry["origin"] = [float(v) for v in origin.split()]

    # One combined builder per texdata name across all door models keeps the
    # primitive count low; per-door node transforms stay identity because the
    # faces are already in world space.
    door_builders: dict[str, bsp2glb.MeshBuilder] = {}
    doors: list[int] = sorted(door_models)
    if doors:
        planes = bsp.planes()
        vertices = bsp.vertices()
        edges = bsp.edges()
        surfedges = bsp.surfedges()
        texinfo_raw = bsp.lump(bsp2glb.LUMP_TEXINFO)
        tex_names = bsp.texture_names()
        texdata = bsp.texdata_entries()

        models_raw = bsp.lump(14)
        for model_index in doors:
            if model_index * 48 + 48 > len(models_raw):
                continue
            vals = struct.unpack_from("<9fi2i", models_raw, model_index * 48)
            first_face, num_faces = vals[10], vals[11]
            for fi in range(first_face, first_face + num_faces):
                face = FACE_STRUCT.unpack_from(bsp.lump(7), fi * 56)
                texinfo_index = face[5]
                if texinfo_index * 72 + 72 > len(texinfo_raw):
                    continue
                ti_vals = TEXINFO_STRUCT.unpack_from(
                    texinfo_raw, texinfo_index * TEXINFO_STRUCT.size)
                texdata_index = ti_vals[-1]
                if not (0 <= texdata_index < len(texdata)):
                    continue
                name_index, tex_w, tex_h = texdata[texdata_index]
                name = (tex_names[name_index]
                        if 0 <= name_index < len(tex_names) else "")
                if not name:
                    continue
                flags = ti_vals[bsp2glb.TEXINFO_FLAGS_INDEX]
                if flags & bsp2glb.SKIP_FACE_FLAGS:
                    continue
                builder = door_builders.get(name)
                if builder is None:
                    builder = door_builders[name] = bsp2glb.MeshBuilder()

                # Face polygon in world space (same as bsp2glb.face_polygon).
                first_edge, num_edges = face[3], face[4]
                if num_edges < 3:
                    continue
                poly = []
                ok = True
                for j in range(num_edges):
                    se = surfedges[first_edge + j]
                    edge_index = se if se >= 0 else -se
                    if edge_index >= len(edges) // 2:
                        ok = False
                        break
                    e0 = edges[edge_index * 2]
                    e1 = edges[edge_index * 2 + 1]
                    vi = e0 if se >= 0 else e1
                    if vi >= len(vertices) // 3:
                        ok = False
                        break
                    poly.append((vertices[vi * 3], vertices[vi * 3 + 1],
                                 vertices[vi * 3 + 2]))
                if not ok or len(poly) < 3:
                    continue

                plane = planes[face[0]]
                normal = (plane[0], plane[1], plane[2])
                if face[1] == 1:
                    normal = (-normal[0], -normal[1], -normal[2])
                normal = bsp2glb.normalize(normal)
                s_vec = (ti_vals[0], ti_vals[1], ti_vals[2], ti_vals[3])
                t_vec = (ti_vals[4], ti_vals[5], ti_vals[6], ti_vals[7])
                width = float(tex_w or 1)
                height = float(tex_h or 1)

                def uv_of(p, s_vec=s_vec, t_vec=t_vec, width=width, height=height):
                    return (
                        (p[0] * s_vec[0] + p[1] * s_vec[1] + p[2] * s_vec[2]
                         + s_vec[3]) / width,
                        (p[0] * t_vec[0] + p[1] * t_vec[1] + p[2] * t_vec[2]
                         + t_vec[3]) / height,
                    )

                white = (0.004, 0.004)  # reserved white cell centre (4px atlas)
                cleaned = bsp2glb.clean_polygon(poly, normal)
                if len(cleaned) < 3:
                    continue
                base = builder.vertex_count
                color = bsp2glb.palette_color(name)
                for p in cleaned:
                    builder.add_vertex(p, normal, color, uv_of(p), white)
                for tri in bsp2glb.ear_clip(cleaned, normal):
                    builder.add_triangle(base + tri[0], base + tri[1],
                                         base + tri[2], normal)
                doors_added += 1

    # --- append door geometry to the GLB -----------------------------------
    cursor = len(blob)

    def append_view(data: bytes, target: int) -> int:
        nonlocal cursor
        padded = data + b"\0" * ((4 - len(data) % 4) % 4)
        view = {
            "buffer": 0,
            "byteOffset": cursor,
            "byteLength": len(data),
        }
        if target:
            view["target"] = target
        gltf.setdefault("bufferViews", []).append(view)
        blob.extend(padded)
        cursor += len(padded)
        return len(gltf["bufferViews"]) - 1

    def append_accessor(view: int, count: int, kind: str,
                        comp: int, minmax=None) -> int:
        acc = {
            "bufferView": view,
            "componentType": comp,
            "count": count,
            "type": kind,
        }
        if minmax:
            acc["min"], acc["max"] = minmax
        gltf.setdefault("accessors", []).append(acc)
        return len(gltf["accessors"]) - 1

    mesh_index = len(gltf.get("meshes", []))
    meshes = gltf.setdefault("meshes", [])
    mesh: dict = {"name": "brushmodels", "primitives": new_primitives}
    for name in sorted(door_builders):
        builder = door_builders[name]
        if builder.vertex_count == 0 or not builder.indices:
            continue
        pos_view = append_view(builder.positions.tobytes(), 34962)
        nrm_view = append_view(builder.normals.tobytes(), 34962)
        col_view = append_view(builder.colors.tobytes(), 34962)
        uv_view = append_view(builder.uvs.tobytes(), 34962)
        lmap_view = append_view(builder.lmap.tobytes(), 34962)
        idx_view = append_view(builder.indices.tobytes(), 34963)

        pos_acc = append_accessor(pos_view, builder.vertex_count, "VEC3",
                                  5126, (builder.min, builder.max))
        nrm_acc = append_accessor(nrm_view, builder.vertex_count, "VEC3", 5126)
        col_acc = append_accessor(col_view, builder.vertex_count, "VEC3", 5126)
        uv_acc = append_accessor(uv_view, builder.vertex_count, "VEC2", 5126)
        lmap_acc = append_accessor(lmap_view, builder.vertex_count, "VEC2", 5126)
        idx_acc = append_accessor(idx_view, len(builder.indices), "SCALAR", 5125)

        mat_index = material_names.get(name)
        if mat_index is None:
            # Unknown material: add a neutral placeholder (should not happen
            # for upward: both door materials are already embedded).
            mat_index = len(gltf["materials"])
            gltf.setdefault("materials", []).append({
                "name": name,
                "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0],
                                          "metallicFactor": 0.0,
                                          "roughnessFactor": 0.9},
                "extras": {"texdata": name},
            })
            material_names[name] = mat_index
        new_primitives.append({
            "attributes": {
                "POSITION": pos_acc,
                "NORMAL": nrm_acc,
                "COLOR_0": col_acc,
                "TEXCOORD_0": uv_acc,
                "TEXCOORD_1": lmap_acc,
            },
            "indices": idx_acc,
            "material": mat_index,
            "extras": {"texdata": name},
        })
    meshes.append(mesh)

    # A separate top-level node so the viewer can find/toggle the doors.
    node_index = len(gltf.get("nodes", []))
    gltf.setdefault("nodes", []).append({
        "name": "brushmodels",
        "mesh": mesh_index,
    })
    scene = gltf.get("scenes", [{}])[0]
    scene.setdefault("nodes", []).append(node_index)
    print(f"func_door faces appended: {doors_added} "
          f"({len(new_primitives)} primitives)", file=sys.stderr)

    # --- prop_dynamic resupply lockers -------------------------------------
    manifest: dict = {}
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    models_table = manifest.get("models", {})

    node0 = gltf["nodes"][0]
    extras = node0.setdefault("extras", {})
    props = extras.setdefault("props", [])
    lockers = 0
    for ent in entities:
        if ent.get("classname") != "prop_dynamic":
            continue
        model = ent.get("model", "").replace("\\", "/")
        if not model.endswith(".mdl"):
            continue
        model_id = model[:-4]
        if model_id not in models_table:
            continue
        origin = ent.get("origin")
        angles = ent.get("angles") or "0 0 0"
        if not origin:
            continue
        o = [float(v) for v in origin.split()]
        pitch, yaw, roll = (float(v) for v in angles.split()[:3])
        props.append({
            "n": model,
            "o": o,
            "a": [pitch, yaw, roll],
            "k": 1.0,
            "m": model_id,
        })
        lockers += 1
    print(f"prop_dynamic entities appended to extras: {lockers}",
          file=sys.stderr)

    write_glb(glb_path, gltf, bytes(blob))
    print(f"wrote {glb_path}: BIN {len(blob) / (1024 * 1024):.2f} MiB",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
