#!/usr/bin/env python3
"""Probe the sprp game lump (static props) of a Source BSP v20.

Verified layout of the TF2 pl_upward.bsp "prps" v10 game lump after LZMA
decompression:
  i32 model_count, model_count x 128-byte (nul-padded) model names,
  i32 leaf_count, leaf_count x u16 leaves,
  i32 prop_count, prop_count x StaticPropLump (72 bytes),
  end of lump (prop data fills the remainder exactly).

Usage: python3 tools/probe_props.py assets/pl_upward.bsp
"""
from __future__ import annotations

import collections
import importlib.util
import lzma
import struct
import sys

spec = importlib.util.spec_from_file_location("m", "tools/bsp2glb.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PROP_STRIDE = 72
NAME_STRIDE = 128


def decompress_sprp(bsp) -> tuple[bytes, int]:
    gl_ofs, gl_len = struct.unpack_from("<ii", bsp.data, 8 + 16 * 35)
    count = struct.unpack_from("<i", bsp.data, gl_ofs)[0]
    print(f"game lump: ofs={gl_ofs} len={gl_len} count={count}")
    for i in range(count):
        fourcc, flags, version, offset, length = struct.unpack_from(
            "<4sHHiI", bsp.data, gl_ofs + 4 + 16 * i)
        print(f"  lump {fourcc!r} flags={flags} ver={version} ofs={offset} len={length}")
        if fourcc in (b"sprp", b"prps"):
            magic, actual, compressed = struct.unpack_from("<4sII", bsp.data, offset)
            if magic != b"LZMA":
                raise SystemExit(f"no LZMA at {offset}: {magic!r}")
            props = bsp.data[offset + 12: offset + 17]
            lc = props[0] % 9
            rest = props[0] // 9
            lp = rest % 5
            pb = rest // 5
            dict_size = int.from_bytes(props[1:5], "little")
            if dict_size < (1 << 16):
                dict_size = 1 << 24
            filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                        "lc": lc, "lp": lp, "pb": pb}]
            out = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(
                bsp.data[offset + 17: offset + 17 + compressed])
            return out[:actual], version
    raise SystemExit("no sprp/prps game lump")


def parse(data: bytes):
    model_count = struct.unpack_from("<i", data, 0)[0]
    names = [data[4 + i * NAME_STRIDE: 4 + (i + 1) * NAME_STRIDE].split(b"\0")[0].decode("latin-1")
             for i in range(model_count)]
    p = 4 + model_count * NAME_STRIDE
    leaf_count = struct.unpack_from("<i", data, p)[0]
    p += 4 + leaf_count * 2
    prop_count = struct.unpack_from("<i", data, p)[0]
    entry = p + 4
    return names, leaf_count, prop_count, entry


def main(argv) -> int:
    bsp = m.Bsp(argv[1])
    data, version = decompress_sprp(bsp)
    print(f"sprp version {version}, decompressed size {len(data)}")
    names, leaf_count, prop_count, entry = parse(data)
    print(f"model_count={len(names)} leaf_count={leaf_count} prop_count={prop_count}")
    end = entry + prop_count * PROP_STRIDE
    print(f"prop entries {entry}..{end} (lump {len(data)}): "
          f"{'exact fit' if end == len(data) else 'MISMATCH'}")

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    in_map = 0
    counts = collections.Counter()
    for i in range(prop_count):
        ox, oy, oz = struct.unpack_from("<3f", data, entry + i * PROP_STRIDE)
        pt = struct.unpack_from("<H", data, entry + i * PROP_STRIDE + 24)[0]
        counts[names[pt] if pt < len(names) else "?"] += 1
        if -4096 <= ox <= 12288 and -4096 <= oy <= 8192 and -2048 <= oz <= 2048:
            in_map += 1
        for k, v in enumerate((ox, oy, oz)):
            lo[k] = min(lo[k], v)
            hi[k] = max(hi[k], v)
    print(f"origins inside map bounds: {in_map}/{prop_count}")
    print(f"origin bbox min={tuple(round(v, 1) for v in lo)} max={tuple(round(v, 1) for v in hi)}")
    print("first 10 names:", names[:10])
    print("top 20 names:")
    for name, n in counts.most_common(20):
        print(f"  {n:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
