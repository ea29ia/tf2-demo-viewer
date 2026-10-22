#!/usr/bin/env python3
"""Validate a GLB produced by bsp2glb.py.

Usage: python3 tools/validate_glb.py web/public/maps/pl_upward.glb

Prints triangle count, vertex count, bounding box and file size.  Exits
non-zero when the map looks empty/absurd.
"""
from __future__ import annotations

import json
import os
import struct
import sys


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
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    component = acc["componentType"]
    type_name = acc["type"]
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[type_name]
    fmt = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}[component]
    width = struct.calcsize(fmt)
    stride = view.get("byteStride") or width * components
    out = []
    for i in range(count):
        values = struct.unpack_from("<" + fmt * components, bin_blob, offset + i * stride)
        out.append(values[0] if components == 1 else values)
    return out


def main(argv) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <map.glb>", file=sys.stderr)
        return 2
    path = argv[1]
    gltf, bin_blob, size = load_glb(path)

    primitives = gltf["meshes"][0]["primitives"]
    if len(primitives) != 1:
        print(f"warning: {len(primitives)} primitives (expected 1)")
    prim = primitives[0]
    positions = accessor_values(gltf, bin_blob, prim["attributes"]["POSITION"])
    indices = accessor_values(gltf, bin_blob, prim["indices"])

    vertex_count = len(positions)
    tri_count = len(indices) // 3
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    bbox_min = (min(xs), min(ys), min(zs))
    bbox_max = (max(xs), max(ys), max(zs))

    print(f"file:      {path}")
    print(f"size:      {size} bytes ({size / (1024 * 1024):.2f} MiB)")
    print(f"vertices:  {vertex_count}")
    print(f"triangles: {tri_count}")
    print(f"bbox min:  ({bbox_min[0]:.3f}, {bbox_min[1]:.3f}, {bbox_min[2]:.3f})")
    print(f"bbox max:  ({bbox_max[0]:.3f}, {bbox_max[1]:.3f}, {bbox_max[2]:.3f})")

    errors = []
    # Threshold re-calibrated after the ear-clip fix in bsp2glb.py: the old
    # fan triangulation emitted ~21k degenerate sliver triangles, inflating
    # the count above 65k. A healthy upward build has ~44k triangles.
    if tri_count < 35000:
        errors.append(f"too few triangles: {tri_count} < 35000")
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
