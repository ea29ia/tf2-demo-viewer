#!/usr/bin/env python3
"""Append prop_dynamic entities (resupply lockers etc.) to a textured map GLB.

Some functional props are NOT in the static-prop lump: TF2's resupply
lockers are prop_dynamic entities paired with func_regenerate volumes.
bsp2glb/add_props_glb never see them, so nothing rendered at their
positions. This tool parses the entity lump, finds prop_dynamic entities
whose model exists in the props manifest, and appends them to the
extras.props table (node 0) with the standard o/a/k/m fields — the
existing viewer prop loader instances them like any static prop.

History: an earlier version also emitted func_door brush-model faces as a
`brushmodels` mesh. That was reverted (2026-10-23, user eyeball): the brush
models are the doors' collision blockers, SOLID boxes textured METALDOOR01/
IBEAM, hiding the actual visual door props (door_slide_large,
spawnroom_door_left, door_grate_*) already exported as static-prop meshes.
The props alone are the correct gate visuals; opening animation is a
replay-data feature (TODO).

Usage: python3 tools/add_brushmodels_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb web/public/models/props/manifest.json
"""
from __future__ import annotations

import json
import re
import struct
import sys

import bsp2glb  # noqa: F401 (keeps the bsp2glb import contract for callers)


def load_glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    json_len = struct.unpack_from("<II", data, 12)[0]
    gltf = json.loads(data[20:20 + json_len])
    return gltf


def write_glb(path: str, gltf: dict) -> None:
    json_blob = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_blob += b" " * ((4 - len(json_blob) % 4) % 4)
    # GLB layout is unchanged apart from the JSON chunk; keep the BIN chunk
    # verbatim by re-reading the original file.
    with open(path, "rb") as f:
        data = f.read()
    bin_len = struct.unpack_from("<II", data, 20 + json_len if False else 0, 0) \
        if False else None
    # Parse the original chunks properly.
    pos = 12
    bin_chunk = b""
    while pos < len(data):
        length, kind = struct.unpack_from("<II", data, pos)
        pos += 8
        chunk = data[pos:pos + length]
        pos += length
        if kind == 0x004E4942:
            bin_chunk = chunk
    total = 12 + 8 + len(json_blob) + 8 + len(bin_chunk)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_chunk), 0x004E4942))
        f.write(bin_chunk)


def parse_entities(bsp) -> list[dict]:
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
    gltf = load_glb(glb_path)

    with open(manifest_path, "r", encoding="utf-8") as fh:
        models_table = json.load(fh).get("models", {})

    node0 = gltf["nodes"][0]
    extras = node0.setdefault("extras", {})
    props = extras.setdefault("props", [])
    appended = 0
    for ent in parse_entities(bsp):
        if ent.get("classname") != "prop_dynamic":
            continue
        model = ent.get("model", "").replace("\\", "/")
        if not model.endswith(".mdl"):
            continue
        model_id = model[:-4]
        if model_id not in models_table:
            continue
        origin = ent.get("origin")
        if not origin:
            continue
        angles = ent.get("angles") or "0 0 0"
        o = [float(v) for v in origin.split()]
        pitch, yaw, roll = (float(v) for v in angles.split()[:3])
        # Empirical fix (user eyeball): resupply locker models face the wall
        # with the plain Source QAngle. Rotate this model batch by 180 deg
        # yaw so the front points into the room (away from its
        # func_regenerate wall).
        if "resupply" in model_id.lower():
            yaw = (yaw + 180.0) % 360.0
        props.append({
            "n": model,
            "o": o,
            "a": [pitch, yaw, roll],
            "k": 1.0,
            "m": model_id,
        })
        appended += 1
    print(f"prop_dynamic entities appended to extras: {appended}",
          file=sys.stderr)

    write_glb(glb_path, gltf)
    print(f"wrote {glb_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
