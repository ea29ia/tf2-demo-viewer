#!/usr/bin/env python3
"""Resolve BSP texdata materials and textures through the full lookup chain.

Resolution order (first hit wins):
  materials: BSP pak zip (lump 40) -> tf2_misc VPK -> loose tf dir
  textures:  BSP pak zip (lump 40) -> tf2_textures VPK -> loose tf dir

Usage:
    python3 tools/texres.py <map.bsp> --report
    python3 tools/texres.py <map.bsp> --dump <texdata-or-texture-name> <out.png>

`--dump` accepts a texdata name (e.g. `maps/pl_upward/grass_02`), which is
resolved through its VMT, or a raw texture path (with or without a
`materials/` prefix / `.vtf` extension).

Stdlib only. Missing packs print a warning and are skipped, never fatal.
"""
from __future__ import annotations

import argparse
import collections
import io
import os
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bsp2glb import Bsp, TEXINFO_STRUCT, LUMP_TEXINFO
import vmt as vmt_mod
import vtf as vtf_mod
import vpk as vpk_mod

LUMP_PAKFILE = 40

# VTF image format enum -> human name (values per the VTF header spec).
FORMAT_NAMES = {
    0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888",
    5: "I8", 8: "A8", 11: "ARGB8888", 12: "BGRA8888",
    13: "DXT1", 14: "DXT3", 15: "DXT5", 16: "BGRX8888",
}


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def vtf_header(data: bytes) -> tuple[int, int, int, int]:
    """(width, height, format id, mip count) from raw VTF bytes."""
    width, height = struct.unpack_from("<HH", data, 16)
    fmt = struct.unpack_from("<I", data, 52)[0]
    mips = data[56]
    return width, height, fmt, mips


def _lower(key: str) -> str:
    return key.lower() if isinstance(key, str) else key


