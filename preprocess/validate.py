#!/usr/bin/env python3
"""Validate and summarise a dem2replay `replay.bin`.

Usage: python3 validate.py <replay.bin> [meta.json]

Exits 0 only when the whole binary is structurally valid (magic, version,
meta JSON, frame and record sizes, finite floats, sane team/class values and
non-decreasing ticks). Prints frame_count, max players, tick range, bbox, and
one sample record.
"""

import json
import math
import struct
import sys

MAGIC = b"TF2R"
VERSION = 4
RECORD_SIZE = 27
PROJ_RECORD_SIZE = 17
HURT_RECORD_SIZE = 15
BUILDING_RECORD_SIZE = 15  # 2+1+1+1+1+1+2+2+2+2; a spec draft said 20, the field list sums to 15
KIT_RECORD_SIZE = 10
RECORD = struct.Struct("<HBBBHfffff")
BUILDING = struct.Struct("<HBBBBHBhhh")  # entity,team,kind,flags,level,health,angle,x,y,z(/4)
KIT = struct.Struct("<HBBhhh")  # index,kind,flags,x,y,z(/4)
MAX_BUILDINGS_PER_FRAME = 32  # dumper cap


def fail(message):
    print(f"INVALID: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse(path):
    with open(path, "rb") as handle:
        data = handle.read()

    if len(data) < 16:
        fail("file too short for header")
    if data[:4] != MAGIC:
        fail(f"bad magic {data[:4]!r}")
    version, meta_len = struct.unpack_from("<II", data, 4)
    if version != VERSION:
        fail(f"unsupported version {version}")
    pos = 12
    if pos + meta_len + 4 > len(data):
        fail("meta_len exceeds file size")
    raw_meta = data[pos : pos + meta_len]
    pos += meta_len
    try:
        meta = json.loads(raw_meta.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"meta JSON: {exc}")
    if not isinstance(meta, dict):
        fail("meta is not a JSON object")

    (frame_count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    if frame_count == 0:
        fail("frame_count is 0")

    frames = []
    max_players = 0
    max_buildings = 0
    max_kits = 0
    min_tick = None
    max_tick = None
    bbox = [math.inf, math.inf, math.inf, -math.inf, -math.inf, -math.inf]
    sample = None
    last_tick = None

    for index in range(frame_count):
        if pos + 6 > len(data):
            fail(f"truncated frame header at frame {index}")
        tick, count = struct.unpack_from("<IH", data, pos)
        pos += 6
        if last_tick is not None and tick < last_tick:
            fail(f"tick went backwards at frame {index}: {tick} < {last_tick}")
        last_tick = tick
        min_tick = tick if min_tick is None else min(min_tick, tick)
        max_tick = tick if max_tick is None else max(max_tick, tick)
        max_players = max(max_players, count)

        need = count * RECORD_SIZE
        if pos + need > len(data):
            fail(f"truncated records at frame {index}")
        for _ in range(count):
            (entity, team, cls, flags, health, x, y, z, yaw, pitch) = RECORD.unpack_from(
                data, pos
            )
            pos += RECORD_SIZE
            if team not in (2, 3):
                fail(f"frame {index}: bad team {team}")
            if cls > 9:
                fail(f"frame {index}: bad class {cls}")
            if flags & ~0x0F:
                fail(f"frame {index}: unknown flag bits {flags:#x}")
            if health > 1000:
                fail(f"frame {index}: implausible health {health}")
            for value in (x, y, z, yaw, pitch):
                if not math.isfinite(value):
                    fail(f"frame {index}: non-finite float")
            bbox[0] = min(bbox[0], x)
            bbox[1] = min(bbox[1], y)
            bbox[2] = min(bbox[2], z)
            bbox[3] = max(bbox[3], x)
            bbox[4] = max(bbox[4], y)
            bbox[5] = max(bbox[5], z)
            if sample is None:
                sample = {
                    "tick": tick,
                    "entity": entity,
                    "team": team,
                    "class": cls,
                    "flags": flags,
                    "health": health,
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "z": round(z, 3),
                    "yaw": round(yaw, 3),
                    "pitch": round(pitch, 3),
                }
        if version >= 2:
            if pos + 2 > len(data):
                fail(f"truncated projectile header at frame {index}")
            (proj_count,) = struct.unpack_from("<H", data, pos)
            pos += 2
            if proj_count > 200:
                fail(f"frame {index}: bad projectile count {proj_count}")
            need = proj_count * PROJ_RECORD_SIZE
            if pos + need > len(data):
                fail(f"truncated projectiles at frame {index}")
            pos += need

            if pos + 2 > len(data):
                fail(f"truncated hurt header at frame {index}")
            (hurt_count,) = struct.unpack_from("<H", data, pos)
            pos += 2
            if hurt_count > 100:
                fail(f"frame {index}: bad hurt count {hurt_count}")
            need = hurt_count * HURT_RECORD_SIZE
            if pos + need > len(data):
                fail(f"truncated hurt records at frame {index}")
            pos += need

        if version >= 4:
            if pos + 2 > len(data):
                fail(f"truncated building header at frame {index}")
            (building_count,) = struct.unpack_from("<H", data, pos)
            pos += 2
            if building_count > MAX_BUILDINGS_PER_FRAME:
                fail(
                    f"frame {index}: bad building count {building_count}"
                    f" (dumper cap {MAX_BUILDINGS_PER_FRAME})"
                )
            need = building_count * BUILDING_RECORD_SIZE
            if pos + need > len(data):
                fail(f"truncated building records at frame {index}")
            for _ in range(building_count):
                (
                    entity,
                    team,
                    kind,
                    flags,
                    level,
                    health,
                    angle,
                    x,
                    y,
                    z,
                ) = BUILDING.unpack_from(data, pos)
                pos += BUILDING_RECORD_SIZE
                if team not in (2, 3):
                    fail(f"frame {index}: bad building team {team}")
                if kind > 3:
                    fail(f"frame {index}: bad building kind {kind}")
                if flags & ~0x07:
                    fail(f"frame {index}: unknown building flag bits {flags:#x}")
                if level > 3:
                    fail(f"frame {index}: bad building level {level}")
                if x == 0 and y == 0 and z == 0:
                    fail(f"frame {index}: building {entity} at zero position")
            max_buildings = max(max_buildings, building_count)

            if pos + 2 > len(data):
                fail(f"truncated kit header at frame {index}")
            (kit_count,) = struct.unpack_from("<H", data, pos)
            pos += 2
            if kit_count > 1000:
                fail(f"frame {index}: bad kit count {kit_count}")
            need = kit_count * KIT_RECORD_SIZE
            if pos + need > len(data):
                fail(f"truncated kit records at frame {index}")
            for kit_index in range(kit_count):
                index_, kind, flags, x, y, z = KIT.unpack_from(data, pos)
                pos += KIT_RECORD_SIZE
                if kind > 5:
                    fail(f"frame {index}: bad kit kind {kind}")
                if flags & ~0x01:
                    fail(f"frame {index}: unknown kit flag bits {flags:#x}")
                if kit_index == 0:
                    first_kit_index = index_
                    kit_kinds = set()
                else:
                    if index_ <= first_kit_index:
                        fail(f"frame {index}: kit indexes not increasing")
                    first_kit_index = index_
                kit_kinds.add(kind)
            if kit_count > 0 and len(kit_kinds) < 2:
                fail(f"frame {index}: only one kit kind present ({kit_kinds})")
            max_kits = max(max_kits, kit_count)

        frames.append((tick, count))

    if pos != len(data):
        fail(f"{len(data) - pos} trailing bytes after {frame_count} frames")
    if "frames" in meta and meta["frames"] != frame_count:
        fail(f"meta frames={meta['frames']} != frame_count={frame_count}")

    return meta, frames, max_players, max_buildings, max_kits, (min_tick, max_tick), bbox, sample


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = argv[1]
    meta, frames, max_players, max_buildings, max_kits, tick_range, bbox, sample = parse(path)

    print(f"file:        {path}")
    print(f"map:         {meta.get('map')}")
    print(f"nick:        {meta.get('nick')}")
    print(f"frame_count: {len(frames)}")
    print(f"max_players: {max_players}")
    print(f"max_buildings: {max_buildings}")
    print(f"max_kits:    {max_kits}")
    print(f"tick_range:  {tick_range[0]}..{tick_range[1]}")
    print(
        "bbox:        x[{:.3f},{:.3f}] y[{:.3f},{:.3f}] z[{:.3f},{:.3f}]".format(
            bbox[0], bbox[3], bbox[1], bbox[4], bbox[2], bbox[5]
        )
    )
    print(f"users:       {len(meta.get('users', []))}")
    print(f"kills:       {len(meta.get('kills', []))}")
    print("sample:      " + json.dumps(sample, sort_keys=True))

    if len(argv) >= 3:
        with open(argv[2], "r", encoding="utf-8") as handle:
            disk_meta = json.load(handle)
        if disk_meta != meta:
            fail("binary meta does not match meta.json")

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
