#!/usr/bin/env python3
"""Embed brush textures into a map GLB produced by bsp2glb.py.

Reads the GLB (multi-primitive world mesh, one primitive per texdata
material tagged in material.extras.texdata), resolves each material's VMT
(BSP pak -> tf2_misc -> loose tree) and its $basetexture VTF (BSP pak ->
tf2_textures -> loose tree), decodes mip 0, downscales, and embeds the PNG
into the GLB: images/textures/samplers appended, PNG bytes appended to the
BIN chunk. Existing geometry, bufferViews and accessors are untouched.

Size policy (user decision: no hard limits; measured defaults):
- the N materials with the largest painted area get 512x512,
- the rest get 256x256.

Usage: python3 tools/add_textures_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
import os
import struct
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import bsp2glb  # Bsp + lump constants (lump access only, no geometry build)
import vmt
import vtf
import vpk

# Area threshold in u^2: materials covering more painted area than this get
# the large size. Computed from triangle areas, not face counts, so a few
# huge ground faces dominate the budget the way they dominate screen space.
LARGE_AREA = 200_000.0
LARGE_SIZE = 512
SMALL_SIZE = 256
# Alpha-tested materials (grates, foliage cards) get MASK blending.
DEFAULT_ALPHA_CUTOFF = 0.5


def load_glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise SystemExit(f"{path}: not a valid GLB")
    json_blob = bin_blob = None
    pos = 12
    while pos < len(data):
        length, kind = struct.unpack_from("<II", data, pos)
        pos += 8
        chunk = data[pos:pos + length]
        pos += length
        if kind == 0x4E4F534A:
            json_blob = chunk
        elif kind == 0x004E4942:
            bin_blob = chunk
    return json.loads(json_blob.decode("utf-8")), bin_blob


def write_glb(path: str, gltf: dict, bin_blob: bytes) -> None:
    json_blob = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_blob = json_blob + b" " * ((4 - len(json_blob) % 4) % 4)
    bin_blob = bin_blob + b"\0" * ((4 - len(bin_blob) % 4) % 4)
    total = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_blob), 0x004E4942))
        f.write(bin_blob)


class PackSet:
    """Resolution order for one asset kind: BSP pak -> packs -> loose tree."""

    def __init__(self, bsp: bsp2glb.Bsp, tf_dir: str):
        self.pak: dict[str, bytes] = {}
        raw = bsp.lump(40)
        if raw[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    self.pak[name.lower()] = z.read(name)
        self.packs: list[tuple[str, object]] = []
        if os.path.isdir(tf_dir):
            for pack_name in ("tf2_misc", "tf2_textures"):
                try:
                    self.packs.append((pack_name, vpk.open_pack(tf_dir, pack_name)))
                except Exception as exc:  # missing VPKs: degrade, don't crash
                    print(f"warning: {pack_name} unavailable: {exc}", file=sys.stderr)
        self.stats = {"pak": 0, "tf2_misc": 0, "tf2_textures": 0, "loose": 0}

    def read(self, path: str) -> bytes | None:
        key = path.lower().replace("\\", "/")
        if key in self.pak:
            self.stats["pak"] += 1
            return self.pak[key]
        for pack_name, pack in self.packs:
            if pack.exists(key):
                self.stats[pack_name] += 1
                return pack.read(key)
        return None


def material_area(gltf: dict, bin_blob: bytes, prim: dict) -> float:
    """Total triangle area of one world primitive (world units^2)."""
    acc = gltf["accessors"][prim["attributes"]["POSITION"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    n = acc["count"]
    xyz = struct.unpack_from(f"<{n * 3}f", bin_blob, off)
    iacc = gltf["accessors"][prim["indices"]]
    iview = gltf["bufferViews"][iacc["bufferView"]]
    ioff = iview.get("byteOffset", 0) + iacc.get("byteOffset", 0)
    idx = struct.unpack_from(f"<{iacc['count']}I", bin_blob, ioff)

    def pt(i):
        return (xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2])

    area = 0.0
    for k in range(0, len(idx), 3):
        a, b, c = pt(idx[k]), pt(idx[k + 1]), pt(idx[k + 2])
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cx = u[1] * v[2] - u[2] * v[1]
        cy = u[2] * v[0] - u[0] * v[2]
        cz = u[0] * v[1] - u[1] * v[0]
        area += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
    return area


def main(argv) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input.bsp> <map.glb>", file=sys.stderr)
        return 2
    bsp_path, glb_path = argv[1], argv[2]
    tf_dir = os.environ.get(
        "TF2_DIR",
        os.path.expanduser(
            "~/.steam/debian-installation/steamapps/common/Team Fortress 2/tf"))

    bsp = bsp2glb.Bsp(bsp_path)
    packs = PackSet(bsp, tf_dir)

    gltf, bin_blob = load_glb(glb_path)
    materials = gltf.get("materials", [])
    world = gltf["meshes"][0]

    # 1. Which materials need textures, and how much area each covers?
    texdata_of: dict[int, str] = {}
    area_of: dict[int, float] = {}
    for prim in world["primitives"]:
        mat_index = prim.get("material")
        if mat_index is None or mat_index in texdata_of:
            continue
        mat = materials[mat_index]
        name = (mat.get("extras") or {}).get("texdata")
        if not name:
            continue
        texdata_of[mat_index] = name
        area_of[mat_index] = material_area(gltf, bin_blob, prim)

    # 2. Resolve VMT per material, then its texture paths.
    vmt_text: dict[int, str] = {}
    texture_paths: dict[int, list[str]] = {}
    missing_vmt: list[str] = []
    for mat_index, name in sorted(texdata_of.items()):
        path = "materials/" + name.lower() + ".vmt"
        data = packs.read(path)
        if data is None:
            missing_vmt.append(name)
            continue
        try:
            mat = vmt.resolve(data.decode("latin-1"), packs.read)
        except vmt.VmtError as exc:
            print(f"warning: VMT {name}: {exc}", file=sys.stderr)
            missing_vmt.append(name)
            continue
        vmt_text[mat_index] = name
        texture_paths[mat_index] = vmt.get_texture_paths(mat)
    print(f"materials: {len(texdata_of)} texdata-tagged, "
          f"{len(vmt_text)} VMT resolved, {len(missing_vmt)} without VMT",
          file=sys.stderr)
    for name in missing_vmt:
        print(f"  no VMT: {name}", file=sys.stderr)

    # 3. Decode + embed one PNG per unique texture path (shared across
    # materials, e.g. WorldVertexTransition's two slots).
    png_cache: dict[tuple[str, int], tuple[int, int]] = {}
    images: list[dict] = []
    textures: list[dict] = []
    bin_parts: list[bytes] = []
    cursor = len(bin_blob)

    def embed(texture_path: str, size: int) -> int | None:
        nonlocal cursor
        key = (texture_path, size)
        if key in png_cache:
            return png_cache[key]
        vtf_path = "materials/" + texture_path + ".vtf"
        data = packs.read(vtf_path)
        if data is None:
            print(f"warning: no VTF for {texture_path}", file=sys.stderr)
            png_cache[key] = None
            return None
        try:
            w, h, rgba = vtf.decode_vtf_scaled(data, size)
        except vtf.VtfError as exc:
            print(f"warning: VTF {texture_path}: {exc}", file=sys.stderr)
            png_cache[key] = None
            return None
        png = vtf.write_png_bytes(w, h, rgba)
        view_index = len(gltf.get("bufferViews", []))
        gltf.setdefault("bufferViews", []).append({
            "buffer": 0,
            "byteOffset": cursor,
            "byteLength": len(png),
        })
        cursor += len(png)
        bin_parts.append(png + b"\0" * ((4 - len(png) % 4) % 4))
        image_index = len(gltf.get("images", []))
        gltf.setdefault("images", []).append({"bufferView": view_index,
                                              "mimeType": "image/png"})
        texture_index = len(textures)
        textures.append({
            "sampler": 0,  # single shared REPEAT sampler, appended below
            "source": image_index,
        })
        png_cache[key] = texture_index
        return texture_index

    # 4. Wire baseColorTexture (and blending mode) onto each material.
    samplers = gltf.setdefault("samplers", [])
    sampler0 = 0
    if not samplers:
        samplers.append({
            "wrapS": 10497,  # REPEAT
            "wrapT": 10497,
            "minFilter": 9987,  # LINEAR_MIPMAP_LINEAR
            "magFilter": 9729,  # LINEAR
        })
        sampler0 = 0
    else:
        sampler0 = len(samplers)
        samplers.append({
            "wrapS": 10497, "wrapT": 10497,
            "minFilter": 9987, "magFilter": 9729,
        })

    textured = 0
    no_texture: list[str] = []
    mutable_bin = bytearray(bin_blob)
    for mat_index in sorted(texdata_of):
        mat = materials[mat_index]
        paths = texture_paths.get(mat_index) or []
        if not paths:
            no_texture.append(texdata_of[mat_index])
            continue
        # WorldVertexTransition: only slot 0 for now (blend phase comes later).
        size = LARGE_SIZE if area_of.get(mat_index, 0.0) >= LARGE_AREA else SMALL_SIZE
        texture_index = embed(paths[0], size)
        if texture_index is None:
            no_texture.append(texdata_of[mat_index])
            continue
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorTexture"] = {"index": texture_index, "texCoord": 0}
        # glTF multiplies COLOR_0 into the base color; the palette vertex
        # colors were the pre-texture stand-in and would darken/tint the real
        # texture. Neutralise them (in place — offsets stay valid) once the
        # material has a map.
        vcolor_prim = next(p for p in world["primitives"] if p.get("material") == mat_index)
        cacc = gltf["accessors"][vcolor_prim["attributes"]["COLOR_0"]]
        cview = gltf["bufferViews"][cacc["bufferView"]]
        coff = cview.get("byteOffset", 0) + cacc.get("byteOffset", 0)
        ones = b"\x01\x00\x80\x3f" * (cacc["count"] * 3)  # 1.0f little-endian
        mutable_bin[coff:coff + len(ones)] = ones
        shader = vmt.shader_name_of(
            vmt.resolve(
                packs.read("materials/" + texdata_of[mat_index].lower() + ".vmt")
                .decode("latin-1"), packs.read))
        if shader == "unlitgeneric":
            ext = mat.setdefault("extensions", {})
            ext["KHR_materials_unlit"] = {}
        textured += 1

    bin_blob = bytes(mutable_bin) + b"".join(bin_parts)
    gltf["buffers"][0]["byteLength"] = len(bin_blob)
    write_glb(glb_path, gltf, bin_blob)

    print(f"textures embedded: {textured} materials "
          f"({len(set(k[0] for k in png_cache if png_cache[k] is not None))} unique PNGs)",
          file=sys.stderr)
    if no_texture:
        print(f"materials without texture ({len(no_texture)}): "
              + ", ".join(sorted(set(no_texture))), file=sys.stderr)
    print(f"resolution sources: {packs.stats}", file=sys.stderr)
    total = len(bin_blob)
    print(f"wrote {glb_path}: BIN {total / (1024 * 1024):.2f} MiB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
