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
import vmt  # noqa: E402

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
        # Works with real VPK sets or a loose extracted directory tree
        # (vpk.open_pack picks whichever is available).
        self.misc = vpk.open_pack(tf_dir, "tf2_misc")
        self.tex = vpk.open_pack(tf_dir, "tf2_textures")
        self.cache: dict[str, tuple[int, int, bytes] | None] = {}

    def resolve_material(self, name: str, model_dir: str | None = None) -> str | None:
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
        if not hits:
            return None
        if len(hits) > 1:
            # Ambiguous bare name: prefer the hit in the model's own material
            # directory, then the shallowest path. HWM duplicates (models/…/hwm/)
            # have a different UV layout and must NOT be picked for lo-d meshes
            # (medic's face rendered black because hwm/medic_head_red.vmt won).
            own = [h for h in hits if model_dir and model_dir in h]
            pool = own or hits
            hits = sorted(pool, key=lambda h: h.count("/"))
        return hits[0]

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
        if not match:
            return None
        # A few VMTs (mostly prop materials) spell the value with a backslash
        # separator or a `.vtf` extension; normalise to the VPK lookup form.
        value = match.group(1).replace("\\", "/")
        if value.lower().endswith(".vtf"):
            value = value[:-4]
        return value

    def alpha_mode(self, material_path: str | None) -> tuple[str, float | None]:
        """Return `(alphaMode, alphaCutoff)` for a material's VMT.

        `$translucent`, `$alpha` and `$decal` map to glTF BLEND, `$alphatest`
        to MASK with the VMT's `$alphatestreference` as cutoff. Unknown or
        unreadable VMTs stay OPAQUE, matching the historical behaviour.
        """
        if not material_path:
            return ("OPAQUE", None)
        try:
            text = self.misc.read(material_path).decode("latin-1")
            try:
                mat = vmt.resolve(text, self.misc)
            except vmt.VmtError:
                mat = vmt.parse(text)
        except Exception:
            return ("OPAQUE", None)
        if vmt.is_translucent(mat) or vmt.is_decal(mat):
            return ("BLEND", None)
        if vmt.is_alpha_tested(mat):
            return ("MASK", vmt.alpha_test_reference(mat))
        return ("OPAQUE", None)

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
              model_dir: str | None = None,
              keep_alpha: bool = False,
              max_tex_size: int | None = None) -> tuple[dict, bytes]:
    """Build one GLB. Returns (gltf_json, binary).

    Source model space (from the VVD) is right-handed with up = +y, forward
    = +z, left = +x. glTF is Y-up and the viewer expects the model to face
    its local +x (rotation.y = player yaw), so the mapping is
    (x, y, z) -> (z, y, -x)  (forward -> +x, left -> -z).

    `keep_alpha` is the prop-model mode: when set, the VMT alpha flags decide
    the glTF alphaMode, and only translucent/alpha-tested materials keep the
    VTF alpha channel in the PNG. The player path (default) flattens alpha to
    opaque RGB because there it is a specular mask, not opacity.

    `max_tex_size` (pixels) caps the embedded texture resolution, preserving
    the aspect ratio and never upscaling. `None` (the player default) embeds
    the native mip0 size.
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
        vmt_path = textures.resolve_material(tex_name, model_dir)
        base = textures.vmt_basetexture(vmt_path) if vmt_path else None
        if base is None:
            base = tex_name
        img = textures.load(base)
        if img is None:
            return None
        w, h, rgba = img
        mode, cutoff = ("OPAQUE", None)
        if keep_alpha:
            mode, cutoff = textures.alpha_mode(vmt_path)
        png = png_bytes(w, h, rgba, keep_alpha=keep_alpha and mode != "OPAQUE",
                        max_size=max_tex_size)
        bin_chunks.append(png)
        images.append({
            "bufferView": len(bin_chunks) - 1,
            "mimeType": "image/png",
            "name": base.split("/")[-1],
        })
        textures_gltf.append({"source": len(images) - 1})
        used_materials[ref] = len(materials)
        material = {
            "name": base.split("/")[-1],
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": len(textures_gltf) - 1},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": True,
            "alphaMode": mode,
        }
        if mode == "MASK" and cutoff is not None:
            material["alphaCutoff"] = cutoff
        materials.append(material)
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
                    # Source is D3D-based: UV origin is top-left, v grows downward
                    # — the same convention glTF uses. No V flip (verified against
                    # medic head: face texels bright only with row = v*h).
                    uvs.extend(uv)
                tris = mesh_tris[me]["strips"] if me < len(mesh_tris) else []
                valid = []
                valid_out = []
                max_v = count - 1
                for t in tris:
                    if 0 <= t <= max_v:
                        valid.append(t + vstart)
                if len(valid) < 3:
                    continue
                # Source/D3D strips are CW front faces; glTF expects CCW.
                # Emit (a, c, b) to flip winding — verified against vertex normals:
                # without this the meshes render inside-out (black faces).
                it = iter(valid)
                for a, b2, c in zip(it, it, it):
                    valid_out.extend((a, c, b2))
                valid = valid_out
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
        "materials": [dict(m) for m in materials],
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


def resize_rgba(width: int, height: int, rgba: bytes,
                new_width: int, new_height: int) -> bytes:
    """Bilinear RGBA resize, interpolating in premultiplied alpha space.

    Downscaling only (callers must not request an upscale). Premultiplying
    keeps fully transparent texels from bleeding their (undefined) colour
    into visible ones, which would produce dark fringes on cutout props such
    as foliage. Edge pixels are clamped.
    """
    if (new_width, new_height) == (width, height):
        return rgba
    # Precompute horizontal taps for every output column.
    xmap: list[tuple[int, int, float]] = []
    for x in range(new_width):
        sx = (x + 0.5) * width / new_width - 0.5
        x0 = math.floor(sx)
        fx = sx - x0
        if x0 < 0:
            x0, fx = 0, 0.0
        elif x0 >= width - 1:
            x0, fx = width - 1, 0.0
        xmap.append((x0, min(x0 + 1, width - 1), fx))

    src = rgba
    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        sy = (y + 0.5) * height / new_height - 0.5
        y0 = math.floor(sy)
        fy = sy - y0
        if y0 < 0:
            y0, fy = 0, 0.0
        elif y0 >= height - 1:
            y0, fy = height - 1, 0.0
        y1 = min(y0 + 1, height - 1)
        row0 = y0 * width * 4
        row1 = y1 * width * 4
        out_row = y * new_width * 4
        for x, (x0, x1, fx) in enumerate(xmap):
            i00 = row0 + x0 * 4
            i10 = row0 + x1 * 4
            i01 = row1 + x0 * 4
            i11 = row1 + x1 * 4
            w00 = (1.0 - fx) * (1.0 - fy)
            w10 = fx * (1.0 - fy)
            w01 = (1.0 - fx) * fy
            w11 = fx * fy
            a00 = src[i00 + 3]
            a10 = src[i10 + 3]
            a01 = src[i01 + 3]
            a11 = src[i11 + 3]
            den = w00 * a00 + w10 * a10 + w01 * a01 + w11 * a11
            o = out_row + x * 4
            if den <= 0.0:
                out[o:o + 4] = b"\0\0\0\0"
                continue
            out[o] = round((w00 * src[i00] * a00 + w10 * src[i10] * a10 +
                            w01 * src[i01] * a01 + w11 * src[i11] * a11) / den)
            out[o + 1] = round((w00 * src[i00 + 1] * a00 +
                                w10 * src[i10 + 1] * a10 +
                                w01 * src[i01 + 1] * a01 +
                                w11 * src[i11 + 1] * a11) / den)
            out[o + 2] = round((w00 * src[i00 + 2] * a00 +
                                w10 * src[i10 + 2] * a10 +
                                w01 * src[i01 + 2] * a01 +
                                w11 * src[i11 + 2] * a11) / den)
            out[o + 3] = round(den)
    return bytes(out)


def png_bytes(width: int, height: int, rgba: bytes,
              keep_alpha: bool = False,
              max_size: int | None = None) -> bytes:
    if max_size and max(width, height) > max_size:
        scale = max_size / float(max(width, height))
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        rgba = resize_rgba(width, height, rgba, new_width, new_height)
        width, height = new_width, new_height

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    if keep_alpha:
        # Prop materials: the VTF alpha channel is real opacity, so emit RGBA
        # (color type 6) and let the material's alphaMode use it.
        color_type = 6
        stride = width * 4
        raw = bytearray()
        for y in range(height):
            raw.append(0)
            raw += rgba[y * stride:(y + 1) * stride]
    else:
        # The VTF alpha channel on player textures holds a specular mask, not
        # opacity — flatten to opaque RGB (color type 2), keep 4-byte input
        # layout.
        color_type = 2
        stride_in = width * 4
        raw = bytearray()
        for y in range(height):
            row = rgba[y * stride_in:(y + 1) * stride_in]
            row = bytearray(row)
            del row[3::4]  # strip alpha channel
            raw.append(0)
            raw += row
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type,
                                      0, 0, 0))
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

    misc = vpk.open_pack(tf_dir, "tf2_misc")
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
