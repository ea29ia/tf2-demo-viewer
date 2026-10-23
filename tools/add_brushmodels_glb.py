#!/usr/bin/env python3
"""Wall-snapped prop placement + prop_dynamic extras for the map GLB.

Two jobs (both append-only on the GLB JSON; the BIN chunk is untouched):

1. prop_dynamic entities whose model exists in the props manifest (TF2's
   resupply lockers and friends) are appended to extras.props with the
   standard o/a/k/m fields, so the existing viewer prop loader instances
   them like any static prop.

2. Wall-facing orientation for those entities: the authored Source QAngle
   makes locker models face the wall. The tool raycasts the BSP brush
   geometry in 16 horizontal directions from the prop origin, finds the
   nearest wall within a search radius, and rotates the prop to face AWAY
   from that wall (back against the wall, front into the room).

History: an even earlier version of this tool also emitted func_door
brush-model faces as a `brushmodels` mesh. That was wrong twice over: the
brushes are the doors' collision blockers (solid METALDOOR01/IBEAM boxes)
duplicating the real door prop visuals, and the door panels themselves
vanish when the round starts — the gate visuals are the static door props
(door_slide_large / spawnroom_door_left / door_grate_*). The brush pass was
removed; the static door props render the gates.

Usage: python3 tools/add_brushmodels_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb web/public/models/props/manifest.json
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys

import bsp2glb  # noqa: F401 (Bsp lump access)

FACE_STRUCT = bsp2glb.FACE_STRUCT
TEXINFO_STRUCT = bsp2glb.TEXINFO_STRUCT

# Raycast walls: search radius in Source units. Larger than any locker-to-
# wall distance on upward; small enough to never cross a room.
WALL_SEARCH_RADIUS = 96.0
# Wall hit qualifies only when the surface is roughly vertical (a floor or
# ceiling must not define the "back" direction).
WALL_MIN_VERTICAL = 0.7
# Number of horizontal ray directions (16 = every 22.5 deg).
RAY_COUNT = 16


def load_glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    json_len = struct.unpack_from("<II", data, 12)[0]
    gltf = json.loads(data[20:20 + json_len])
    return gltf


def write_glb(path: str, gltf: dict) -> None:
    with open(path, "rb") as f:
        data = f.read()
    pos = 12
    bin_chunk = b""
    while pos < len(data):
        length, kind = struct.unpack_from("<II", data, pos)
        pos += 8
        chunk = data[pos:pos + length]
        pos += length
        if kind == 0x004E4942:
            bin_chunk = chunk
    json_blob = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_blob += b" " * ((4 - len(json_blob) % 4) % 4)
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


class WallIndex:
    """Brute-force ray vs world-brush-face intersection index.

    Built from worldspawn brush faces (model 0) only: func_door brushes and
    detail props are excluded so the search finds ROOM walls, not the door
    panel the locker stands next to. Faces are pre-filtered to vertical
    surfaces to keep the candidate list small.
    """

    def __init__(self, bsp):
        planes = bsp.planes()
        vertices = bsp.vertices()
        edges = bsp.edges()
        surfedges = bsp.surfedges()
        texinfo_raw = bsp.lump(bsp2glb.LUMP_TEXINFO)
        models_raw = bsp.lump(14)
        world_first, world_count = 0, 0
        if len(models_raw) >= 48:
            vals = struct.unpack_from("<9fi2i", models_raw, 0)
            world_first, world_count = vals[10], vals[11]
        faces_raw = bsp.lump(7)
        self.tris: list[tuple[tuple, tuple, tuple, tuple]] = []
        for fi in range(world_first, world_first + world_count):
            face = FACE_STRUCT.unpack_from(faces_raw, fi * 56)
            texinfo_index = face[5]
            if texinfo_index * 72 + 72 > len(texinfo_raw):
                continue
            ti_vals = TEXINFO_STRUCT.unpack_from(
                texinfo_raw, texinfo_index * TEXINFO_STRUCT.size)
            flags = ti_vals[bsp2glb.TEXINFO_FLAGS_INDEX]
            if flags & bsp2glb.SKIP_FACE_FLAGS:
                continue
            plane = planes[face[0]]
            normal = (plane[0], plane[1], plane[2])
            if face[1] == 1:
                normal = (-normal[0], -normal[1], -normal[2])
            if abs(normal[2]) > 1.0 - WALL_MIN_VERTICAL:
                continue  # floor/ceiling: not a wall
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
            cleaned = bsp2glb.clean_polygon(poly, bsp2glb.normalize(normal))
            for tri in bsp2glb.ear_clip(cleaned, bsp2glb.normalize(normal)):
                a, b, c = (cleaned[tri[0]], cleaned[tri[1]], cleaned[tri[2]])
                self.tris.append((a, b, c, bsp2glb.normalize(normal)))

    def raycast(self, origin: tuple[float, float, float],
                direction: tuple[float, float, float],
                max_dist: float) -> float | None:
        """Distance to the closest triangle hit, or None."""
        best: float | None = None
        ox, oy, oz = origin
        dx, dy, dz = direction
        for a, b, c, normal in self.tris:
            denom = normal[0] * dx + normal[1] * dy + normal[2] * dz
            # Two-sided: the ray may hit either side of a wall face.
            if abs(denom) < 1e-9:
                continue
            # Plane distance along the ray.
            px, py, pz = a
            t = ((px - ox) * normal[0] + (py - oy) * normal[1]
                 + (pz - oz) * normal[2]) / denom
            if t <= 0.5 or t >= max_dist:
                continue
            hx, hy, hz = ox + dx * t, oy + dy * t, oz + dz * t
            if bsp2glb._point_in_tri(
                    (hx, hy, hz), a, b, c, 0, 1):
                if best is None or t < best:
                    best = t
        return best


def nearest_wall_yaw(index: WallIndex,
                     origin: tuple[float, float, float]) -> float | None:
    """Yaw (degrees, Source convention) pointing AWAY from the nearest wall.

    Casts RAY_COUNT horizontal rays; the wall direction is the ray with the
    shortest hit distance. The prop must face the OPPOSITE direction.
    Source yaw 0 = +X, 90 = +Y, so facing +X means yaw = 0.
    """
    best_dist = None
    best_dir = None
    step = 360.0 / RAY_COUNT
    for i in range(RAY_COUNT):
        yaw = i * step
        rad = math.radians(yaw)
        direction = (math.cos(rad), math.sin(rad), 0.0)
        dist = index.raycast(origin, direction, WALL_SEARCH_RADIUS)
        if dist is not None and (best_dist is None or dist < best_dist):
            best_dist = dist
            best_dir = yaw
    if best_dir is None:
        return None
    return (best_dir + 180.0) % 360.0


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

    entities = [e for e in parse_entities(bsp)
                if e.get("classname") == "prop_dynamic"]
    candidates = []
    for ent in entities:
        model = ent.get("model", "").replace("\\", "/")
        if not model.endswith(".mdl"):
            continue
        model_id = model[:-4]
        if model_id not in models_table:
            continue
        origin = ent.get("origin")
        if not origin:
            continue
        candidates.append((ent, model_id, [float(v) for v in origin.split()]))
    if not candidates:
        print("no prop_dynamic entities with manifest models", file=sys.stderr)
        return 0

    index = WallIndex(bsp)
    print(f"wall index: {len(index.tris)} vertical triangles", file=sys.stderr)

    appended = 0
    for ent, model_id, o in candidates:
        angles = ent.get("angles") or "0 0 0"
        pitch, yaw, roll = (float(v) for v in angles.split()[:3])
        # Wall-snap ONLY the resupply lockers: signs/billboards are wall-
        # mounted with correct authored angles, floor-standing lockers are
        # the batch the user flagged as facing the wall.
        if "resupply" in model_id.lower():
            wall_yaw = nearest_wall_yaw(index, (o[0], o[1], o[2] + 32.0))
            if wall_yaw is not None:
                yaw = wall_yaw
        props.append({
            "n": ent.get("model"),
            "o": o,
            "a": [pitch, yaw, roll],
            "k": 1.0,
            "m": model_id,
        })
        appended += 1
        if "resupply" in model_id.lower():
            print(f"  {model_id} at ({o[0]:.0f},{o[1]:.0f},{o[2]:.0f}): "
                  f"authored yaw={float(angles.split()[1]) if angles else 0:.0f} "
                  f"-> wall-facing yaw={yaw:.0f}", file=sys.stderr)
    print(f"prop_dynamic entities appended to extras: {appended}",
          file=sys.stderr)

    write_glb(glb_path, gltf)
    print(f"wrote {glb_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