class TexResolver:
    """Loads the BSP pak and the TF2 packs once, then resolves names."""

    def __init__(self, bsp_path: str, tf_dir: str):
        self.bsp = Bsp(bsp_path)
        self.tf_dir = tf_dir
        self.pak: dict[str, bytes] = self._load_pak()
        self.misc = self._open_pack("tf2_misc")
        self.texpack = self._open_pack("tf2_textures")
        self._loose_pack: object | None = None
        self._loose_tried = False

    # -- pack loading -------------------------------------------------------
    def _load_pak(self) -> dict[str, bytes]:
        raw = self.bsp.lump(LUMP_PAKFILE)
        if raw[:2] != b"PK":
            warn("BSP lump 40 is not a ZIP pak (no PK magic); pak lookups disabled")
            return {}
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as e:
            warn(f"pak lump is not a readable ZIP: {e}")
            return {}
        out: dict[str, bytes] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            out[info.filename.lower()] = zf.read(info)
        return out

    def _open_pack(self, name: str):
        try:
            return vpk_mod.open_pack(self.tf_dir, name)
        except (OSError, vpk_mod.VpkError) as e:
            warn(f"cannot open {name}: {e}; continuing without it")
            return None

    def _loose(self):
        """Loose tf dir tree, built lazily (only used when VPKs miss)."""
        if not self._loose_tried:
            self._loose_tried = True
            if os.path.isdir(self.tf_dir):
                try:
                    self._loose_pack = vpk_mod.LoosePack(self.tf_dir)
                except OSError as e:
                    warn(f"cannot index loose tree {self.tf_dir}: {e}")
            else:
                warn(f"tf dir not found: {self.tf_dir}")
        return self._loose_pack

    # -- reading ------------------------------------------------------------
    def _read_from(self, path: str, packs: list[tuple[str, object]]):
        """Return (bytes|None, source_label) trying each pack in order."""
        key = path.lower().replace("\\", "/")
        for label, pack in packs:
            if pack is None:
                continue
            try:
                if isinstance(pack, dict):
                    data = pack.get(key)
                    if data is not None:
                        return data, label
                    continue
                if pack.exists(key):
                    return pack.read(key), label
            except (OSError, vpk_mod.VpkError) as e:
                warn(f"read {key} from {label} failed: {e}")
        return None, ""

    @staticmethod
    def _clean_tex(name: str) -> str:
        """Texdata/texture/texture-name -> bare lowercase path token."""
        p = str(name).strip().strip('"').replace("\\", "/").lower()
        if p.startswith("materials/"):
            p = p[len("materials/"):]
        if p.endswith(".vtf"):
            p = p[:-4]
        return p.split()[0] if p else ""

    @staticmethod
    def _clean_mat(name: str) -> str:
        """Material name -> lowercase path WITHOUT stripping extensions.

        Texdata names are used verbatim by the engine (`materials/<name>.vmt`);
        some texdata names even embed a stray `.vmt` (water/water_well_beneath
        .vmt), which then legitimately fails to resolve.
        """
        p = str(name).strip().strip('"').replace("\\", "/").lower()
        if p.startswith("materials/"):
            p = p[len("materials/"):]
        return p.split()[0] if p else ""

    def _packs_for(self, kind: str) -> list[tuple[str, object]]:
        packs: list[tuple[str, object]] = [("pak", self.pak)]
        packs.append(("tf2_misc" if kind == "vmt" else "tf2_textures",
                      self.misc if kind == "vmt" else self.texpack))
        packs.append(("loose", self._loose()))
        return packs

    def read_vmt_path(self, path: str):
        """Read a full `materials/...vmt` path (used for patch includes)."""
        return self._read_from(path, self._packs_for("vmt"))

    def read_material(self, name: str):
        """Resolve a material name to (resolved_mat|None, source, error)."""
        base = self._clean_mat(name)
        if not base:
            return None, "", "empty material name"
        data, src = self._read_from("materials/" + base + ".vmt",
                                    self._packs_for("vmt"))
        if data is None:
            return None, "", "not found"
        reader = lambda path: self.read_vmt_path(path)[0]  # noqa: E731
        try:
            mat = vmt_mod.resolve(data.decode("latin-1"), reader)
        except vmt_mod.VmtError as e:
            return None, src, f"parse/resolve failed: {e}"
        # VMT keys are case-insensitive (map VMTs commonly write
        # `$baseTexture`); normalise to lowercase so vmt's lookups see them.
        mat = {_lower(k): v for k, v in mat.items()}
        return mat, src, ""

    def read_texture(self, tex_path: str):
        """Read a texture path (`dir/name`, no extension) as VTF bytes."""
        base = self._clean_tex(tex_path)
        if not base:
            return None, "", "empty texture path"
        return self._read_from("materials/" + base + ".vtf",
                               self._packs_for("vtf"))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def collect_used_texdata(bsp: Bsp) -> set[int]:
    """Texdata indices referenced by at least one face (faces -> texinfo)."""
    texinfo_raw = bsp.lump(LUMP_TEXINFO)
    n_texinfo = len(texinfo_raw) // TEXINFO_STRUCT.size
    texdata_of: list[int] = []
    for i in range(n_texinfo):
        texdata_of.append(TEXINFO_STRUCT.unpack_from(
            texinfo_raw, i * TEXINFO_STRUCT.size)[-1])
    used: set[int] = set()
    for face in bsp.faces():
        ti = face[5]
        if 0 <= ti < n_texinfo:
            td = texdata_of[ti]
            if td >= 0:
                used.add(td)
    return used


