#!/usr/bin/env python3
"""Export a BSP's skybox (worldspawn `skyname`) as six cube-face PNGs.

Reads the entity lump for `skyname`, resolves the six sky VMTs
(materials/skybox/<skyname>{ft,bk,lf,rt,up,dn}.vmt in tf2_misc) to their LDR
`$basetexture` VTFs (tf2_textures), decodes mip 0 with the existing vtf.py,
resamples every face to a square 512x512 and writes them next to the map GLB
plus a sky.json manifest consumed by the viewer:

    { "sky": "sky_upward", "size": 512,
      "faces": { "px": "...", "nx": "...", "py": "...", "ny": "...",
                 "pz": "...", "nz": "..." } }

Face mapping (Source Z-up -> viewer Y-up cube slots px/nx/py/ny/pz/nz):
Source yaw 0 faces +X, so ft=+X, bk=-X, rt=-Y, lf=+Y, up=+Z, dn=-Z.
The viewer maps Source +Y -> viewer -Z and Source +Z -> viewer +Y, hence
px<-ft, nx<-bk, py<-up, ny<-dn, pz<-rt, nz<-lf.

Source's sky shader maps the shared `side` texture so the horizon line sits
at the vertical centre of each side face (the texture is zenith->nadir with
the horizon in the middle; each cube face spans 90 deg vertically, so the
full 1:1 mapping puts the horizon at face middle — below-horizon texels are
hidden by the world anyway). pl_upward's sky is radially symmetric, so
per-face rotation is currently invisible; if a future map has a directional
sky, the in-face orientation may need a per-face rotation pass (noted in
STATUS.md).

Usage: python3 tools/export_sky.py assets/pl_upward.bsp web/public/maps
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

import bsp2glb  # noqa: E402 (lump access + Bsp)
import vmt  # noqa: E402
import vpk  # noqa: E402
import vtf  # noqa: E402

FACE_SIZE = 512
# Source face suffix -> Source-space direction (see module docstring).
FACE_OF_SLOT = {
    "px": "ft",
    "nx": "bk",
    "py": "up",
    "ny": "dn",
    "pz": "rt",
    "nz": "lf",
}


def skyname_of(bsp: bsp2glb.Bsp) -> str:
    text = bsp.lump(bsp2glb.LUMP_ENTITIES).decode("utf-8", errors="replace")
    match = re.search(r'"skyname"\s*"([^"]*)"', text)
    if not match or not match.group(1):
        raise SystemExit("worldspawn has no skyname")
    return match.group(1)


def resample_bilinear(
    rgba: bytes, w: int, h: int, tw: int, th: int
) -> bytes:
    """RGBA bilinear resample (gradient skies only — no gamma tricks)."""
    out = bytearray(tw * th * 4)
    for ty in range(th):
        sy = (ty + 0.5) * h / th - 0.5
        y0 = max(0, min(h - 1, int(sy)))
        y1 = max(0, min(h - 1, y0 + 1))
        fy = sy - y0
        for tx in range(tw):
            sx = (tx + 0.5) * w / tw - 0.5
            x0 = max(0, min(w - 1, int(sx)))
            x1 = max(0, min(w - 1, x0 + 1))
            fx = sx - x0
            for c in range(4):
                v00 = rgba[(y0 * w + x0) * 4 + c]
                v10 = rgba[(y0 * w + x1) * 4 + c]
                v01 = rgba[(y1 * w + x0) * 4 + c]
                v11 = rgba[(y1 * w + x1) * 4 + c]
                out[(ty * tw + tx) * 4 + c] = int(
                    v00 * (1 - fx) * (1 - fy)
                    + v10 * fx * (1 - fy)
                    + v01 * (1 - fx) * fy
                    + v11 * fx * fy
                )
    return bytes(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input.bsp> <out_dir>", file=sys.stderr)
        return 2
    bsp_path, out_dir = argv[1], argv[2]
    tf_dir = os.environ.get(
        "TF2_DIR",
        os.path.expanduser(
            "~/.steam/debian-installation/steamapps/common/Team Fortress 2/tf"))

    sky = skyname_of(bsp2glb.Bsp(bsp_path))
    print(f"skyname: {sky}", file=sys.stderr)

    packs_misc = vpk.open_pack(tf_dir, "tf2_misc")
    packs_tex = vpk.open_pack(tf_dir, "tf2_textures")

    os.makedirs(out_dir, exist_ok=True)
    faces: dict[str, str] = {}
    horizon_rgb = [0.0, 0.0, 0.0]
    horizon_faces = 0
    for slot, suffix in FACE_OF_SLOT.items():
        vmt_path = f"materials/skybox/{sky}{suffix}.vmt"
        vmt_raw = packs_misc.read(vmt_path)
        if vmt_raw is None:
            print(f"warning: no VMT for face {suffix} ({vmt_path}); "
                  f"slot {slot} left out", file=sys.stderr)
            continue
        mat = vmt.resolve(vmt_raw.decode("latin-1"), packs_misc.read)
        base = mat.get("$basetexture")
        if not isinstance(base, str):
            print(f"warning: {vmt_path} has no $basetexture", file=sys.stderr)
            continue
        base = base.strip().strip('"').lower()
        if base.endswith(".vtf"):
            base = base[:-4]
        vtf_raw = packs_tex.read(f"materials/{base}.vtf")
        if vtf_raw is None:
            print(f"warning: no VTF for face {suffix} ({base})", file=sys.stderr)
            continue
        w, h, rgba = vtf.decode_vtf(vtf_raw)
        sq = resample_bilinear(rgba, w, h, FACE_SIZE, FACE_SIZE)
        name = f"sky_{slot}.png"
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(vtf.write_png_bytes(FACE_SIZE, FACE_SIZE, sq))
        faces[slot] = name
        # Brightness sanity: means should be sky-like (blue >= red).
        n = len(sq) // 4
        r, g, b = (sum(sq[c::4]) / n for c in range(3))
        # Horizon/fog colour: mean of the bottom band of the side faces (the
        # haze line the world's distant geometry fades into).
        if suffix in ("ft", "bk", "lf", "rt"):
            band = sq[(FACE_SIZE * 3 // 4) * FACE_SIZE * 4:]
            nb = len(band) // 4
            horizon_sums = [sum(band[c::4]) / nb for c in range(3)]
            for i in range(3):
                horizon_rgb[i] += horizon_sums[i]
            horizon_faces += 1
        print(f"  {suffix:2s} -> {slot} {w}x{h} -> {FACE_SIZE}^2 "
              f"means rgb=({r:.0f},{g:.0f},{b:.0f})", file=sys.stderr)

    if len(faces) != 6:
        print(f"warning: only {len(faces)}/6 faces exported", file=sys.stderr)
    manifest = {
        "sky": sky,
        "size": FACE_SIZE,
        "faces": faces,
        # Scene fog tint (0-255 rgb): the horizon haze, so distant geometry
        # fades into the sky rather than into the old dark navy.
        "fog": [round(v / horizon_faces) for v in horizon_rgb]
        if horizon_faces else None,
    }
    with open(os.path.join(out_dir, "sky.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"wrote {os.path.join(out_dir, 'sky.json')} ({len(faces)} faces)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
