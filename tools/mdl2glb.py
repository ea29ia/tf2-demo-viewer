#!/usr/bin/env python3
"""Convert a Source engine model (MDL v48 + VVD + dx90 VTX) to glTF (GLB).

Static bind-pose conversion: vertex positions/normals/uvs come straight from
the VVD (LOD0, fixup-adjusted exactly like the engine does in
Studio_LoadVertexes), indices come from the dx90 VTX strip groups. No bones,
no animations — the model is exported in its bind pose, which is what the
viewer renders.

Team skins: skin family 0 is used for "red" output and family 1 for "blue"
(same convention the game uses). Each mesh material is resolved through the
skin table to a texture name; the matching VTF is pulled from the VPK set and
embedded into the GLB as a PNG image.

Usage:
    python3 tools/mdl2glb.py <tf_dir> <model_name> <out_prefix>

    <tf_dir>     path to the "tf" game directory (contains *.vpk)
    <model_name> internal model path without extension, e.g.
                 models/player/scout
    <out_prefix> output path prefix; produces <prefix>_red.glb / _blue.glb
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vpk  # noqa: E402
import vtf  # noqa: E402

VSIZE = 48  # sizeof(mstudiovertex_t): weights 16 + pos 12 + normal 12 + uv 8
MAX_NUM_LODS = 8
STRIP_IS_TRILIST = 0x01
STRIP_IS_TRISTRIP = 0x02


class MdlError(Exception):
    pass


# ---------------------------------------------------------------------------
# MDL
# ---------------------------------------------------------------------------
class Mdl:
    """Minimal studiohdr_t v48 reader. Only what the converter needs."""

    def __init__(self, data: bytes):
        self.data = data

        def i(o: int) -> int:
            return struct.unpack_from("<i", data, o)[0]

        def f(o: int) -> float:
            return struct.unpack_from("<f", data, o)[0]

        self.version = i(4)
        if self.version not in (44, 44 + 1, 46, 47, 48):
            raise MdlError(f"unsupported MDL version {self.version}")
        self.checksum = i(8)
        self.name = data[12:76].split(b"\0")[0].decode("latin-1")

        self.flags = i(152)
        self.numbones = i(156)
        self.boneindex = i(160)

        self.numtextures = i(204)
        self.textureindex = i(208)
        self.numskinref = i(220)
        self.numskinfamilies = i(224)
        self.skinindex = i(228)
        self.numbodyparts = i(232)
        self.bodypartindex = i(236)
        self.rootLOD = data[369] if len(data) > 369 else 0

        # texture names
        self.texture_names: list[str] = []
        for k in range(self.numtextures):
            off = self.textureindex + k * 64
            nameoff = i(off)
            self.texture_names.append(
                data[off + nameoff:].split(b"\0")[0].decode("latin-1"))

        # skin families: numskinfamilies x numskinref shorts
        self.skins: list[list[int]] = []
        for fam in range(self.numskinfamilies):
            base = self.skinindex + fam * self.numskinref * 2
            self.skins.append([
                struct.unpack_from("<h", data, base + k * 2)[0]
                for k in range(self.numskinref)])

        # bodyparts -> models -> meshes
        # mstudiomesh_t stride is 116 on disk for v46+ (8 ints + Vector center
        # + mstudio_meshvertexdata_t{ ptr, numLODVertexes[8] } + unused[8]).
        self.bodyparts: list[dict] = []
        for b in range(self.numbodyparts):
            bp = self.bodypartindex + b * 16
            nameoff = i(bp)
            nummodels = i(bp + 4)
            modelindex = i(bp + 12)
            models = []
            for m in range(nummodels):
                mo = bp + modelindex + m * 96
                if mo + 96 > len(data):
                    break  # trailing padding models, garbage fields
                mname = data[mo:mo + 64].split(b"\0")[0].decode("latin-1")
                nummeshes = i(mo + 72)
                numvertices = i(mo + 80)
                if nummeshes <= 0 or numvertices <= 0:
                    continue  # empty LOD placeholder model
                if mo + 72 + nummeshes * 116 > len(data):
                    continue  # implausible mesh count
                meshoff = i(mo + 76)
                vertexindex = i(mo + 84)  # byte offset into the vvd pool
                tangentsindex = i(mo + 88)  # byte offset into tangent pool
                meshes = []
                for me in range(nummeshes):
                    e = mo + meshoff + me * 116
                    if e + 116 > len(data):
                        break
                    meshes.append({
                        "material": i(e),
                        "numvertices": i(e + 8),
                        "vertexoffset": i(e + 12),
                        "numLODVertexes": [
                            i(e + 52 + 4 * l) for l in range(MAX_NUM_LODS)],
                    })
                models.append({
                    "name": mname,
                    "numvertices": numvertices,
                    "vertexbase": vertexindex // VSIZE,
                    "meshes": meshes,
                })
            self.bodyparts.append({
                "name": data[bp + nameoff:].split(b"\0")[0].decode("latin-1"),
                "models": models,
            })


# ---------------------------------------------------------------------------
# VVD
# ---------------------------------------------------------------------------
class Vvd:
    """LOD0 vertex pool, fixup-adjusted exactly like Studio_LoadVertexes."""

    def __init__(self, data: bytes, root_lod: int = 0):
        def i(o: int) -> int:
            return struct.unpack_from("<i", data, o)[0]

        self.checksum = i(8)
        self.num_lods = i(12)
        self.lod_vertexes = [i(16 + 4 * l) for l in range(MAX_NUM_LODS)]
        num_fixups = i(48)
        fixup_table = i(52)
        vertex_start = i(56)

        self.count = self.lod_vertexes[root_lod]
        positions = bytearray()
        normals = bytearray()
        uvs = bytearray()

        def emit(raw_index: int) -> None:
            nonlocal positions, normals, uvs
            base = vertex_start + raw_index * VSIZE
            positions += data[base + 16:base + 28]
            normals += data[base + 28:base + 40]
            uvs += data[base + 40:base + 48]

        if num_fixups == 0:
            for k in range(self.count):
                emit(k)
        else:
            target = 0
            for fi in range(num_fixups):
                lod, src, cnt = struct.unpack_from("<3i", data, fixup_table + fi * 12)
                if lod < root_lod:
                    continue
                for j in range(cnt):
                    if target >= self.count:
                        break
                    emit(src + j)
                    target += 1
            # Vertices past the last fixup run keep ascending order (defensive;
            # the engine relies on the table covering the full pool).
            while target < self.count:
                emit(target)
                target += 1

        self.positions = bytes(positions)
        self.normals = bytes(normals)
        self.uvs = bytes(uvs)

    def vertex(self, index: int) -> tuple[tuple[float, float, float],
                                          tuple[float, float, float],
                                          tuple[float, float]]:
        b = index * 12
        pos = struct.unpack_from("<3f", self.positions, b)
        nrm = struct.unpack_from("<3f", self.normals, b)
        uv = struct.unpack_from("<2f", self.uvs, index * 8)
        return pos, nrm, uv


# ---------------------------------------------------------------------------
# VTX (dx90)
# ---------------------------------------------------------------------------
class Vtx:
    def __init__(self, data: bytes):
        self.data = data

        def i(o: int) -> int:
            return struct.unpack_from("<i", data, o)[0]

        self.version = i(0)
        self.vert_cache_size = i(4)
        self.max_bones_per_strip, self.max_bones_per_tri = struct.unpack_from(
            "<HH", data, 8)
        self.max_bones_per_vert = i(12)
        self.checksum = i(16)
        self.num_lods = i(20)
        self.material_replacement_offset = i(24)
        self.num_bodyparts = i(28)
        self.bodypart_offset = i(32)
        if self.version != 7:
            raise MdlError(f"unsupported VTX version {self.version}")

    def root_lod(self, root_lod: int) -> list[list[dict]]:
        """Return, per bodypart, per model, a list of mesh strip data at LOD.

        Each mesh entry is {"strips": [ [tri indices...], ... ]} where the
        indices are mesh-vertex-local (0..mesh.numvertices-1).
        """
        data = self.data

        def i(o: int) -> int:
            return struct.unpack_from("<i", data, o)[0]

        def h(o: int) -> int:
            return struct.unpack_from("<H", data, o)[0]

        result = []
        for b in range(self.num_bodyparts):
            bp = self.bodypart_offset + b * 8
            num_models = i(bp)
            model_offset = i(bp + 4)
            models = []
            for m in range(num_models):
                mo = bp + model_offset + m * 8
                num_lods_model = i(mo)
                lod_offset = i(mo + 4)
                lod = min(root_lod, num_lods_model - 1)
                lo = mo + lod_offset + lod * 12
                num_meshes = i(lo)
                mesh_offset = i(lo + 4)
                meshes = []
                for me in range(num_meshes):
                    mh = lo + mesh_offset + me * 9
                    num_groups = i(mh)
                    group_offset = i(mh + 4)
                    tris: list[int] = []
                    for g in range(num_groups):
                        gh = mh + group_offset + g * 25
                        gnum_verts = i(gh)
                        gvert_offset = i(gh + 4)
                        gnum_indices = i(gh + 8)
                        gindex_offset = i(gh + 12)
                        gnum_strips = i(gh + 16)
                        gstrip_offset = i(gh + 20)
                        gflags = data[gh + 24]
                        # Strip group vertices: Vertex_t = boneWeightIndex[3],
                        # numBones, origMeshVertID u16, boneID[3] = 9 bytes
                        # (packed). We only need origMeshVertID.
                        verts = []
                        vbase = gh + gvert_offset
                        for v in range(gnum_verts):
                            verts.append(
                                struct.unpack_from("<H", data, vbase + v * 9 + 4)[0])
                        for s in range(gnum_strips):
                            sh = gh + gstrip_offset + s * 27
                            snum_indices = i(sh)
                            sindex_offset = i(sh + 4)
                            sflags = data[sh + 18]
                            indices = [
                                h(gh + gindex_offset + (sindex_offset + k) * 2)
                                for k in range(snum_indices)]
                            if sflags & STRIP_IS_TRILIST:
                                tris.extend(verts[j] for j in indices)
                            else:
                                # triangle strip -> triangle list
                                for k in range(len(indices) - 2):
                                    a, b2, c = (verts[j] for j in indices[k:k + 3])
                                    if k & 1:
                                        tris.extend((b2, a, c))
                                    else:
                                        tris.extend((a, b2, c))
                        _ = gflags
                    meshes.append({"strips": tris})
                models.append(meshes)
            result.append(models)
        return result


# ---------------------------------------------------------------------------
# Material / texture resolution
# ---------------------------------------------------------------------------
class Textures:
    def __init__(self, tf_dir: str):
        self.misc = vpk.Vpk(os.path.join(tf_dir, "tf2_misc_dir.vpk"))
        self.tex = vpk.Vpk(os.path.join(tf_dir, "tf2_textures_dir.vpk"))
        self.cache: dict[str, tuple[int, int, bytes] | None] = {}

    def resolve_material(self, name: str) -> str | None:
        """Resolve a texture/material name to a full VMT path in the VPK.

        Bare names (no '/') live next to their .mdl, but the directory does
        not always match the model path (heavy's materials are in
        models/player/hvyweapon), so fall back to a VPK-wide search.
        """
        if name.startswith("models/"):
            return "materials/" + name + ".vmt"
        direct = [
            "materials/" + name + ".vmt",
            "materials/models/" + name + ".vmt",
        ]
        for cand in direct:
            if self.misc.exists(cand):
                return cand
        suffix = "/" + name + ".vmt"
        hits = [k for k in self.misc.entries
                if k.startswith("materials/models/player/") and k.endswith(suffix)]
        return hits[0] if len(hits) == 1 else (hits[0] if hits else None)

    def vmt_basetexture(self, material_path: str) -> str | None:
        try:
            text = self.misc.read(material_path).decode("utf-8", errors="replace")
        except Exception:
            return None
        match = re.search(r"\$basetexture\"?\s*\"?([^\"\s]+)", text)
        if not match:
            # EyeRefract eyeballs have no $basetexture; the iris is close
            # enough for a low-detail viewer.
            match = re.search(r"\$Iris\"?\s*\"?([^\"\s]+)", text)
        return match.group(1) if match else None

    def load(self, texture_name: str) -> tuple[int, int, bytes] | None:
        if texture_name in self.cache:
            return self.cache[texture_name]
        candidates = [
            "materials/" + texture_name + ".vtf",
            "materials/models/" + texture_name + ".vtf",
        ]
        for cand in candidates:
            try:
                data = self.tex.read(cand)
            except Exception:
                continue
            try:
                out = vtf.decode_vtf(data)
            except Exception as exc:
                print(f"  warn: vtf decode failed for {cand}: {exc}", file=sys.stderr)
                continue
            # VTF flag 0x40 = TEXTUREFLAGS_SRGB: pixel values are LINEAR and the
            # engine converts them to sRGB on sample. PNGs are interpreted as
            # sRGB by the viewer, so encode linear data back to sRGB here.
            # (demoman textures carry the flag; scout's don't — same face pixels
            # measured 3x darker without this step.)
            flags = struct.unpack_from("<I", data, 20)[0]
            w, h, rgba = out
            if flags & 0x40:
                rgba = bytes(
                    b if (i & 3) == 3 else round((b / 255.0) ** (1.0 / 2.2) * 255)
                    for i, b in enumerate(rgba))
                out = (w, h, rgba)
            self.cache[texture_name] = out
            return out
        self.cache[texture_name] = None
        return None


# ---------------------------------------------------------------------------
# GLB writer (static mesh, one primitive per material)
# ---------------------------------------------------------------------------
def pad4(data: bytes, fill: bytes = b"\0") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def write_glb(path: str, gltf: dict, bin_blob: bytes) -> None:
    json_blob = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_blob = pad4(bin_blob)
    total = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_blob), 0x004E4942))
        f.write(bin_blob)


def build_glb(mdl: Mdl, vvd: Vvd, vtx_lod: list, textures: Textures,
              skin: list[int], name: str,
              model_dir: str | None = None) -> tuple[dict, bytes]:
    """Build one GLB. Returns (gltf_json, binary).

    Source model space (from the VVD) is right-handed with up = +y, forward
    = +z, left = +x. glTF is Y-up and the viewer expects the model to face
    its local +x (rotation.y = player yaw), so the mapping is
    (x, y, z) -> (z, y, -x)  (forward -> +x, left -> -z).
    """
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []
    images: list[dict] = []
    textures_gltf: list[dict] = []
    materials: list[dict] = []
    bin_chunks: list[bytes] = []
    offset = 0

    used_materials: dict[int, int] = {}  # skin ref -> gltf material index

    def material_for(ref: int) -> int | None:
        if ref in used_materials:
            return used_materials[ref]
        tex_index = skin[ref] if ref < len(skin) else ref
        if not (0 <= tex_index < len(mdl.texture_names)):
            return None
        tex_name = mdl.texture_names[tex_index]
        # Bare texture names (no path) are relative to the model's own
        # material directory; derive that from a fully qualified sibling.
        vmt = textures.resolve_material(tex_name)
        base = textures.vmt_basetexture(vmt) if vmt else None
        if base is None:
            base = tex_name
        img = textures.load(base)
        if img is None:
            return None
        w, h, rgba = img
        png = png_bytes(w, h, rgba)
        bin_chunks.append(png)
        images.append({
            "bufferView": len(bin_chunks) - 1,
            "mimeType": "image/png",
            "name": base.split("/")[-1],
        })
        textures_gltf.append({"source": len(images) - 1})
        used_materials[ref] = len(materials)
        materials.append({
            "name": base.split("/")[-1],
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": len(textures_gltf) - 1},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": True,
        })
        return used_materials[ref]

    primitives: list[dict] = []

    for b, bodypart in enumerate(mdl.bodyparts):
        for m, model in enumerate(bodypart["models"]):
            if not model["meshes"] or model["numvertices"] == 0:
                continue
            mesh_tris = vtx_lod[b][m]
            for me, mesh in enumerate(model["meshes"]):
                count = mesh["numvertices"]
                if count == 0:
                    continue
                base = model["vertexbase"] + mesh["vertexoffset"]
                vstart = len(positions) // 3
                for k in range(count):
                    pos, nrm, uv = vvd.vertex(base + k)
                    positions.extend((pos[2], pos[1], -pos[0]))
                    normals.extend((nrm[2], nrm[1], -nrm[0]))
                    uvs.extend((uv[0], 1.0 - uv[1]))
                tris = mesh_tris[me]["strips"] if me < len(mesh_tris) else []
                valid = []
                max_v = count - 1
                for t in tris:
                    if 0 <= t <= max_v:
                        valid.append(t + vstart)
                if len(valid) < 3:
                    continue
                mat_index = material_for(mesh["material"])
                if mat_index is None:
                    continue
                primitives.append({
                    "attributes": {
                        "POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2,
                    },
                    "indices": None,  # filled below
                    "material": mat_index,
                    "mode": 4,
                    "_indices_local": valid,
                })

    if not primitives:
        raise MdlError("no renderable geometry")

    # Assemble buffers: one POSITION/NORMAL/TEXCOORD accessor, one indices
    # accessor per primitive.
    pos_blob = struct.pack(f"<{len(positions)}f", *positions)
    nrm_blob = struct.pack(f"<{len(normals)}f", *normals)
    uv_blob = struct.pack(f"<{len(uvs)}f", *uvs)
    idx_blobs = []
    for prim in primitives:
        local = prim.pop("_indices_local")
        idx_blobs.append(struct.pack(f"<{len(local)}H", *local))

    bin_data = b"".join(bin_chunks)  # images first (PNG needs 4-byte align)
    image_offsets = []
    cursor = 0
    for chunk in bin_chunks:
        image_offsets.append(cursor)
        cursor += len(pad4(chunk))
    # rebuild with padding
    bin_data = b"".join(pad4(c) for c in bin_chunks)

    def bv(length: int, byte_offset: int, target: int | None) -> dict:
        view: dict = {"buffer": 0, "byteOffset": byte_offset,
                      "byteLength": length}
        if target:
            view["target"] = target
        return view

    buffer_views: list[dict] = []
    accessors: list[dict] = []

    vx = len(positions) // 3
    minp = [min(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]
    maxp = [max(positions[i] for i in range(k, len(positions), 3)) for k in range(3)]

    # vertex attributes
    buffer_views.append(bv(len(pos_blob), cursor, 34962)); cursor += len(pad4(pos_blob))
    buffer_views.append(bv(len(nrm_blob), cursor, 34962)); cursor += len(pad4(nrm_blob))
    buffer_views.append(bv(len(uv_blob), cursor, 34962)); cursor += len(pad4(uv_blob))
    accessors.append({"bufferView": 0, "componentType": 5126, "count": vx,
                      "type": "VEC3", "min": minp, "max": maxp})
    accessors.append({"bufferView": 1, "componentType": 5126, "count": vx, "type": "VEC3"})
    accessors.append({"bufferView": 2, "componentType": 5126, "count": vx, "type": "VEC2"})

    # fix image bufferViews (they were appended before we knew offsets)
    for n, img in enumerate(images):
        img["bufferView"] = len(buffer_views) + n
    for chunk in bin_chunks:
        buffer_views.append(bv(len(chunk), cursor, None))
        cursor += len(pad4(chunk))

    for blob in idx_blobs:
        buffer_views.append(bv(len(blob), cursor, 34963))
        cursor += len(pad4(blob))

    for n, blob in enumerate(idx_blobs):
        prim = primitives[n]
        acc_index = len(accessors)
        accessors.append({"bufferView": len(buffer_views) - len(idx_blobs) + n,
                          "componentType": 5123, "count": len(blob) // 2,
                          "type": "SCALAR"})
        prim["indices"] = acc_index

    # images were placed first in the binary; their bufferViews must point at
    # offsets 0..N. Reorder: rebuild bufferViews properly.
    buffer_views = []
    accessors = []
    cursor = 0

    def push(data_blob: bytes, target: int | None = None) -> int:
        nonlocal cursor
        view_index = len(buffer_views)
        view: dict = {"buffer": 0, "byteOffset": cursor, "byteLength": len(data_blob)}
        if target:
            view["target"] = target
        buffer_views.append(view)
        cursor += len(pad4(data_blob))
        return view_index

    # images
    for n, chunk in enumerate(bin_chunks):
        vi = push(chunk)
        images[n]["bufferView"] = vi

    pvi = push(pos_blob, 34962)
    nvi = push(nrm_blob, 34962)
    uvi = push(uv_blob, 34962)
    acc_pos = len(accessors)
    accessors.append({"bufferView": pvi, "componentType": 5126, "count": vx,
                      "type": "VEC3", "min": minp, "max": maxp})
    accessors.append({"bufferView": nvi, "componentType": 5126, "count": vx, "type": "VEC3"})
    accessors.append({"bufferView": uvi, "componentType": 5126, "count": vx, "type": "VEC2"})

    for prim, blob in zip(primitives, idx_blobs):
        ivi = push(blob, 34963)
        acc = len(accessors)
        accessors.append({"bufferView": ivi, "componentType": 5123,
                          "count": len(blob) // 2, "type": "SCALAR"})
        prim["indices"] = acc

    # final binary = images + attributes + indices in that order
    final_bin = b"".join(pad4(c) for c in bin_chunks) + \
        pad4(pos_blob) + pad4(nrm_blob) + pad4(uv_blob) + \
        b"".join(pad4(b) for b in idx_blobs)

    gltf = {
        "asset": {"version": "2.0", "generator": "tf2-demo mdl2glb.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": [dict(m, alphaMode="OPAQUE") for m in materials],
        "textures": textures_gltf,
        "images": images,
        "samplers": [{"magFilter": 9729, "minFilter": 9987,
                      "wrapS": 10497, "wrapT": 10497}],
        "buffers": [{"byteLength": len(final_bin)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    # default sampler for all textures
    for tex in textures_gltf:
        tex["sampler"] = 0
    return gltf, final_bin


# zlib import here to keep the top clean (only used by png_bytes)
import zlib  # noqa: E402


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    # The VTF alpha channel on player textures holds a specular mask, not
    # opacity — flatten to opaque RGB (color type 2), keep 4-byte input layout.
    stride_in = width * 4
    stride_out = width * 3
    raw = bytearray()
    for y in range(height):
        row = rgba[y * stride_in:(y + 1) * stride_in]
        row = bytearray(row)
        del row[3::4]  # strip alpha channel
        raw.append(0)
        raw += row
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    out += chunk(b"IEND", b"")
    return out


# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: mdl2glb.py <tf_dir> <model_name> <out_prefix>",
              file=sys.stderr)
        return 2
    tf_dir, model_name, out_prefix = argv[1], argv[2], argv[3]

    misc = vpk.Vpk(os.path.join(tf_dir, "tf2_misc_dir.vpk"))
    mdl_data = misc.read(f"{model_name}.mdl")
    vvd_data = misc.read(f"{model_name}.vvd")
    try:
        vtx_data = misc.read(f"{model_name}.dx90.vtx")
    except vpk.VpkError:
        vtx_data = misc.read(f"{model_name}.dx80.vtx")

    mdl = Mdl(mdl_data)
    vvd = Vvd(vvd_data, root_lod=0)
    vtx = Vtx(vtx_data)
    if vtx.checksum != mdl.checksum:
        print(f"warn: vtx checksum {vtx.checksum} != mdl {mdl.checksum}",
              file=sys.stderr)
    print(f"{model_name}: mdl v{mdl.version}, {vvd.count} lod0 verts, "
          f"{len(mdl.texture_names)} textures, {mdl.numskinfamilies} skins",
          file=sys.stderr)

    textures = Textures(tf_dir)
    vtx_lod = vtx.root_lod(0)

    for family, team in ((0, "red"), (1, "blue")):
        if family >= len(mdl.skins):
            print(f"no skin family {family}; skipping {team}", file=sys.stderr)
            continue
        gltf, blob = build_glb(mdl, vvd, vtx_lod, textures,
                               mdl.skins[family], f"{model_name}_{team}",
                               model_dir=model_name)
        out = f"{out_prefix}_{team}.glb"
        write_glb(out, gltf, blob)
        n_tri = sum(
            p["indices"] is not None and
            gltf["accessors"][p["indices"]]["count"] // 3 or 0
            for p in gltf["meshes"][0]["primitives"])
        print(f"wrote {out}: {n_tri} triangles, {len(gltf['images'])} textures",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
