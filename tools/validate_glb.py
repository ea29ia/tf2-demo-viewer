#!/usr/bin/env python3
"""Validate a GLB produced by bsp2glb.py (+ add_props_glb / add_kits_glb).

Usage: python3 tools/validate_glb.py web/public/maps/pl_upward.glb

Walks EVERY mesh and EVERY primitive (the world mesh has one primitive per
material), accumulates vertices/triangles, computes the bbox over all
POSITION accessors and checks:

  * GLB container integrity (magic/version/lengths, JSON + BIN chunks)
  * every bufferView lies inside the BIN chunk and every accessor fits its
    bufferView (catches appended chunks with stale/overlapping offsets)
  * every POSITION accessor declares min/max (both finite, min <= max)
  * POSITION and TEXCOORD_0 data contains no NaN/Inf
  * every primitive has a POSITION and a non-empty index buffer
  * every index is within its own primitive's vertex count
  * every primitive references a valid material index

Prints a per-mesh summary, the totals and the material count, then PASS/FAIL.
Exits non-zero when the map looks empty/absurd.
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys

COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
# componentType -> (struct format character, byte size)
COMPONENT_TYPES = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}


def load_glb(path: str):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        header = f.read(12)
        magic, version, total = struct.unpack("<4sII", header)
        if magic != b"glTF":
            raise SystemExit(f"bad magic {magic!r}")
        if version != 2:
            raise SystemExit(f"bad GLB version {version}")
        if total != size:
            raise SystemExit(f"header length {total} != file size {size}")
        json_blob = None
        bin_blob = None
        while f.tell() < size:
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                raise SystemExit("truncated chunk header")
            length, kind = struct.unpack("<II", chunk_header)
            data = f.read(length)
            if len(data) != length:
                raise SystemExit("truncated chunk")
            if kind == 0x4E4F534A:
                json_blob = data
            elif kind == 0x004E4942:
                bin_blob = data
    if json_blob is None or bin_blob is None:
        raise SystemExit("missing JSON or BIN chunk")
    return json.loads(json_blob.decode("utf-8")), bin_blob, size


def accessor_values(gltf, bin_blob, index):
    """Read an accessor into a list of scalars or component tuples."""
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    components = COMPONENTS[acc["type"]]
    fmt, width = COMPONENT_TYPES[acc["componentType"]]
    stride = view.get("byteStride") or width * components
    out = []
    for i in range(count):
        values = struct.unpack_from("<" + fmt * components, bin_blob, offset + i * stride)
        out.append(values[0] if components == 1 else values)
    return out


def structure_errors(gltf, bin_blob):
    """Check that bufferViews and accessors fit inside the BIN chunk."""
    errors = []
    views = gltf.get("bufferViews", [])
    for vi, view in enumerate(views):
        if view.get("buffer", 0) != 0:
            errors.append(f"bufferView[{vi}]: buffer {view.get('buffer')} != 0")
        start = view.get("byteOffset", 0)
        length = view.get("byteLength", 0)
        if start < 0 or length < 0:
            errors.append(f"bufferView[{vi}]: negative byteOffset/byteLength")
        elif start + length > len(bin_blob):
            errors.append(
                f"bufferView[{vi}]: {start}+{length} exceeds BIN chunk ({len(bin_blob)} bytes)")
    for ai, acc in enumerate(gltf.get("accessors", [])):
        vi = acc.get("bufferView")
        if vi is None or not isinstance(vi, int) or not (0 <= vi < len(views)):
            errors.append(f"accessor[{ai}]: invalid bufferView {vi}")
            continue
        if acc.get("type") not in COMPONENTS:
            errors.append(f"accessor[{ai}]: unknown type {acc.get('type')!r}")
            continue
        if acc.get("componentType") not in COMPONENT_TYPES:
            errors.append(f"accessor[{ai}]: unknown componentType {acc.get('componentType')}")
            continue
        components = COMPONENTS[acc["type"]]
        _, width = COMPONENT_TYPES[acc["componentType"]]
        element = width * components
        view = views[vi]
        stride = view.get("byteStride") or element
        count = acc.get("count", 0)
        start = acc.get("byteOffset", 0)
        need = start + (count - 1) * stride + element if count > 0 else start
        if need > view.get("byteLength", 0):
            errors.append(
                f"accessor[{ai}]: needs {need} bytes of bufferView[{vi}] "
                f"({view.get('byteLength', 0)} bytes)")
    return errors


def finite(values) -> bool:
    return all(math.isfinite(v) for v in values)


def main(argv) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <map.glb>", file=sys.stderr)
        return 2
    path = argv[1]
    gltf, bin_blob, size = load_glb(path)

    materials = gltf.get("materials", [])
    textures = gltf.get("textures", [])
    images = gltf.get("images", [])

    # Texture wiring: any material texture reference must resolve through the
    # textures/images tables (a dangling baseColorTexture crashes
    # GLTFLoader with "json.textures is undefined" at load time). A missing
    # reference (None) is normal — untextured materials are legal.
    def check_texture_ref(ref, where):
        if ref is None:
            return
        if not isinstance(ref, dict) or "index" not in ref:
            errors.append(f"{where}: malformed texture reference {ref!r}")
            return
        ti = ref["index"]
        if not isinstance(ti, int) or not (0 <= ti < len(textures)):
            errors.append(f"{where}: texture index {ti!r} out of range "
                          f"(textures: {len(textures)})")
            return
        source = textures[ti].get("source")
        if not isinstance(source, int) or not (0 <= source < len(images)):
            errors.append(f"{where}: texture {ti} source {source!r} out of range "
                          f"(images: {len(images)})")

    for mi, mat in enumerate(materials):
        where = f"material[{mi}] ({mat.get('name', '?')})"
        pbr = mat.get("pbrMetallicRoughness", {})
        check_texture_ref(pbr.get("baseColorTexture"), where + " baseColorTexture")
        check_texture_ref(pbr.get("metallicRoughnessTexture"),
                          where + " metallicRoughnessTexture")
        check_texture_ref(mat.get("normalTexture"), where + " normalTexture")
        check_texture_ref(mat.get("emissiveTexture"), where + " emissiveTexture")
    meshes = gltf.get("meshes", [])
    errors = structure_errors(gltf, bin_blob)

    total_vertices = 0
    total_tris = 0
    bbox_min = [math.inf] * 3
    bbox_max = [-math.inf] * 3
    mesh_lines = []

    for mi, mesh in enumerate(meshes):
        mesh_name = mesh.get("name", f"mesh{mi}")
        primitives = mesh.get("primitives", [])
        mesh_tris = 0
        if not primitives:
            errors.append(f"mesh[{mi}] {mesh_name}: no primitives")
        for pi, prim in enumerate(primitives):
            label = f"mesh[{mi}] {mesh_name} prim[{pi}]"
            attributes = prim.get("attributes", {})

            # Material reference
            material = prim.get("material")
            if not isinstance(material, int) or not (0 <= material < len(materials)):
                errors.append(f"{label}: invalid material index {material!r}")

            # POSITION: must exist, declare min/max, stay finite
            pos_index = attributes.get("POSITION")
            if pos_index is None:
                errors.append(f"{label}: missing POSITION")
                continue
            pos_acc = gltf["accessors"][pos_index]
            if "min" not in pos_acc or "max" not in pos_acc:
                errors.append(f"{label}: POSITION accessor {pos_index} lacks min/max")
            else:
                amin, amax = pos_acc["min"], pos_acc["max"]
                if len(amin) != 3 or len(amax) != 3:
                    errors.append(f"{label}: POSITION min/max not VEC3")
                elif not finite(amin) or not finite(amax):
                    errors.append(f"{label}: POSITION min/max not finite")
                elif any(a > b for a, b in zip(amin, amax)):
                    errors.append(f"{label}: POSITION min > max")
            positions = accessor_values(gltf, bin_blob, pos_index)
            vertex_count = len(positions)
            if vertex_count == 0:
                errors.append(f"{label}: zero vertices")
            bad = 0
            for p in positions:
                if not finite(p):
                    bad += 1
                    continue
                for k in range(3):
                    bbox_min[k] = min(bbox_min[k], p[k])
                    bbox_max[k] = max(bbox_max[k], p[k])
            if bad:
                errors.append(f"{label}: {bad} POSITION values with NaN/Inf")

            # TEXCOORD_0 (when present) must be finite
            uv_index = attributes.get("TEXCOORD_0")
            if uv_index is not None:
                uvs = accessor_values(gltf, bin_blob, uv_index)
                bad_uv = sum(1 for uv in uvs if not finite(uv))
                if bad_uv:
                    errors.append(f"{label}: {bad_uv} TEXCOORD_0 values with NaN/Inf")

            # Indices: present, non-empty, whole triangles, in range
            index_index = prim.get("indices")
            if index_index is None:
                errors.append(f"{label}: missing index buffer")
                continue
            indices = accessor_values(gltf, bin_blob, index_index)
            if not indices:
                errors.append(f"{label}: empty index buffer")
            if len(indices) % 3 != 0:
                errors.append(f"{label}: {len(indices)} indices is not a multiple of 3")
            if indices:
                worst = max(indices)
                if worst >= vertex_count:
                    errors.append(
                        f"{label}: index {worst} >= vertex count {vertex_count}")

            total_vertices += vertex_count
            mesh_tris += len(indices) // 3

        total_tris += mesh_tris
        mesh_lines.append(
            f"mesh[{mi}] {mesh_name}: {len(primitives)} primitive"
            f"{'' if len(primitives) == 1 else 's'}, {mesh_tris} tris")

    if meshes and (total_vertices == 0 or total_tris == 0):
        errors.append("no renderable geometry")

    print(f"file:      {path}")
    print(f"size:      {size} bytes ({size / (1024 * 1024):.2f} MiB)")
    for line in mesh_lines:
        print(line)
    print(f"vertices:  {total_vertices}")
    print(f"triangles: {total_tris}")
    print(f"materials: {len(materials)}")
    if total_vertices:
        print(f"bbox min:  ({bbox_min[0]:.3f}, {bbox_min[1]:.3f}, {bbox_min[2]:.3f})")
        print(f"bbox max:  ({bbox_max[0]:.3f}, {bbox_max[1]:.3f}, {bbox_max[2]:.3f})")

    # Threshold re-calibrated after the ear-clip fix in bsp2glb.py: the old
    # fan triangulation emitted ~21k degenerate sliver triangles, inflating
    # the count above 65k. A healthy upward build has ~44k world triangles
    # (plus props/foliage/kits, ~58k total).
    if total_tris < 35000:
        errors.append(f"too few triangles: {total_tris} < 35000")
    if total_vertices:
        if abs(bbox_min[0]) > 12000 or abs(bbox_max[0]) > 12000:
            errors.append("x bbox outside +/-12000")
        if abs(bbox_min[1]) > 12000 or abs(bbox_max[1]) > 12000:
            errors.append("y bbox outside +/-12000")
        if bbox_min[2] < -2500 or bbox_max[2] > 3500:
            errors.append("z bbox outside [-2500, 3500]")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
