#!/usr/bin/env python3
"""Convert Source BSP v20 render geometry to a single vertex-coloured GLB.

Usage: python3 tools/bsp2glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb

No external dependencies.  Handles both plain Source BSP lumps and
bspzip/LZMA-compressed lumps (the pl_upward.bsp shipped here is compressed).
Displacements are built with the standard Source grid algorithm; flat brush
faces are used as a fallback if a displacement is malformed.
"""
from __future__ import annotations

import json
import lzma
import math
import os
import struct
import sys
from array import array

# ---------------------------------------------------------------------------
# BSP lumps
# ---------------------------------------------------------------------------
LUMP_ENTITIES = 0
LUMP_PLANES = 1
LUMP_TEXDATA = 2
LUMP_VERTEXES = 3
LUMP_TEXINFO = 6
LUMP_FACES = 7
LUMP_EDGES = 12
LUMP_SURFEDGES = 13
LUMP_MODELS = 14
LUMP_DISPINFO = 26
LUMP_DISP_VERTS = 33
LUMP_TEXDATA_STRING_DATA = 43
LUMP_TEXDATA_STRING_TABLE = 44
LUMP_LIGHTING = 8

# dface_t v20, 56 bytes (verified against the lump length / entry count)
FACE_STRUCT = struct.Struct("<HBBihhhh4Bif4iiHHI")
assert FACE_STRUCT.size == 56

# texinfo_t is 72 bytes: 4*4 texture vecs, 4*4 lightmap vecs, flags, texdata
TEXINFO_STRUCT = struct.Struct("<16fii")
assert TEXINFO_STRUCT.size == 72

DISPINFO_STRUCT = struct.Struct("<3fiiii")  # start, firstvert, firsttri, power, minTess
DISPINFO_SIZE = 176
DISPVERT_STRUCT = struct.Struct("<5f")  # vec3, dist, alpha

# texdata_t is 32 bytes: reflectivity vec3, nameStringTableID, width, height,
# view_width, view_height. Width/height are needed to normalise the texinfo
# texture vectors into glTF UV space.
TEXDATA_SIZE = 32

HEADER_LUMP_STRUCT = struct.Struct("<4i")  # fileofs, filelen, version, uncompressed


