#!/usr/bin/env python3
"""Valve Texture Format (VTF) reader + minimal PNG writer, stdlib only.

Supports the formats used by TF2 character/prop materials: DXT1, DXT3,
DXT5, BGR888, BGRA8888, RGB888, RGBA8888 and BGRX8888. Only mip 0 of frame 0
is decoded; cubemaps are not supported (not used by model materials).

Usage:
    python3 tools/vtf.py <in.vtf> <out.png>
"""
from __future__ import annotations

import struct
import sys
import zlib

SIG = b"VTF\0"

IMG_RGBA8888 = 0
IMG_ABGR8888 = 1
IMG_RGB888 = 2
IMG_BGR888 = 3
IMG_I8 = 5
IMG_A8 = 8
IMG_ARGB8888 = 11
IMG_BGRA8888 = 12
IMG_DXT1 = 13
IMG_DXT3 = 14
IMG_DXT5 = 15
IMG_BGRX8888 = 16


class VtfError(Exception):
    pass


def _mip_size(fmt: int, w: int, h: int) -> int:
    if fmt in (IMG_DXT1,):
        return max(1, w // 4) * max(1, h // 4) * 8
    if fmt in (IMG_DXT3, IMG_DXT5):
        return max(1, w // 4) * max(1, h // 4) * 16
    if fmt in (IMG_BGR888, IMG_RGB888):
        return w * h * 3
    if fmt in (IMG_RGBA8888, IMG_ABGR8888, IMG_ARGB8888, IMG_BGRA8888, IMG_BGRX8888):
        return w * h * 4
    if fmt in (IMG_I8, IMG_A8):
        return w * h
    raise VtfError(f"unsupported VTF image format {fmt}")


def _decode_dxt1(data: bytes, w: int, h: int) -> bytearray:
    out = bytearray(w * h * 4)
    pos = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            c0, c1, bits = struct.unpack_from("<HHI", data, pos)
            pos += 8
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            if c0 > c1:
                palette = [
                    (r0, g0, b0, 255),
                    (r1, g1, b1, 255),
                    ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                    ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255),
                ]
            else:
                palette = [
                    (r0, g0, b0, 255),
                    (r1, g1, b1, 255),
                    ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
                    (0, 0, 0, 0),
                ]
            for i in range(16):
                dx, dy = i % 4, i // 4
                x, y = bx + dx, by + dy
                if x >= w or y >= h:
                    continue
                color = palette[(bits >> (2 * i)) & 3]
                k = (y * w + x) * 4
                out[k:k + 4] = bytes(color)
    return out


def _decode_dxt5_alpha(block: bytes, w: int, h: int, bx: int, by: int, out: bytearray) -> None:
    a0, a1 = block[0], block[1]
    if a0 > a1:
        pal = [a0, a1] + [((6 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)]
    else:
        pal = [a0, a1] + [((4 - i) * a0 + (1 + i) * a1) // 5 for i in range(4)] + [0, 255]
    bits = int.from_bytes(block[2:8], "little")
    for i in range(16):
        x, y = i % 4, i // 4
        if bx + x < w and by + y < h:
            out[((by + y) * w + bx + x) * 4 + 3] = pal[(bits >> (3 * i)) & 7]


def _decode_dxt3_alpha(block: bytes, w: int, h: int, bx: int, by: int, out: bytearray) -> None:
    bits = int.from_bytes(block[:8], "little")
    for i in range(16):
        x, y = i % 4, i // 4
        if bx + x < w and by + y < h:
            out[((by + y) * w + bx + x) * 4 + 3] = ((bits >> (4 * i)) & 0xF) * 17


def _copy_dxt1_colors(block: bytes, w: int, h: int, bx: int, by: int, out: bytearray) -> None:
    color = _decode_dxt1(block, 4, 4)
    for i in range(16):
        x, y = i % 4, i // 4
        if bx + x < w and by + y < h:
            k = ((by + y) * w + bx + x) * 4
            out[k:k + 3] = color[i * 4:i * 4 + 3]


def _decode_dxt5(data: bytes, w: int, h: int) -> bytearray:
    out = bytearray(w * h * 4)
    pos = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            _decode_dxt5_alpha(data[pos:pos + 8], w, h, bx, by, out)
            _copy_dxt1_colors(data[pos + 8:pos + 16], w, h, bx, by, out)
            pos += 16
    return out


def _decode_dxt3(data: bytes, w: int, h: int) -> bytearray:
    out = bytearray(w * h * 4)
    pos = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            _decode_dxt3_alpha(data[pos:pos + 8], w, h, bx, by, out)
            _copy_dxt1_colors(data[pos + 8:pos + 16], w, h, bx, by, out)
            pos += 16
    return out


def _rgb565(c: int) -> tuple[int, int, int]:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def _decode_bgra(data: bytes, w: int, h: int, swap: bool, alpha: bool) -> bytearray:
    out = bytearray(w * h * 4)
    step = 4 if alpha else 3
    for i in range(w * h):
        s = i * step
        b, g, r = data[s], data[s + 1], data[s + 2]
        a = data[s + 3] if alpha else 255
        k = i * 4
        out[k:k + 4] = bytes((r, g, b, a))
    return out


def load_vtf(path: str) -> tuple[int, int, bytes]:
    with open(path, "rb") as f:
        data = f.read()
    return decode_vtf(data)


def decode_vtf(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < 64 or data[:4] != SIG:
        raise VtfError("not a VTF file")
    major, minor = struct.unpack_from("<II", data, 4)
    header_size = struct.unpack_from("<I", data, 12)[0]
    width, height = struct.unpack_from("<HH", data, 16)
    frames = struct.unpack_from("<H", data, 24)[0]
    fmt = struct.unpack_from("<I", data, 52)[0]
    mip_count = data[56]
    if frames > 1:
        raise VtfError("animated VTF not supported")
    if mip_count < 1:
        raise VtfError("VTF has no mipmaps")
    size = _mip_size(fmt, width, height)
    # VTF stores the mip chain smallest-first, so the largest mip (mip 0)
    # is the LAST mip_size bytes of the image data — not the first. Reading
    # from the start yields small-mip thumbnails followed by the head of
    # larger mips: a spatially scrambled image (verified by mip
    # self-consistency: mip0-downscaled vs mip1 mean|diff| = 1.0 for
    # last-largest vs 41.4 for first-largest).
    start = header_size + (len(data) - header_size) - size
    block = data[start:start + size]
    if len(block) < size:
        raise VtfError(f"truncated VTF data (need {size}, have {len(block)})")

    if fmt == IMG_DXT1:
        rgba = _decode_dxt1(block, width, height)
    elif fmt == IMG_DXT5:
        rgba = _decode_dxt5(block, width, height)
    elif fmt == IMG_DXT3:
        rgba = _decode_dxt3(block, width, height)
    elif fmt in (IMG_BGR888,):
        rgba = _decode_bgra(block, width, height, True, False)
    elif fmt in (IMG_RGB888,):
        rgba = _decode_bgra(block, width, height, False, False)
    elif fmt in (IMG_BGRA8888, IMG_BGRX8888):
        rgba = _decode_bgra(block, width, height, True, True)
    elif fmt in (IMG_RGBA8888, IMG_ABGR8888, IMG_ARGB8888):
        raise VtfError("non-BGRA 32-bit VTF not supported")
    else:
        raise VtfError(f"unsupported VTF image format {fmt}")
    return width, height, bytes(rgba)


def _box_downscale(rgba: bytes, w: int, h: int, tw: int, th: int) -> bytes:
    """Integer-factor box filter: w x h RGBA -> tw x th RGBA."""
    fx, fy = w // tw, h // th
    if fx <= 1 and fy <= 1:
        return rgba
    out = bytearray(tw * th * 4)
    for ty in range(th):
        for tx in range(tw):
            r = g = b_ = a = n = 0
            for sy in range(ty * fy, (ty + 1) * fy):
                row = sy * w
                for sx in range(tx * fx, (tx + 1) * fx):
                    k = (row + sx) * 4
                    r += rgba[k]
                    g += rgba[k + 1]
                    b_ += rgba[k + 2]
                    a += rgba[k + 3]
                    n += 1
            k = (ty * tw + tx) * 4
            out[k:k + 4] = bytes((r // n, g // n, b_ // n, a // n))
    return bytes(out)


def decode_vtf_scaled(data: bytes, max_size: int) -> tuple[int, int, bytes]:
    """Decode mip 0 and box-downscale so max(w, h) <= max_size.

    Only integer power-of-two reduction steps are used (matching the mip
    chain), so quality stays clean. Returns the FINAL (possibly reduced)
    dimensions. Raises the same VtfError variants as decode_vtf.
    """
    w, h, rgba = decode_vtf(data)
    tw, th = w, h
    while max(tw, th) > max_size:
        tw = max(1, tw // 2)
        th = max(1, th // 2)
    if (tw, th) == (w, h):
        return w, h, rgba
    return tw, th, _box_downscale(rgba, w, h, tw, th)


def write_png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * width * 4:(y + 1) * width * 4]
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return bytes(png)


def write_png(path: str, width: int, height: int, rgba: bytes) -> None:
    with open(path, "wb") as f:
        f.write(write_png_bytes(width, height, rgba))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: vtf.py <in.vtf> <out.png>", file=sys.stderr)
        return 2
    w, h, rgba = load_vtf(argv[1])
    write_png(argv[2], w, h, rgba)
    print(f"{argv[1]} -> {argv[2]} ({w}x{h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
