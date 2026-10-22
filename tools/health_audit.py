#!/usr/bin/env python3
"""Health audit for dem2replay replay.bin (v3).

Walks the replay, finds respawn windows (a player transitioning dead -> alive)
and compares the maximum health observed within 200 ticks of the respawn
against the class base health. Overheal counts as OK; only a window whose max
health is below 0.95 * base is reported as a MISMATCH.

Class ids follow tf-demo-parser's `Class` enum, which matches TF2's
TF_CLASS_* constants:
    1 scout 125, 2 sniper 125, 3 soldier 200, 4 demoman 175, 5 medic 150,
    6 heavy 300, 7 pyro 175, 8 spy 125, 9 engineer 125.

Usage: python3 health_audit.py <replay.bin>
"""

import collections
import struct
import sys

MAGIC = b"TF2R"
VERSION = 3
RECORD_SIZE = 27
RECORD = struct.Struct("<HBBBHfffff")
BASE = {1: 125, 2: 125, 3: 200, 4: 175, 5: 150, 6: 300, 7: 175, 8: 125, 9: 125}
WINDOW = 200  # ticks after respawn in which the max health is sampled
NAMES = {1: "scout", 2: "sniper", 3: "soldier", 4: "demoman", 5: "medic",
         6: "heavy", 7: "pyro", 8: "spy", 9: "engineer"}


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    with open(argv[1], "rb") as handle:
        data = handle.read()
    if data[:4] != MAGIC:
        print(f"INVALID: bad magic {data[:4]!r}", file=sys.stderr)
        return 1
    version, meta_len = struct.unpack_from("<II", data, 4)
    if version != VERSION:
        print(f"INVALID: version {version}, expected {VERSION}", file=sys.stderr)
        return 1
    import json
    meta = json.loads(data[12:12+meta_len].decode("utf-8"))
    (frame_count,) = struct.unpack_from("<I", data, 12 + meta_len)
    pos = 12 + meta_len + 4
    # Death ticks per user id (from meta kills): an alive transition only
    # counts as a respawn when the player actually died shortly before.
    # Without this, a player entering the PVS while merely damaged (the
    # analyser's life state went stale) would look like a respawn.
    deaths = collections.defaultdict(list)
    for k in meta.get("kills", []):
        deaths[k["victim"]].append(k["tick"])
    for ticks in deaths.values():
        ticks.sort()

    uid_by_entity = {u["entity"]: u["userId"] for u in meta.get("users", [])}
    state = {}  # entity -> {"alive": bool, "resp": tick or None, "maxhp": int, "cls": int}
    mism = collections.Counter()
    examples = collections.defaultdict(list)
    ok = collections.Counter()
    for index in range(frame_count):
        tick, count = struct.unpack_from("<IH", data, pos)
        pos += 6
        for _ in range(count):
            entity, _team, cls, flags, health, *_ = RECORD.unpack_from(data, pos)
            pos += RECORD_SIZE
            _user = uid_by_entity.get(entity, -1)
            alive = bool(flags & 1)
            s = state.setdefault(entity, {"alive": alive, "resp": None, "maxhp": 0, "cls": cls})
            if alive and not s["alive"]:
                # Respawn only if a kill event for this user happened within
                # the last 10 seconds (~660 ticks); stale analyser life state
                # otherwise fakes respawns on PVS re-entry.
                recent_death = any(0 < tick - dt <= 2400 for dt in deaths.get(_user, []))
                if recent_death:
                    s["resp"] = tick
                    s["maxhp"] = 0
                    s["cls"] = cls
            if alive:
                s["maxhp"] = max(s["maxhp"], health)
                if cls:
                    s["cls"] = cls
                if s["resp"] is not None and tick - s["resp"] > WINDOW:
                    base = BASE.get(s["cls"], 0)
                    if base and s["maxhp"] < base * 0.95:
                        mism[s["cls"]] += 1
                        if len(examples[s["cls"]]) < 4:
                            examples[s["cls"]].append((tick, entity, s["maxhp"]))
                    elif base:
                        ok[s["cls"]] += 1
                    s["resp"] = None
            s["alive"] = alive
        (proj_count,) = struct.unpack_from("<H", data, pos)
        pos += 2 + proj_count * 17
        (hurt_count,) = struct.unpack_from("<H", data, pos)
        pos += 2 + hurt_count * 15
    if pos != len(data):
        print(f"INVALID: {len(data) - pos} trailing bytes", file=sys.stderr)
        return 1

    print(f"health audit: {argv[1]} ({frame_count} frames, respawn window {WINDOW} ticks)")
    total_m = total_o = 0
    for cls in sorted(BASE):
        total_m += mism[cls]
        total_o += ok[cls]
        print(f"  {NAMES[cls]:8s} OK {ok[cls]:4d}  MISMATCH {mism[cls]:4d}"
              + (f"  examples {examples[cls]}" if mism[cls] else ""))
    print(f"  total    OK {total_o}  MISMATCH {total_m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