def build_report(res: TexResolver) -> dict:
    bsp = res.bsp
    names = bsp.texture_names()
    texdata = bsp.texdata_entries()
    used = collect_used_texdata(bsp)

    per_name: dict[str, dict] = {}
    for idx, (name_index, width, height) in enumerate(texdata):
        name = names[name_index] if 0 <= name_index < len(names) else ""
        entry = per_name.setdefault(name, {
            "name": name, "width": width, "height": height,
            "indices": [], "used": False,
        })
        entry["indices"].append(idx)
        if idx in used:
            entry["used"] = True

    shaders: collections.Counter = collections.Counter()  # lower name -> count
    shader_spelling: dict[str, str] = {}  # lower name -> first spelling seen
    vmt_sources: collections.Counter = collections.Counter()
    missing_vmts: list[str] = []
    broken_vmts: list[str] = []
    tex_paths: list[str] = []  # unique, in first-seen order
    tex_seen: set[str] = set()
    tex_of_name: dict[str, list[str]] = {}

    for name, entry in per_name.items():
        mat, src, err = res.read_material(name)
        entry["vmt_source"] = src
        if mat is None:
            if err == "not found":
                missing_vmts.append(name)
                entry["status"] = "no VMT"
            else:
                broken_vmts.append(f"{name}: {err}")
                entry["status"] = "broken VMT"
            continue
        entry["status"] = "ok"
        shader = vmt_mod.shader_name_of(mat) or "<none>"
        key = shader.lower()
        shader_spelling.setdefault(key, shader)
        shaders[key] += 1
        vmt_sources[src] += 1
        paths = vmt_mod.get_texture_paths(mat)
        tex_of_name[name] = paths
        for p in paths:
            if p not in tex_seen:
                tex_seen.add(p)
                tex_paths.append(p)

    # VTF header stats over unique texture paths (no full decode here).
    dims: collections.Counter = collections.Counter()
    fmts: collections.Counter = collections.Counter()
    vtf_sources: collections.Counter = collections.Counter()
    missing_vtfs: list[str] = []
    bad_vtfs: list[str] = []
    total_bytes = 0
    resolved_count = 0
    for p in tex_paths:
        data, src = res.read_texture(p)
        if data is None:
            missing_vtfs.append(p)
            continue
        try:
            w, h, fmt, _mips = vtf_header(data)
        except (struct.error, IndexError) as e:
            bad_vtfs.append(f"{p}: bad header ({e})")
            continue
        resolved_count += 1
        total_bytes += len(data)
        dims[f"{w}x{h}"] += 1
        fmts[FORMAT_NAMES.get(fmt, f"fmt{fmt}")] += 1
        vtf_sources[src] += 1

    return {
        "texdata_total": len(texdata),
        "texdata_names_total": len(per_name),
        "texdata_names_used": sum(1 for e in per_name.values() if e["used"]),
        "shaders": shaders,
        "shader_spelling": shader_spelling,
        "vmt_sources": vmt_sources,
        "vmt_resolved": sum(1 for e in per_name.values() if e["status"] == "ok"),
        "missing_vmts": missing_vmts,
        "broken_vmts": broken_vmts,
        "tex_paths": tex_paths,
        "tex_of_name": tex_of_name,
        "vtf_resolved": resolved_count,
        "missing_vtfs": missing_vtfs,
        "bad_vtfs": bad_vtfs,
        "dims": dims,
        "fmts": fmts,
        "vtf_sources": vtf_sources,
        "total_bytes": total_bytes,
        "per_name": per_name,
    }


