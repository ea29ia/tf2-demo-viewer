#!/usr/bin/env python3
"""Embed brush textures into a map GLB produced by bsp2glb.py.

Reads the GLB (multi-primitive world mesh, one primitive per texdata
material tagged in material.extras.texdata), resolves each material's VMT
(BSP pak -> tf2_misc -> loose tree) and its $basetexture VTF (BSP pak ->
tf2_textures -> loose tree), decodes mip 0, downscales, and embeds the PNG
into the GLB: images/textures/samplers appended, PNG bytes appended to the
BIN chunk. Existing geometry, bufferViews and accessors are untouched.

$detail travels in material.extras.sourceDetail (textureIndex/scale/
blendMode/blendFactor) AND, for non-WVT materials, in the emissive slot
(emissiveTexture + black emissiveFactor): a bare textureIndex in extras is
not resolvable by GLTFLoader, so the emissive transport is how the PNG
reaches the viewer. $bumpmap maps to normalTexture and all three can be
present on one material at once.

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


def _strip_ext(path: str) -> str:
    """Drop a trailing `.vmt`/`.vtf` so the caller can append its own.

    Some BSP texdata names already embed an extension (upward has
    `water/water_well_beneath.vmt`), and a texture value may name a `.vtf`
    explicitly; appending blindly produced `...vmt.vmt`.
    """
    p = path.replace("\\", "/").strip().strip('"')
    low = p.lower()
    for ext in (".vmt", ".vtf"):
        if low.endswith(ext):
            return p[:-len(ext)]
    return p


def vmt_path_for(name: str) -> str:
    """Texdata material name -> `materials/<name>.vmt` (lowercase)."""
    return "materials/" + _strip_ext(name).lower() + ".vmt"


def vtf_path_for(texture: str) -> str:
    """Texture path (with or without `.vtf`) -> `materials/<path>.vtf`."""
    return "materials/" + _strip_ext(texture).lower() + ".vtf"


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
        self.tf_dir = tf_dir
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
        self._loose = None
        self._loose_tried = False
        self.stats = {"pak": 0, "tf2_misc": 0, "tf2_textures": 0, "loose": 0}

    def _loose_pack(self):
        """Index the loose tf tree lazily, only when the packs miss.

        The walk is expensive and most lookups resolve from the VPKs, so it
        is built at most once and never when the directory does not exist.
        """
        if not self._loose_tried:
            self._loose_tried = True
            if os.path.isdir(self.tf_dir):
                try:
                    self._loose = vpk.LoosePack(self.tf_dir)
                except OSError as exc:
                    print(f"warning: loose tree unavailable: {exc}", file=sys.stderr)
        return self._loose

    def read(self, path: str) -> bytes | None:
        key = path.lower().replace("\\", "/")
        if key in self.pak:
            self.stats["pak"] += 1
            return self.pak[key]
        for pack_name, pack in self.packs:
            if pack.exists(key):
                self.stats[pack_name] += 1
                return pack.read(key)
        loose = self._loose_pack()
        if loose is not None and loose.exists(key):
            self.stats["loose"] += 1
            return loose.read(key)
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
    resolved_mats: dict[int, dict] = {}
    texture_paths: dict[int, list[str]] = {}
    missing_vmt: list[str] = []
    for mat_index, name in sorted(texdata_of.items()):
        path = vmt_path_for(name)
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
        resolved_mats[mat_index] = mat
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

    def embed(texture_path: str, size: int, label: str = "texture") -> int | None:
        nonlocal cursor
        key = (texture_path, size)
        if key in png_cache:
            return png_cache[key]
        vtf_path = vtf_path_for(texture_path)
        data = packs.read(vtf_path)
        if data is None:
            print(f"warning: no VTF for {label} {texture_path}", file=sys.stderr)
            png_cache[key] = None
            return None
        try:
            w, h, rgba = vtf.decode_vtf_scaled(data, size)
        except vtf.VtfError as exc:
            # An undecodable format (some normal maps use uncompressed or
            # exotic layouts) skips that one texture, never the material.
            print(f"warning: VTF {label} {texture_path}: {exc}", file=sys.stderr)
            png_cache[key] = None
            return None
        png = vtf.write_png_bytes(w, h, rgba)
        padded_png = png + b"\0" * ((4 - len(png) % 4) % 4)
        view_index = len(gltf.get("bufferViews", []))
        gltf.setdefault("bufferViews", []).append({
            "buffer": 0,
            "byteOffset": cursor,
            "byteLength": len(png),
        })
        # cursor must advance by the ALIGNED length (the next bufferView
        # points past the padding that follows this PNG in the BIN chunk).
        cursor += len(padded_png)
        bin_parts.append(padded_png)
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
    mask_count = 0
    blend_count = 0
    wvt_count = 0
    bump_count = 0
    ssbump_count = 0
    detail_count = 0
    kept_color_count = 0
    no_texture: list[str] = []
    mutable_bin = bytearray(bin_blob)
    for mat_index in sorted(texdata_of):
        mat = materials[mat_index]
        paths = texture_paths.get(mat_index) or []
        if not paths:
            no_texture.append(texdata_of[mat_index])
            continue
        resolved = resolved_mats[mat_index]
        shader = vmt.shader_name_of(resolved)
        wvt = shader == "worldvertextransition"
        size = LARGE_SIZE if area_of.get(mat_index, 0.0) >= LARGE_AREA else SMALL_SIZE
        texture_index = embed(paths[0], size)
        if texture_index is None:
            no_texture.append(texdata_of[mat_index])
            continue
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorTexture"] = {"index": texture_index, "texCoord": 0}
        # Source material opacity -> glTF alpha mode. PNGs carry the VTF
        # alpha channel, so only the mode/cutoff flags are missing.
        if vmt.is_alpha_tested(resolved):
            mat["alphaMode"] = "MASK"
            mat["alphaCutoff"] = vmt.alpha_test_reference(
                resolved, DEFAULT_ALPHA_CUTOFF)
            mask_count += 1
        elif vmt.is_translucent(resolved) or vmt.is_decal(resolved):
            mat["alphaMode"] = "BLEND"
            blend_count += 1
        # WorldVertexTransition blends two materials by COLOR_0.r. The blend
        # weight (red channel) comes from bsp2glb.py on displacement faces.
        # Transport the second texture in the emissive slot: emissiveFactor
        # is zero, so a spec-compliant renderer shows nothing extra, and the
        # viewer reads emissiveTexture as the blend map. See extras.sourceBlend.
        blend_index = None
        if wvt:
            tex2 = resolved.get("$basetexture2")
            tex2_path = vmt.normalize_texture_path(tex2) if isinstance(tex2, str) else ""
            if tex2_path:
                blend_index = embed(tex2_path, size, "WVT $basetexture2")
            if blend_index is not None:
                mat["emissiveTexture"] = {"index": blend_index, "texCoord": 0}
                mat["emissiveFactor"] = [0.0, 0.0, 0.0]
                mat.setdefault("extras", {})["sourceBlend"] = {
                    "shader": "WorldVertexTransition",
                    "channel": "COLOR_0.r",
                    "texture2Index": blend_index,
                }
                wvt_count += 1
            else:
                print(f"warning: WVT material {texdata_of[mat_index]} has no "
                      f"usable $basetexture2, rendering unblended",
                      file=sys.stderr)
            # TODO: `$blendmodulatetexture` (detail-style modulation of the
            # blend weight, smoothstep-shaped) is not implemented either.
        # $bumpmap -> normalTexture. Only REAL tangent-space normal maps may
        # go there: many Source $bumpmap textures are SSBump/height maps
        # (`*_height-ssbump`, `*_bump-ssbump`) — grayscale height fields that
        # Source self-shadows with. glTF has no ssbump slot; feeding a height
        # map through normalTexture bends tangent-space normals sideways and
        # the surface renders near-black (measured: mean RGB ~60/60/60 instead
        # of blue-dominant). Source names normal maps `*_normal`, so the name
        # is the discriminator; ssbump/height maps are skipped (smooth-lit
        # surface) until a real SSBump shader pass exists.
        bump = resolved.get("$bumpmap")
        bump_path = vmt.normalize_texture_path(bump) if isinstance(bump, str) else ""
        if bump_path and "normal" in bump_path.lower():
            bump_index = embed(bump_path, size, "normal map $bumpmap")
            if bump_index is not None:
                mat["normalTexture"] = {"index": bump_index, "texCoord": 0}
                extras = mat.setdefault("extras", {})
                extras["sourceBumpmap"] = True
                bump_uv = vmt.get_bump_uv_transform(resolved)
                if bump_uv:
                    extras["sourceBumpmapUV"] = bump_uv
                bump_count += 1
        elif bump_path:
            ssbump_count += 1
            print(f"note: $bumpmap {bump_path} is an SSBump/height map, "
                  f"not attached as normalTexture ({texdata_of[mat_index]})",
                  file=sys.stderr)
        # $detail -> extras.sourceDetail + emissiveTexture transport. The
        # detail textureIndex in extras alone is NOT resolvable by GLTFLoader
        # (it only creates textures for known material slots), so the PNG is
        # ALSO wired into the emissive slot — emissiveFactor stays black, so
        # a spec-compliant renderer shows nothing extra, and the viewer reads
        # emissiveTexture as the detail map (same transport trick as the WVT
        # blend, see extras.sourceBlend). normalTexture is owned by $bumpmap,
        # so the detail never collides with it. Blend modes (see
        # vmt.get_detail): 0 MODULATE = detail * base * 2, 1 ADD, 2 TRANSFORM
        # (basescale), 6+ unlit/fancy. The viewer only needs mode 0 today;
        # pl_upward uses mode 0 exclusively (histogram checked across the 72
        # texdata VMTs), the other modes are transported for completeness.
        detail = vmt.get_detail(resolved)
        if detail:
            detail_index = embed(detail["texture"], size, "$detail")
            if detail_index is not None:
                mat.setdefault("extras", {})["sourceDetail"] = {
                    "textureIndex": detail_index,
                    "scale": detail["scale"],          # float or [u, v]
                    "blendMode": detail["blendMode"],  # 0 = MODULATE
                    "blendFactor": detail["blendFactor"],
                }
                # One emissive slot, two potential passengers: the WVT blend
                # texture (wired above) and the detail texture. If a future
                # material were BOTH WVT and $detail, keep the WVT transport
                # (it wins the slot) and leave the detail as an unresolvable
                # extras record rather than overloading the slot.
                if blend_index is not None:
                    print(f"warning: material {texdata_of[mat_index]} is both "
                          f"WVT and $detail; emissive slot keeps the WVT "
                          f"blend texture, detail is not transportable",
                          file=sys.stderr)
                else:
                    mat["emissiveTexture"] = {"index": detail_index,
                                              "texCoord": 0}
                    mat["emissiveFactor"] = [0.0, 0.0, 0.0]
                detail_count += 1
        # glTF multiplies COLOR_0 into the base color; the palette vertex
        # colors were the pre-texture stand-in and would darken/tint the real
        # texture. Neutralise them (in place — offsets stay valid) once the
        # material has a map — EXCEPT where Source itself consumes COLOR_0:
        # WVT keeps the blend weight in .r, $vertexcolor/$vertexalpha keep
        # the tint/opacity.
        keep_vertex_color = (
            blend_index is not None
            or vmt.has_vertex_color(resolved)
            or vmt.has_vertex_alpha(resolved)
        )
        if keep_vertex_color:
            kept_color_count += 1
        else:
            vcolor_prim = next(p for p in world["primitives"]
                               if p.get("material") == mat_index)
            cacc = gltf["accessors"][vcolor_prim["attributes"]["COLOR_0"]]
            cview = gltf["bufferViews"][cacc["bufferView"]]
            coff = cview.get("byteOffset", 0) + cacc.get("byteOffset", 0)
            ones = b"\x01\x00\x80\x3f" * (cacc["count"] * 3)  # 1.0f little-endian
            mutable_bin[coff:coff + len(ones)] = ones
        if shader == "unlitgeneric":
            ext = mat.setdefault("extensions", {})
            ext["KHR_materials_unlit"] = {}
            # GLTFLoader only installs an extension handler when the asset
            # DECLARES the extension in extensionsUsed; a material-level
            # extension without it crashes the loader (kmuExtension undefined).
            used = gltf.setdefault("extensionsUsed", [])
            if "KHR_materials_unlit" not in used:
                used.append("KHR_materials_unlit")
        textured += 1

    bin_blob = bytes(mutable_bin) + b"".join(bin_parts)
    gltf["buffers"][0]["byteLength"] = len(bin_blob)
    if textures:
        gltf["textures"] = textures
    write_glb(glb_path, gltf, bin_blob)

    print(f"textures embedded: {textured} materials "
          f"({len(set(k[0] for k in png_cache if png_cache[k] is not None))} unique PNGs)",
          file=sys.stderr)
    print(f"alpha modes: MASK {mask_count}, BLEND {blend_count}", file=sys.stderr)
    print(f"worldvertextransition: {wvt_count} materials with blend texture "
          f"(transport: emissiveTexture, weight: COLOR_0.r)", file=sys.stderr)
    print(f"normal maps attached: {bump_count}", file=sys.stderr)
    print(f"ssbump/height $bumpmaps skipped (not normal maps): {ssbump_count}",
          file=sys.stderr)
    print(f"detail maps attached: {detail_count} (transport: "
          f"emissiveTexture + extras.sourceDetail "
          f"-> textureIndex/scale/blendMode/blendFactor)",
          file=sys.stderr)
    print(f"COLOR_0 kept (WVT/$vertexcolor/$vertexalpha): {kept_color_count}",
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