class Bsp:
    def __init__(self, path: str):
        with open(path, "rb") as f:
            self.data = f.read()
        self.ident, self.version = struct.unpack_from("<4si", self.data, 0)
        if self.ident != b"VBSP":
            raise SystemExit(f"not a Source BSP: ident={self.ident!r}")
        if self.version != 20:
            print(f"warning: BSP version {self.version}, expected 20", file=sys.stderr)
        self.lumps = [HEADER_LUMP_STRUCT.unpack_from(self.data, 8 + 16 * i) for i in range(64)]

    def lump(self, index: int) -> bytes:
        offset, length, _version, uncompressed = self.lumps[index]
        if length <= 0 or offset <= 0 or offset >= len(self.data):
            return b""
        raw = self.data[offset:min(offset + length, len(self.data))]
        if raw[:4] == b"LZMA":
            if len(raw) < 17:
                raise SystemExit(f"truncated LZMA lump {index}")
            _magic, actual, _compressed = struct.unpack_from("<4sII", raw, 0)
            props = raw[12:17]
            # props = lc/lp/pb byte + 4-byte little-endian dictionary size
            lc = props[0] % 9
            rest = props[0] // 9
            lp = rest % 5
            pb = rest // 5
            dict_size = int.from_bytes(props[1:5], "little")
            if dict_size < (1 << 16):
                dict_size = 1 << 24
            filters = [{
                "id": lzma.FILTER_LZMA1,
                "dict_size": dict_size,
                "lc": lc,
                "lp": lp,
                "pb": pb,
            }]
            out = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(raw[17:])
            expected = uncompressed or actual
            if expected:
                out = out[:expected]
            return out
        # uncompressed lump
        if uncompressed and length != uncompressed:
            return raw[:length]
        return raw

    def faces(self):
        raw = self.lump(LUMP_FACES)
        for i in range(0, len(raw) - FACE_STRUCT.size + 1, FACE_STRUCT.size):
            yield FACE_STRUCT.unpack_from(raw, i)

    def planes(self):
        raw = self.lump(LUMP_PLANES)
        return [struct.unpack_from("<4fi", raw, i) for i in range(0, len(raw) - 19, 20)]

    def vertices(self):
        raw = self.lump(LUMP_VERTEXES)
        a = array("f")
        a.frombytes(raw[: len(raw) // 12 * 12])
        if sys.byteorder != "little":
            a.byteswap()
        return a

    def edges(self):
        raw = self.lump(LUMP_EDGES)
        a = array("H")
        a.frombytes(raw[: len(raw) // 4 * 4])
        if sys.byteorder != "little":
            a.byteswap()
        return a

    def surfedges(self):
        raw = self.lump(LUMP_SURFEDGES)
        a = array("i")
        a.frombytes(raw[: len(raw) // 4 * 4])
        if sys.byteorder != "little":
            a.byteswap()
        return a

    def texture_names(self):
        names = []
        string_data = self.lump(LUMP_TEXDATA_STRING_DATA)
        table = self.lump(LUMP_TEXDATA_STRING_TABLE)
        for i in range(0, len(table) - 3, 4):
            off = struct.unpack_from("<i", table, i)[0]
            if off < 0 or off >= len(string_data):
                names.append("")
                continue
            end = string_data.find(b"\0", off)
            if end < 0:
                end = len(string_data)
            names.append(string_data[off:end].decode("latin-1"))
        return names

    def texdata_entries(self):
        """Return [(name_string_index, width, height)] per texdata_t."""
        raw = self.lump(LUMP_TEXDATA)
        out = []
        for i in range(0, len(raw) - TEXDATA_SIZE + 1, TEXDATA_SIZE):
            name_index = struct.unpack_from("<i", raw, i + 12)[0]
            width, height = struct.unpack_from("<ii", raw, i + 16)
            out.append((name_index, width, height))
        return out


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
def palette_color(name: str):
    """Return (r, g, b) or None for skipped tool/sky/nodraw materials."""
    n = name.lower()
    if "nodraw" in n or "tool" in n or "sky" in n:
        return None
    if "wood" in n:
        return (0.46, 0.29, 0.14)
    if "metal" in n:
        return (0.55, 0.57, 0.60)
    if "concrete" in n:
        return (0.72, 0.71, 0.67)
    if "rock" in n:
        return (0.32, 0.31, 0.30)
    if "grass" in n:
        return (0.34, 0.49, 0.22)
    if "dirt" in n:
        return (0.40, 0.29, 0.17)
    if "blend" in n:
        return (0.30, 0.44, 0.20)
    if "glass" in n:
        return (0.35, 0.55, 0.75)
    if "water" in n:
        return (0.16, 0.34, 0.62)
    if "brick" in n:
        return (0.52, 0.27, 0.21)
    if "red" in n:
        return (0.56, 0.22, 0.18)
    if "blue" in n:
        return (0.24, 0.31, 0.55)
    if "coal" in n:
        return (0.20, 0.20, 0.21)
    return (0.50, 0.50, 0.50)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def normalize(v):
    length = math.sqrt(dot(v, v))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def displacement_triangles(power: int):
    """Indices for a Source displacement grid (same order as bsp_tool)."""
    power2 = 2 ** power
    a = power2 + 1
    b = power2 + 2
    c = power2 + 3
    tris = []
    for line in range(power2):
        line_offset = a * line
        for block in range(2 ** (power - 1)):
            off = line_offset + 2 * block
            if line % 2 == 0:  # |\|/|
                tris.append((off + 0, off + a, off + 1))
                tris.append((off + a, off + b, off + 1))
                tris.append((off + b, off + c, off + 1))
                tris.append((off + c, off + 2, off + 1))
            else:  # |/|\|
                tris.append((off + 0, off + a, off + b))
                tris.append((off + 1, off + 0, off + b))
                tris.append((off + 2, off + 1, off + b))
                tris.append((off + c, off + 2, off + b))
    return tris


# ---------------------------------------------------------------------------
# Lightmap atlas
# ---------------------------------------------------------------------------
LM_ATLAS = 2048          # atlas side in texels (~1.2M luxels on upward)
LM_GUTTER = 1            # 1px border against linear-filter bleeding


def pack_lightmaps(rects):
    """Shelf-pack face lightmap rects into one atlas.

    `rects` is [(key, w, h), ...]. Returns {key: (x, y, w, h)} in atlas
    texels, where (x, y) is the first LUXEL of the cell (the gutter sits at
    x-1/y-1). Unpackable rects (larger than the atlas) are absent; callers
    must treat them as unlit.
    """
    placed = {}
    rows = []  # [y, height, cursor_x]
    x = y = 0
    row_h = 0
    for key, w, h in sorted(rects, key=lambda r: -r[2]):
        if w > LM_ATLAS or h > LM_ATLAS:
            continue
        if x + w > LM_ATLAS:  # start a new shelf
            y += row_h
            x = 0
            row_h = 0
        if y + h > LM_ATLAS:
            continue  # atlas exhausted; face stays unlit
        placed[key] = (x + LM_GUTTER, y + LM_GUTTER, w, h)
        x += w + LM_GUTTER
        row_h = max(row_h, h + LM_GUTTER)
    return placed


def build_lightmap_atlas(bsp, placed, all_faces, atlas_buf):
    """Decode each placed face's lightmap rect into the atlas RGB buffer.

    `placed` maps face_index -> (ax, ay, w, h) from pack_lightmaps();
    `atlas_buf` is a mutable bytearray(LM_ATLAS*LM_ATLAS*3), pre-initialised
    (the caller writes the reserved white cell before calling). Luxels are
    BGRA; the atlas stores RGB.
    """
    lm = bsp.lump(LUMP_LIGHTING)
    atlas = atlas_buf
    for fi, (ax, ay, w, h) in placed.items():
        face = all_faces[fi]
        lo = face[12]
        if lo < 0:
            continue
        for j in range(h):
            src = lo + j * face[16] * 4
            dst = ((ay + j) * LM_ATLAS + ax) * 3
            for k in range(w):
                atlas[dst + k * 3] = lm[src + k * 4 + 2]
                atlas[dst + k * 3 + 1] = lm[src + k * 4 + 1]
                atlas[dst + k * 3 + 2] = lm[src + k * 4]
    return atlas_buf


def write_png_rgb_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal PNG writer for 8-bit RGB (no alpha) buffers."""
    import zlib
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * width * 3:(y + 1) * width * 3]
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    return bytes(png)


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------
class MeshBuilder:
    def __init__(self):
        self.positions = array("f")
        self.normals = array("f")
        self.colors = array("f")
        self.uvs = array("f")
        self.lmap = array("f")   # TEXCOORD_1: lightmap atlas coords
        self.indices = array("I")
        self.vertex_count = 0
        self.min = [float("inf")] * 3
        self.max = [float("-inf")] * 3

    def add_vertex(self, p, n, c, uv, lmap):
        self.positions.extend(p)
        self.normals.extend(n)
        self.colors.extend(c)
        self.uvs.extend(uv)
        self.lmap.extend(lmap)
        for i in range(3):
            if p[i] < self.min[i]:
                self.min[i] = p[i]
            if p[i] > self.max[i]:
                self.max[i] = p[i]
        self.vertex_count += 1
        return self.vertex_count - 1

    def add_triangle(self, a, b, c, reference_normal):
        pa = self.positions[a * 3:a * 3 + 3]
        pb = self.positions[b * 3:b * 3 + 3]
        pc = self.positions[c * 3:c * 3 + 3]
        n = cross(sub(pb, pa), sub(pc, pa))
        if dot(n, reference_normal) < 0:
            self.indices.extend((a, c, b))
        else:
            self.indices.extend((a, b, c))


def clean_polygon(poly, normal):
    """Drop near-duplicate and near-collinear vertices from a face polygon."""
    EPS_DUP2 = 0.05   # squared distance below which points are the same
    EPS_AREA2 = 1.0   # squared cross product below which 3 points are collinear
    # remove consecutive duplicates
    pts = []
    for p in poly:
        if pts and dist2(pts[-1], p) < EPS_DUP2:
            continue
        pts.append(p)
    while len(pts) > 1 and dist2(pts[0], pts[-1]) < EPS_DUP2:
        pts.pop()
    if len(pts) < 3:
        return pts
    # remove collinear middle points (on the dominant plane)
    axis = max(range(3), key=lambda i: abs(normal[i]))
    u, v = [i for i in range(3) if i != axis]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            ab = (b[u] - a[u], b[v] - a[v])
            bc = (c[u] - b[u], c[v] - b[v])
            cr = ab[0] * bc[1] - ab[1] * bc[0]
            if cr * cr < EPS_AREA2:
                del pts[i]
                changed = True
                break
    return pts


def ear_clip(poly, normal):
    """Ear-clip a polygon projected onto its dominant plane.

    Returns index triples into `poly`. Zero-area ears are skipped; for
    robustness, falls back to a fan when clipping stalls.
    """
    axis = max(range(3), key=lambda i: abs(normal[i]))
    u, v = [i for i in range(3) if i != axis]
    n = len(poly)
    idx = list(range(n))
    out = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        clipped = False
        for k in range(len(idx)):
            i0 = idx[k - 1]
            i1 = idx[k]
            i2 = idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            ab = (b[u] - a[u], b[v] - a[v])
            ac = (c[u] - a[u], c[v] - a[v])
            cr = ab[0] * ac[1] - ab[1] * ac[0]
            # orientation must match the polygon winding
            if cr * normal[axis] <= 1e-6:
                continue  # reflex or degenerate ear
            if any(_point_in_tri(poly[j], a, b, c, u, v)
                   for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            del idx[k]
            clipped = True
            break
        if not clipped:
            break  # fall through to fan
    # remaining vertices (fan) — usually just the final triangle
    for k in range(1, len(idx) - 1):
        out.append((idx[0], idx[k], idx[k + 1]))
    return out


def _point_in_tri(p, a, b, c, u, v):
    def sign(p1, p2, p3):
        return (p1[u] - p3[u]) * (p2[v] - p3[v]) - (p2[u] - p3[u]) * (p1[v] - p3[v])
    d1, d2, d3 = sign(p, a, b), sign(p, b, c), sign(p, c, a)
    has_neg = d1 < -1e-6 or d2 < -1e-6 or d3 < -1e-6
    has_pos = d1 > 1e-6 or d2 > 1e-6 or d3 > 1e-6
    return not (has_neg and has_pos)


def build(bsp: Bsp) -> dict[str, MeshBuilder]:
    planes = bsp.planes()
    vertices = bsp.vertices()
    edges = bsp.edges()
    surfedges = bsp.surfedges()
    texinfo_raw = bsp.lump(LUMP_TEXINFO)
    tex_names = bsp.texture_names()
    texdata = bsp.texdata_entries()
    dispinfo_raw = bsp.lump(LUMP_DISPINFO)
    dispverts_raw = bsp.lump(LUMP_DISP_VERTS)

    # texinfo index -> (skip, color, texture name, texinfo texture vectors).
    # texinfo_t: 8 floats of texture vecs (s.xyz, s.shift, t.xyz, t.shift),
    # 8 floats of lightmap vecs, flags, texdata index.
    texinfo_cache = {}
    for ti in range(len(texinfo_raw) // TEXINFO_STRUCT.size):
        vals = TEXINFO_STRUCT.unpack_from(texinfo_raw, ti * TEXINFO_STRUCT.size)
        texdata_index = vals[-1]
        if 0 <= texdata_index < len(texdata):
            name_index, width, height = texdata[texdata_index]
        else:
            name_index, width, height = -1, 0, 0
        name = (tex_names[name_index]
                if 0 <= name_index < len(tex_names) else "")
        s_vec = (vals[0], vals[1], vals[2], vals[3])
        t_vec = (vals[4], vals[5], vals[6], vals[7])
        lm_s = (vals[8], vals[9], vals[10], vals[11])
        lm_t = (vals[12], vals[13], vals[14], vals[15])
        texinfo_cache[ti] = (
            palette_color(name), name, s_vec, t_vec, lm_s, lm_t,
            float(width or 1), float(height or 1),
        )

    def make_uv(texinfo):
        """World position -> normalised texture UV (Source texinfo projection).

        u = (P . s + s.shift) / texWidth, v = (P . t + t.shift) / texHeight.
        Source textures use a top-left origin, which matches glTF's UV
        convention (GLTFLoader loads images with flipY = false), so no V flip.
        """
        s_vec, t_vec, width, height = texinfo
        sx, sy, sz, s_shift = s_vec
        tx, ty, tz, t_shift = t_vec

        def uv(p):
            return ((p[0] * sx + p[1] * sy + p[2] * sz + s_shift) / width,
                    (p[0] * tx + p[1] * ty + p[2] * tz + t_shift) / height)

        return uv

    builders: dict[str, MeshBuilder] = {}
    skipped_faces = 0
    skipped_outside = 0
    flat_faces = 0
    disp_faces = 0
    fallback_faces = 0

    # Face index -> atlas cell (ax, ay, w, h), filled after face iteration
    # (see the lightmap pass below).
    lm_cells: dict[int, tuple] = {}

    def make_lmap(face_index, face, lm_s, lm_t):
        """World position -> lightmap ATLAS UV (TEXCOORD_1).

        Luxel coords come from the texinfo lightmap vectors
        (u = P·s + shift, in luxel units); u/v are clamped to the face's
        [0, size-1] luxel rect, then mapped into the face's atlas cell with
        the half-luxel texel-centre offset. Unlit faces map to the reserved
        white 2x2 cell at the atlas origin.
        """
        cell = lm_cells.get(face_index)
        sx, sy = face[16], face[17]
        lux = (lm_s[0], lm_s[1], lm_s[2])
        luy = (lm_s[3],)
        ltv = (lm_t[0], lm_t[1], lm_t[2])
        lty = (lm_t[3],)

        def lmap(p):
            if cell is None:
                return (WHITE_CELL_UV[0], WHITE_CELL_UV[1])
            u = p[0] * lux[0] + p[1] * lux[1] + p[2] * lux[2] + luy[0]
            v = p[0] * ltv[0] + p[1] * ltv[1] + p[2] * ltv[2] + lty[0]
            u = max(0.0, min(float(sx - 1), u))
            v = max(0.0, min(float(sy - 1), v))
            ax, ay, _w, _h = cell
            # +0.5: texel-centre sampling of the luxel grid inside the atlas
            return ((ax + 0.5 + u) / LM_ATLAS, (ay + 0.5 + v) / LM_ATLAS)

        return lmap

    # Stray 3D-skybox geometry lives far outside the playable world; including
    # it would destroy the bounding box.  Use the worldspawn world_mins/maxs
    # entity bounds when present (they exclude the 3D skybox by definition),
    # falling back to generous hard constants.  Drop a face only when ALL of
    # its vertices are outside the bounds (with a small margin for seams).
    # Read worldspawn world_mins/world_maxs: the authoritative playable bounds
    # that exclude 3D-skybox geometry (which sits far outside them).
    def parse_world_bounds():
        import re as _re
        raw = bsp.lump(LUMP_ENTITIES).decode("utf-8", errors="replace")
        def grab(key):
            m = _re.search(r'"%s"\s+"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)"' % key, raw)
            return [float(m.group(i)) for i in (1, 2, 3)] if m else None
        mins = grab("world_mins")
        maxs = grab("world_maxs")
        if mins and maxs:
            return mins, maxs
        return None

    world_min = [-12000.0, -12000.0, -2500.0]
    world_max = [12000.0, 12000.0, 3500.0]
    _bounds = parse_world_bounds()
    if _bounds:
        _mins, _maxs = _bounds
        # Intersect with the sane constants: some maps seal their 3D skybox
        # inside worldspawn bounds (upward), so worldspawn alone is too wide;
        # others (dustbowl) keep the skybox outside them, so worldspawn alone
        # is the precise cut. The intersection handles both.
        world_min = [max(a, b) for a, b in zip(world_min, _mins)]
        world_max = [min(a, b) for a, b in zip(world_max, _maxs)]

    def outside_world(poly):
        for x, y, z in poly:
            if (world_min[0] - 64 <= x <= world_max[0] + 64
                    and world_min[1] - 64 <= y <= world_max[1] + 64
                    and world_min[2] - 64 <= z <= world_max[2] + 64):
                return False
        return True

    def face_polygon(face):
        first_edge, num_edges = face[3], face[4]
        if num_edges < 3 or num_edges > 1024:
            return []
        out = []
        for j in range(num_edges):
            se = surfedges[first_edge + j]
            edge_index = se if se >= 0 else -se
            if edge_index >= len(edges) // 2:
                return []
            e0 = edges[edge_index * 2]
            e1 = edges[edge_index * 2 + 1]
            vi = e0 if se >= 0 else e1
            if vi >= len(vertices) // 3:
                return []
            out.append((vertices[vi * 3], vertices[vi * 3 + 1], vertices[vi * 3 + 2]))
        return out

    def add_flat(face, poly, normal, color, uv_of, lmap_of, mesh):
        # Brush faces accumulate near-duplicate and collinear vertices from
        # plane splits; a naive fan over them emits zero-area triangles and
        # long needle slivers that stick out of the surface (visible as
        # spikes). Clean the polygon, then ear-clip on the dominant plane.
        cleaned = clean_polygon(poly, normal)
        if len(cleaned) < 3:
            return
        base = mesh.vertex_count
        for p in cleaned:
            mesh.add_vertex(p, normal, color, uv_of(p), lmap_of(p))
        for tri in ear_clip(cleaned, normal):
            mesh.add_triangle(base + tri[0], base + tri[1],
                              base + tri[2], normal)

    def add_displacement(face, poly, face_normal, color, uv_of, lmap_of, mesh):
        disp_index = face[6]
        start = disp_index * DISPINFO_SIZE
        if start + DISPINFO_STRUCT.size > len(dispinfo_raw) or len(poly) != 4:
            return False
        sx, sy, sz, first_vert, _first_tri, power, _min_tess = DISPINFO_STRUCT.unpack_from(
            dispinfo_raw, start)
        if power < 2 or power > 4:
            return False
        grid = (1 << power) + 1
        if first_vert < 0 or (first_vert + grid * grid) * 20 > len(dispverts_raw):
            return False

        # Rotate the face quad so the corner closest to start_position is first.
        start_pos = (sx, sy, sz)
        best = min(range(4), key=lambda i: dot(sub(poly[i], start_pos), sub(poly[i], start_pos)))
        quad = poly[best:] + poly[:best]
        A, B, C, D = quad
        steps = 1 << power

        positions = []
        offsets = []
        for i in range(grid * grid):
            t1 = (i % grid) / steps
            t2 = (i // grid) / steps
            # bilinear: along A->D for t1, and A->B / D->C for t2
            ad = (A[0] + (D[0] - A[0]) * t1,
                  A[1] + (D[1] - A[1]) * t1,
                  A[2] + (D[2] - A[2]) * t1)
            bc = (B[0] + (C[0] - B[0]) * t1,
                  B[1] + (C[1] - B[1]) * t1,
                  B[2] + (C[2] - B[2]) * t1)
            base_p = (ad[0] + (bc[0] - ad[0]) * t2,
                      ad[1] + (bc[1] - ad[1]) * t2,
                      ad[2] + (bc[2] - ad[2]) * t2)
            vec = DISPVERT_STRUCT.unpack_from(dispverts_raw, (first_vert + i) * 20)
            offset = (vec[0] * vec[3], vec[1] * vec[3], vec[2] * vec[3])
            positions.append((base_p[0] + offset[0],
                              base_p[1] + offset[1],
                              base_p[2] + offset[2]))
            offsets.append(offset)

        # Smooth grid normals from neighbouring positions.
        normals = []
        for y in range(grid):
            for x in range(grid):
                i = y * grid + x
                xl = max(0, x - 1)
                xr = min(grid - 1, x + 1)
                yl = max(0, y - 1)
                yr = min(grid - 1, y + 1)
                tu = sub(positions[y * grid + xr], positions[y * grid + xl])
                tv = sub(positions[yr * grid + x], positions[yl * grid + x])
                n = cross(tu, tv)
                if dot(n, n) < 1e-12:
                    n = face_normal
                elif dot(n, face_normal) < 0:
                    n = (-n[0], -n[1], -n[2])
                normals.append(normalize(n))

        base = mesh.vertex_count
        for i, p in enumerate(positions):
            mesh.add_vertex(p, normals[i], color, uv_of(p), lmap_of(p))
        for tri in displacement_triangles(power):
            a, b, c = tri
            ref = normals[a]
            mesh.add_triangle(base + a, base + b, base + c, ref)
        return True

    all_faces = list(bsp.faces())

    # --- Lightmap atlas pass (must run before vertex emission) -------------
    # Collect the faces that will actually emit geometry (same filters as the
    # main loop), pack their lightmap rects, then emit TEXCOORD_1 alongside
    # TEXCOORD_0 below.
    def face_passes_filters(face, info):
        if info is None or info[0] is None:
            return False
        plane_index = face[0]
        if plane_index >= len(planes):
            return False
        poly = face_polygon(face)
        if len(poly) < 3:
            return False
        if outside_world(poly):
            return False
        return True

    emitted_indices = []
    for fi, face in enumerate(all_faces):
        info = texinfo_cache.get(face[5])
        if not face_passes_filters(face, info):
            continue
        emitted_indices.append(fi)

    # Reserve a 2x2 white cell at (0,0) for unlit faces (lightofs = -1 or
    # unpackable rects); write its RGB first.
    white_cell = bytearray(LM_ATLAS * LM_ATLAS * 3)
    for j in range(2):
        for i in range(2):
            k = ((j) * LM_ATLAS + i) * 3
            white_cell[k] = 255
            white_cell[k + 1] = 255
            white_cell[k + 2] = 255

    WHITE_CELL_UV = (1.0 / LM_ATLAS, 1.0 / LM_ATLAS)

    rects = []
    for fi in emitted_indices:
        face = all_faces[fi]
        if face[12] >= 0 and 0 < face[16] <= LM_ATLAS and 0 < face[17] <= LM_ATLAS:
            rects.append((fi, face[16], face[17]))
    placed = pack_lightmaps(rects)
    lm_cells.update(placed)

    build_lightmap_atlas(bsp, placed, all_faces, white_cell)
    atlas_bytes = bytes(white_cell)

    for fi, face in enumerate(all_faces):
        tex_info = face[5]
        info = texinfo_cache.get(tex_info)
        if info is None or info[0] is None:
            skipped_faces += 1
            continue
        color, name, s_vec, t_vec, lm_s, lm_t, width, height = info
        mesh = builders.get(name)
        if mesh is None:
            mesh = builders[name] = MeshBuilder()
        uv_of = make_uv((s_vec, t_vec, width, height))
        lmap_of = make_lmap(fi, face, lm_s, lm_t)
        plane_index = face[0]
        if plane_index >= len(planes):
            continue
        plane = planes[plane_index]
        face_normal = (plane[0], plane[1], plane[2])
        if face[1] == 1:  # side
            face_normal = (-face_normal[0], -face_normal[1], -face_normal[2])
        face_normal = normalize(face_normal)
        poly = face_polygon(face)
        if len(poly) < 3:
            continue
        if outside_world(poly):
            skipped_outside += 1
            continue
        if face[6] >= 0:
            if add_displacement(face, poly, face_normal, color, uv_of, lmap_of, mesh):
                disp_faces += 1
                continue
            fallback_faces += 1
        add_flat(face, poly, face_normal, color, uv_of, lmap_of, mesh)
        flat_faces += 1

    print(f"faces: {flat_faces} flat, {disp_faces} displaced, "
          f"{fallback_faces} displacement fallbacks, {skipped_faces} skipped tool/sky, "
          f"{skipped_outside} skipped out-of-world, {len(builders)} materials, "
          f"{len(placed)} lightmapped",
          file=sys.stderr)
    return builders, atlas_bytes


# ---------------------------------------------------------------------------
# GLB writer
# ---------------------------------------------------------------------------
def pad4(data: bytes, fill: bytes = b"\0") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def write_glb(path: str, builders: dict[str, MeshBuilder], name: str = "pl_upward") -> None:
    """Write one mesh with one primitive per material (texture) name.

    Splitting per material is what lets the viewer (and later the texture
    step) bind a separate map per brush surface while keeping the geometry in
    a single file. Every builder carries POSITION/NORMAL/COLOR_0/TEXCOORD_0.
    """
    views: list[dict] = []
    blobs: list[bytes] = []
    cursor = 0

    def add_blob(data: bytes, target: int) -> int:
        nonlocal cursor
        padded = pad4(data)
        views.append({
            "buffer": 0,
            "byteOffset": cursor,
            "byteLength": len(data),
            "target": target,
        })
        blobs.append(padded)
        cursor += len(padded)
        return len(views) - 1

    materials: list[dict] = []
    primitives: list[dict] = []
    accessors: list[dict] = []
    total_vertices = 0
    total_tris = 0

    for mat_name in sorted(builders):
        mesh = builders[mat_name]
        if mesh.vertex_count == 0 or not mesh.indices:
            continue
        pos = mesh.positions.tobytes()
        nrm = mesh.normals.tobytes()
        col = mesh.colors.tobytes()
        uv = mesh.uvs.tobytes()
        lmap = mesh.lmap.tobytes()
        idx = mesh.indices.tobytes()

        v_pos = add_blob(pos, 34962)
        v_nrm = add_blob(nrm, 34962)
        v_col = add_blob(col, 34962)
        v_uv = add_blob(uv, 34962)
        v_lmap = add_blob(lmap, 34962)
        v_idx = add_blob(idx, 34963)

        base = len(accessors)
        accessors.extend([
            {
                "bufferView": v_pos, "componentType": 5126,
                "count": mesh.vertex_count, "type": "VEC3",
                "min": mesh.min, "max": mesh.max,
            },
            {"bufferView": v_nrm, "componentType": 5126,
             "count": mesh.vertex_count, "type": "VEC3"},
            {"bufferView": v_col, "componentType": 5126,
             "count": mesh.vertex_count, "type": "VEC3"},
            {"bufferView": v_uv, "componentType": 5126,
             "count": mesh.vertex_count, "type": "VEC2"},
            {"bufferView": v_lmap, "componentType": 5126,
             "count": mesh.vertex_count, "type": "VEC2"},
            {"bufferView": v_idx, "componentType": 5125,
             "count": len(mesh.indices), "type": "SCALAR"},
        ])

        materials.append({
            "name": mat_name,
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.92,
            },
            "extras": {"texdata": mat_name},
        })
        primitives.append({
            "attributes": {
                "POSITION": base,
                "NORMAL": base + 1,
                "COLOR_0": base + 2,
                "TEXCOORD_0": base + 3,
                "TEXCOORD_1": base + 4,
            },
            "indices": base + 5,
            "material": len(materials) - 1,
            "mode": 4,
        })
        total_vertices += mesh.vertex_count
        total_tris += len(mesh.indices) // 3

    bin_blob = pad4(b"".join(blobs))
    gltf = {
        "asset": {"version": "2.0", "generator": "tf2-demo bsp2glb.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }

    json_blob = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total = 12 + 8 + len(json_blob) + 8 + len(bin_blob)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        f.write(json_blob)
        f.write(struct.pack("<II", len(bin_blob), 0x004E4942))
        f.write(bin_blob)

    gmin = [min(m.min[i] for m in builders.values() if m.vertex_count) for i in range(3)]
    gmax = [max(m.max[i] for m in builders.values() if m.vertex_count) for i in range(3)]
    print(f"wrote {path}: {total_vertices} vertices, {total_tris} triangles, "
          f"{len(primitives)} materials, {total / (1024 * 1024):.2f} MiB", file=sys.stderr)
    print(f"bbox min={gmin} max={gmax}", file=sys.stderr)


def main(argv) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input.bsp> <output.glb>", file=sys.stderr)
        return 2
    bsp = Bsp(argv[1])
    print(f"{argv[1]}: VBSP v{bsp.version}", file=sys.stderr)
    builders, atlas_bytes = build(bsp)
    if not builders or not any(m.indices for m in builders.values()):
        print("error: no renderable geometry", file=sys.stderr)
        return 1
    write_glb(argv[2], builders)
    # Lightmap atlas sidecar: <glb-stem>_lm.png next to the GLB (the web
    # viewer loads it as material.lightMap — GLTF has no lightmap slot).
    atlas_path = os.path.splitext(argv[2])[0] + "_lm.png"
    with open(atlas_path, "wb") as f:
        f.write(write_png_rgb_bytes(LM_ATLAS, LM_ATLAS, atlas_bytes))
    print(f"wrote {atlas_path}: {LM_ATLAS}x{LM_ATLAS} lightmap atlas",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