def print_report(rep: dict) -> None:
    p = print
    p(f"texdata entries: {rep['texdata_total']} "
      f"({rep['texdata_names_total']} unique names, "
      f"{rep['texdata_names_used']} used by faces)")
    p("")
    p("shader histogram (resolved VMTs):")
    for key, n in rep["shaders"].most_common():
        p(f"  {rep['shader_spelling'].get(key, key):<28} {n}")
    p("")
    p(f"VMTs resolved: {rep['vmt_resolved']}, missing: {len(rep['missing_vmts'])}, "
      f"broken: {len(rep['broken_vmts'])}")
    if rep["vmt_sources"]:
        p("  VMT sources: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["vmt_sources"].items())))
    if rep["missing_vmts"]:
        p("  missing VMTs (texdata names with no VMT anywhere):")
        for name in rep["missing_vmts"]:
            p(f"    {name}")
    for line in rep["broken_vmts"]:
        p(f"  broken VMT: {line}")
    p("")
    p(f"unique texture paths: {len(rep['tex_paths'])}")
    p(f"VTFs resolved: {rep['vtf_resolved']}, missing: {len(rep['missing_vtfs'])}, "
      f"bad header: {len(rep['bad_vtfs'])}")
    if rep["vtf_sources"]:
        p("  VTF sources: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["vtf_sources"].items())))
    if rep["missing_vtfs"]:
        p("  missing VTFs:")
        for path in rep["missing_vtfs"]:
            p(f"    {path}")
    for line in rep["bad_vtfs"]:
        p(f"  bad VTF: {line}")
    p("")
    p("dimension histogram (resolved VTFs):")
    for dim, n in sorted(rep["dims"].items(), key=lambda kv: (-kv[1], kv[0])):
        p(f"  {dim:<12} {n}")
    p("")
    p("image-format histogram (resolved VTFs):")
    for fmt, n in rep["fmts"].most_common():
        p(f"  {fmt:<12} {n}")
    p("")
    p(f"total raw VTF bytes: {rep['total_bytes']} "
      f"({rep['total_bytes'] / (1024 * 1024):.1f} MiB)")


# ---------------------------------------------------------------------------
# Dump
# ---------------------------------------------------------------------------
def cmd_dump(res: TexResolver, name: str, out_path: str) -> int:
    # 1) Try it as a texdata name first (case-insensitive).
    tex_paths: list[str] = []
    names = res.bsp.texture_names()
    lower_names = {n.lower(): n for n in names}
    base = res._clean_tex(name)
    if base in lower_names:
        mat, src, err = res.read_material(base)
        if mat is None and base.endswith(".vmt"):
            # Texdata names with an embedded `.vmt` (e.g.
            # water/water_well_beneath.vmt) cannot resolve strictly; retry
            # without the suffix so the underlying texture can still be dumped.
            mat, src, err = res.read_material(base[:-4])
        if mat is None:
            print(f"error: texdata '{name}' found but its VMT is unavailable "
                  f"({err or 'not found'})", file=sys.stderr)
            return 1
        tex_paths = vmt_mod.get_texture_paths(mat)
        print(f"texdata {lower_names[base]}: VMT source={src or 'none'}, "
              f"shader={vmt_mod.shader_name_of(mat)}, "
              f"textures={tex_paths}")
        if not tex_paths:
            print("error: VMT has no $basetexture-like texture keys", file=sys.stderr)
            return 1
    else:
        tex_paths = [res._clean_tex(name)]

    # 2) Fetch the first texture (for WorldVertexTransition materials the
    #    remaining $basetexture2 etc. keys are listed but not dumped).
    sel = tex_paths[0]
    if len(tex_paths) > 1:
        print(f"note: {len(tex_paths)} texture keys in material; dumping '{sel}'")
    data, src = res.read_texture(sel)
    if data is None:
        print(f"error: VTF not found for '{sel}'", file=sys.stderr)
        return 1
    w, h, fmt, _mips = vtf_header(data)
    print(f"VTF {sel}: source={src}, {w}x{h}, "
          f"format={FORMAT_NAMES.get(fmt, fmt)}, {len(data)} bytes")
    try:
        w2, h2, rgba = vtf_mod.decode_vtf(data)
    except vtf_mod.VtfError as e:
        print(f"error: decode failed: {e}", file=sys.stderr)
        return 1
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    vtf_mod.write_png(out_path, w2, h2, rgba)
    print(f"wrote {out_path} ({w2}x{h2})")
    return 0


def main(argv: list[str]) -> int:
    default_tf = os.path.expanduser(
        "~/.steam/debian-installation/steamapps/common/Team Fortress 2/tf")
    ap = argparse.ArgumentParser(description="BSP texdata material/texture resolver")
    ap.add_argument("bsp", help="path to the .bsp file")
    ap.add_argument("--tf", default=default_tf, help="TF2 tf directory")
    ap.add_argument("--report", action="store_true", help="print resolution report")
    ap.add_argument("--dump", nargs=2, metavar=("NAME", "OUT.png"),
                    help="dump one texture (texdata or texture name) as PNG")
    args = ap.parse_args(argv)

    res = TexResolver(args.bsp, args.tf)
    if args.report:
        print_report(build_report(res))
    if args.dump:
        return cmd_dump(res, args.dump[0], args.dump[1])
    if not args.report and not args.dump:
        ap.print_usage()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
