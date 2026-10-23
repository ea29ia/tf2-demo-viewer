#!/usr/bin/env python3
"""Enumerate and export TF2 static prop meshes (BSP sprp lump -> GLB).

Two subcommands:

    python3 tools/props2glb.py <map.bsp> list [--top 20]
    python3 tools/props2glb.py <map.bsp> export --tf <tf_dir> --out <dir>
                                     [--top 5] [--model models/props_2fort/oildrum]
                                     [--match REGEX[,REGEX...]]
                                     [--exclude REGEX[,REGEX...]]

`list` parses the sprp game lump through tools/probe_props.py (the same path
tools/add_props_glb.py uses), counts prop instances per model and prints the
most frequent ones.

`--match` filters the candidate models by a case-insensitive regular expression
matched against the full model id (e.g. `models/props_coalmines/boulder_pile2`).
It can be repeated or comma-separated and combines with `--top` (limit the
matched set by instance count) and `--model` (filter the explicit list). When
`--match` is given without `--top` and `--model`, every matching model of the
map is selected.

`--exclude` removes models from the matched set. It takes the same
repeatable/comma-separated, case-insensitive regular expressions and is applied
after `--match` (e.g. `--exclude skybox`). Anchor a pattern with a leading
slash to match the basename only: `--match '/(train|wood_)'` does not match
`models/props_trainyard/handrail28`, because the slash must directly precede
the matched prefix.

`export` runs the mdl2glb.py geometry pipeline (MDL v48 + VVD + dx90 VTX) in
prop mode: skin family 0 only, one GLB per model, no red/blue team split,
textures embedded as PNG. The GLB keeps the mdl2glb axis mapping
(x, y, z) -> (z, y, -x), i.e. glTF Y-up, model forward = local +x. Prop mode
also keeps the VTF alpha channel and emits glTF `alphaMode` (`BLEND`, `MASK`
with `alphaCutoff`, or `OPAQUE`) from the VMT alpha flags, so VMT-driven
transparency (translucent glass, fence cutouts) is preserved.

`export` also merges `<out>/manifest.json`:
    {"version": 1, "models": {"<model id>": {"file": "<basename>.glb", "tris": N}}}
Old entries are kept, and only successfully exported models are recorded.

Prop materials differ from player materials in two ways that the player
texture resolver does not handle:
  * texture names inside prop MDLs are bare (`oildrum001`, `handrail128`),
    not `models/<dir>/<name>`;
  * the same bare name exists under several prop material directories
    (`shrub_03` exists under props_foliage / props_forest / props_swamp), so
    picking the first VPK hit gives the wrong skin.

PropTextures below resolves a bare name to the materials directory that
shares the longest path prefix with the model's own directory, and falls back
to a 1x1 grey placeholder (reported as a failed texture) instead of dropping
the primitive, so geometry never disappears because of a missing VMT.
"""
from __future__ import annotations

import collections
import contextlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, TOOLS_DIR)

import mdl2glb  # noqa: E402
import vtf  # noqa: E402

PLACEHOLDER_RGBA = (128, 128, 128, 255)  # 1x1 grey, used when a VTF is missing
# A prop larger than this (Source units) is almost certainly a misparse:
# pl_upward fits in ~8000u, the largest legitimate props are a few hundred.
ABSURD_EXTENT = 4096.0


def load_probe():
    """Import tools/probe_props.py.

    probe_props.py loads bsp2glb.py through the relative path
    "tools/bsp2glb.py", so the import has to happen with the repo root as the
    working directory (that is also how add_props_glb.py does it).
    """
    prev = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        spec = importlib.util.spec_from_file_location(
            "probe_props", os.path.join(TOOLS_DIR, "probe_props.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.chdir(prev)
    return module


# ---------------------------------------------------------------------------
# sprp lump enumeration
# ---------------------------------------------------------------------------
def enumerate_props(bsp_path: str):
    """Return (model_names_in_table, instance_counts, prop_count)."""
    probe = load_probe()
    # probe_props prints lump diagnostics; keep our report clean.
    with contextlib.redirect_stdout(io.StringIO()):
        bsp = probe.m.Bsp(bsp_path)
        data, _version = probe.decompress_sprp(bsp)
        names, _leaf_count, prop_count, entry = probe.parse(data)
    counts: collections.Counter[str] = collections.Counter()
    for i in range(prop_count):
        base = entry + i * probe.PROP_STRIDE
        prop_type = struct.unpack_from("<H", data, base + 24)[0]
        counts[names[prop_type] if prop_type < len(names) else "?"] += 1
    return names, counts, prop_count


def model_id(model_path: str) -> str:
    """Normalise a sprp model name to a model path without extension."""
    name = model_path.replace("\\", "/").strip()
    if name.lower().endswith(".mdl"):
        name = name[:-4]
    return name


def sanitised_name(model_path: str) -> str:
    """models/props_foliage/shrub_03a.mdl -> props_foliage_shrub_03a.glb"""
    name = model_id(model_path)
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name.replace("/", "_") + ".glb"


# ---------------------------------------------------------------------------
# Prop-specific material/texture resolution
# ---------------------------------------------------------------------------
class PropTextures(mdl2glb.Textures):
    """mdl2glb.Textures with VPK-wide, directory-aware prop resolution."""

    def __init__(self, tf_dir: str):
        super().__init__(tf_dir)
        self.model_dir = ""
        self.failed: list[dict] = []
        self._vmt_index: dict[str, list[str]] | None = None
        self._vtf_index: dict[str, list[str]] | None = None

    def vmt_basetexture(self, material_path: str) -> str | None:
        """Case-insensitive $basetexture lookup (prop-VMT override).

        mdl2glb.Textures.vmt_basetexture matches only lowercase
        `$basetexture`, but ~40% of TF2 VMTs spell it `$baseTexture`
        (e.g. props_coalmine/boulder_pile1.vmt) and silently fall through
        to the mdl texture-name fallback. Values may also mix separators
        (`models\\props_coalmine/boulder_pile1`), so normalise to '/'.
        """
        try:
            text = self.misc.read(material_path).decode("utf-8",
                                                        errors="replace")
        except Exception:
            return None
        match = re.search(r"\$basetexture\"?\s*\"?([^\"\s]+)", text,
                          re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).replace("\\", "/")
        # Some prop VMTs spell the value with the `.vtf` extension
        # (props_coalmine/chicken_wire.vmt); the VPK lookup wants it without.
        if value.lower().endswith(".vtf"):
            value = value[:-4]
        return value

    def _ensure_index(self) -> None:
        if self._vmt_index is not None:
            return
        self._vmt_index = {}
        for key in self.misc.entries:
            if key.startswith("materials/") and key.endswith(".vmt"):
                base = key.rsplit("/", 1)[-1][:-4]
                self._vmt_index.setdefault(base, []).append(key)
        self._vtf_index = {}
        for key in self.tex.entries:
            if key.startswith("materials/") and key.endswith(".vtf"):
                base = key.rsplit("/", 1)[-1][:-4]
                self._vtf_index.setdefault(base, []).append(key)

    def _score(self, path: str, model_dir: str | None) -> tuple[int, int]:
        """Rank a materials path by shared leading components with the model.

        materials/models/props_foliage/shrub_03.vmt for
        models/props_foliage/shrub_03a -> 2 shared components (best);
        materials/models/props_swamp/shrub_03.vmt -> 1. Ties prefer the
        shallower path.
        """
        if not model_dir:
            return (0, -path.count("/"))
        directory = path.rsplit("/", 1)[0]
        if directory.startswith("materials/"):
            directory = directory[len("materials/"):]
        mod_dir = model_dir.rsplit("/", 1)[0] if "/" in model_dir else ""
        a = directory.split("/")
        b = mod_dir.split("/")
        shared = 0
        for x, y in zip(a, b):
            if x != y:
                break
            shared += 1
        return (shared, -len(a))

    def _best(self, hits: list[str]) -> str:
        return max(hits, key=lambda p: (self._score(p, self.model_dir), -len(p)))

    def resolve_material(self, name: str,
                         model_dir: str | None = None) -> str | None:
        lookup_dir = model_dir or self.model_dir
        low = name.lower()
        if low.startswith("models/"):
            path = "materials/" + low + ".vmt"
            if self.misc.exists(path):
                return path
        for cand in ("materials/" + low + ".vmt",
                     "materials/models/" + low + ".vmt"):
            if self.misc.exists(cand):
                return cand
        self._ensure_index()
        hits = self._vmt_index.get(low.rsplit("/", 1)[-1])
        if not hits:
            # The VMT may be named after a sub-path only, e.g.
            # models/props_coalmine/boulder_pile1 -> boulder_pile1.
            return None
        return max(hits, key=lambda p: (self._score(p, lookup_dir), -len(p)))

    def load(self, texture_name: str):
        # A handful of prop VMTs spell $basetexture with the extension
        # (`models/props_coalmine/boulder5.vtf`), unlike the usual
        # extensionless value. Normalise it away before probing the VPK,
        # otherwise the resolver would look for `boulder5.vtf.vtf`.
        texture_name = texture_name.replace("\\", "/")
        if texture_name.lower().endswith(".vtf"):
            texture_name = texture_name[:-4]
        if texture_name in self.cache:
            return self.cache[texture_name]
        low = texture_name.lower()
        path = None
        for cand in ("materials/" + low + ".vtf",
                     "materials/models/" + low + ".vtf"):
            if self.tex.exists(cand):
                path = cand
                break
        if path is None:
            self._ensure_index()
            hits = (self._vtf_index.get(low.rsplit("/", 1)[-1]) or
                    self._vtf_index.get(low))
            if hits:
                path = self._best(hits)
        if path is None:
            # Some VTFs only ship as dxlevel80 variants.
            suffix = "/dxlevel80/" + low.rsplit("/", 1)[-1] + ".vtf"
            hits = [k for k in self.tex.entries if k.endswith(suffix)]
            if hits:
                path = self._best(hits)
        if path is None:
            self._fail(texture_name, "vtf-missing")
            return self._placeholder(texture_name)

        try:
            data = self.tex.read(path)
        except Exception as exc:  # noqa: BLE001 - report, do not abort
            self._fail(texture_name, f"vtf-read-error: {exc}")
            return self._placeholder(texture_name)
        try:
            out = vtf.decode_vtf(data)
        except Exception as exc:  # noqa: BLE001 - report, do not abort
            self._fail(texture_name, f"vtf-decode-error: {exc}")
            return self._placeholder(texture_name)

        # Same sRGB handling as mdl2glb.Textures.load (flag 0x40 = linear
        # source data; PNG is interpreted as sRGB by the viewer).
        flags = struct.unpack_from("<I", data, 20)[0]
        w, h, rgba = out
        if flags & 0x40:
            rgba = bytes(
                b if (i & 3) == 3 else round((b / 255.0) ** (1.0 / 2.2) * 255)
                for i, b in enumerate(rgba))
            out = (w, h, rgba)
        self.cache[texture_name] = out
        return out

    def _fail(self, name: str, reason: str) -> None:
        self.failed.append({"texture": name, "reason": reason})

    def _placeholder(self, texture_name: str):
        r, g, b, a = PLACEHOLDER_RGBA
        out = (1, 1, bytes((r, g, b, a)))
        self.cache[texture_name] = out
        return out


# ---------------------------------------------------------------------------
# Export one prop model
# ---------------------------------------------------------------------------
def read_model_parts(tf_dir: str, model: str, misc) -> tuple:
    mdl_data = misc.read(f"{model}.mdl")
    vvd_data = misc.read(f"{model}.vvd")
    try:
        vtx_data = misc.read(f"{model}.dx90.vtx")
    except Exception:
        vtx_data = misc.read(f"{model}.dx80.vtx")
    return mdl_data, vvd_data, vtx_data


def export_model(tf_dir: str, model: str, out_path: str,
                 textures: PropTextures, misc) -> dict:
    """Convert one prop model to a single GLB. Returns a stats dict."""
    started = time.time()
    stats: dict = {
        "model": model,
        "out": out_path,
        "ok": False,
        "error": None,
        "verts": 0,
        "tris": 0,
        "bytes": 0,
        "materials": 0,
        "images": 0,
        "primitives": 0,
        "bbox_min": None,
        "bbox_max": None,
        "failed_textures": [],
        "seconds": 0.0,
    }
    textures.model_dir = model
    textures.failed = []
    try:
        mdl_data, vvd_data, vtx_data = read_model_parts(tf_dir, model, misc)
        mdl = mdl2glb.Mdl(mdl_data)
        vvd = mdl2glb.Vvd(vvd_data, root_lod=0)
        vtx = mdl2glb.Vtx(vtx_data)
        if vtx.checksum != mdl.checksum:
            print(f"  warn: {model}: vtx checksum {vtx.checksum} != "
                  f"mdl {mdl.checksum}", file=sys.stderr)
        vtx_lod = vtx.root_lod(0)
        if len(vtx_lod) != len(mdl.bodyparts):
            print(f"  warn: {model}: {len(vtx_lod)} vtx bodyparts vs "
                  f"{len(mdl.bodyparts)} mdl bodyparts", file=sys.stderr)
        for b, bodypart in enumerate(mdl.bodyparts):
            if b < len(vtx_lod) and len(vtx_lod[b]) != len(bodypart["models"]):
                print(f"  warn: {model}: bodypart {b} model count mismatch "
                      f"({len(bodypart['models'])} mdl vs {len(vtx_lod[b])} vtx)",
                      file=sys.stderr)
        # Prop mode: skin family 0; identity when the model has no skin table.
        skin = mdl.skins[0] if mdl.skins else []
        gltf, blob = mdl2glb.build_glb(
            mdl, vvd, vtx_lod, textures, skin, model, model_dir=model,
            keep_alpha=True)
        mdl2glb.write_glb(out_path, gltf, blob)

        accessors = gltf["accessors"]
        prims = gltf["meshes"][0]["primitives"]
        stats["verts"] = accessors[0]["count"]
        stats["tris"] = sum(
            accessors[p["indices"]]["count"] for p in prims) // 3
        stats["primitives"] = len(prims)
        stats["materials"] = len(gltf.get("materials", []))
        stats["images"] = len(gltf.get("images", []))
        stats["bbox_min"] = [round(v, 3) for v in accessors[0]["min"]]
        stats["bbox_max"] = [round(v, 3) for v in accessors[0]["max"]]
        stats["bytes"] = os.path.getsize(out_path)
        stats["failed_textures"] = list(textures.failed)
        stats["ok"] = True
    except Exception as exc:  # noqa: BLE001 - one bad model must not stop batch
        stats["error"] = f"{type(exc).__name__}: {exc}"
        stats["failed_textures"] = list(textures.failed)
    stats["seconds"] = time.time() - started
    return stats


def flags_for(stats: dict) -> list[str]:
    """Human-readable problems with one export."""
    flags = []
    if not stats["ok"]:
        flags.append("EXPORT FAILED")
        return flags
    if stats["verts"] <= 0:
        flags.append("zero vertices")
    if stats["tris"] <= 0:
        flags.append("zero triangles")
    if stats["bbox_min"] is None:
        flags.append("no bbox")
    else:
        ext = [stats["bbox_max"][k] - stats["bbox_min"][k] for k in range(3)]
        if max(ext) > ABSURD_EXTENT:
            flags.append(f"absurd bbox extent {max(ext):.0f}u")
        if min(ext) < 0.01:
            flags.append("degenerate bbox (flat axis)")
    if stats["failed_textures"]:
        flags.append(f"{len(stats['failed_textures'])} unresolved texture(s)")
    return flags


def print_stats(stats: dict) -> None:
    flags = flags_for(stats)
    print(f"  model        {stats['model']}")
    print(f"  out          {stats['out']}")
    if not stats["ok"]:
        print(f"  result       FAILED: {stats['error']}")
    else:
        print(f"  verts        {stats['verts']}")
        print(f"  tris         {stats['tris']}")
        print(f"  primitives   {stats['primitives']}")
        print(f"  materials    {stats['materials']} "
              f"({stats['images']} embedded texture(s))")
        print(f"  file size    {stats['bytes']} bytes "
              f"({stats['bytes'] / 1024.0:.1f} KiB)")
        lo, hi = stats["bbox_min"], stats["bbox_max"]
        ext = [hi[k] - lo[k] for k in range(3)]
        print(f"  bbox (glTF)  min {lo}  max {hi}")
        print(f"  bbox extent  {[round(v, 2) for v in ext]} (u, Y-up space)")
    if stats["failed_textures"]:
        for item in stats["failed_textures"]:
            print(f"  texture FAIL {item['texture']}  ({item['reason']})")
    print(f"  time         {stats['seconds'] * 1000.0:.0f} ms")
    print(f"  flags        {', '.join(flags) if flags else 'none'}")


def print_summary(rows: list[dict], instances: dict[str, int]) -> None:
    print()
    print(f"  {'model':52} {'inst':>4} {'verts':>7} {'tris':>7} "
          f"{'KiB':>8} {'mat':>3} {'fail':>4}  flags")
    for stats in rows:
        short = stats["model"].replace("models/", "", 1)
        inst = instances.get(stats["model"], 0)
        kib = stats["bytes"] / 1024.0
        flags = flags_for(stats)
        print(f"  {short:52} {inst:4d} {stats['verts']:7d} {stats['tris']:7d} "
              f"{kib:8.1f} {stats['materials']:3d} "
              f"{len(stats['failed_textures']):4d}  "
              f"{', '.join(flags) if flags else 'ok'}")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


def load_manifest(out_dir: str) -> dict:
    """Read <out_dir>/manifest.json, tolerating a missing or broken file."""
    path = os.path.join(out_dir, MANIFEST_NAME)
    models: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("models"), dict):
                models = {str(k): v for k, v in data["models"].items()
                          if isinstance(v, dict)}
            else:
                print(f"  warn: {path}: unexpected schema, starting over",
                      file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - never fail the export for this
            print(f"  warn: {path}: {exc}, starting over", file=sys.stderr)
    return {"version": MANIFEST_VERSION, "models": models}


def write_manifest(out_dir: str, rows: list[dict]) -> str:
    """Merge successful exports into <out_dir>/manifest.json (sorted, indent 2).

    Keys are full model ids without `.mdl`; `file` is the basename only.
    Failed exports are skipped and never overwrite an existing good entry.
    """
    manifest = load_manifest(out_dir)
    models: dict = manifest["models"]
    added = 0
    for stats in rows:
        if not stats["ok"]:
            continue
        models[stats["model"]] = {
            "file": os.path.basename(stats["out"]),
            "tris": int(stats["tris"]),
        }
        added += 1
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"manifest: {path} ({added} written this run, "
          f"{len(models)} total entries)")
    return path


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def compile_matches(patterns: list[str]) -> list[re.Pattern]:
    """Compile --match values (already split, case-insensitive)."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def matches_any(patterns: list[re.Pattern], model: str) -> bool:
    return any(p.search(model) for p in patterns)


def select_targets(names: list[str], counts: collections.Counter, top: int | None,
                   explicit: list[str],
                   patterns: list[re.Pattern],
                   excludes: list[re.Pattern] | None = None) -> list[str]:
    """Pick the models to export.

    * `--model` wins as the candidate list (then `--match` filters it).
    * otherwise the whole sprp name table is the candidate list when
      `--match` is given, so models are matched by id, not by rank;
    * otherwise the `top` most frequent placed models are used.
    A `--top` combined with `--match` keeps the N most frequent matches.
    `--exclude` is applied last, after `--match` has selected the candidates.
    """
    if explicit:
        targets = [model_id(m) for m in explicit]
    elif patterns:
        targets = [model_id(n) for n in names]
    else:
        targets = [model_id(n) for n, _ in counts.most_common(top or 20)]

    seen = set()
    targets = [t for t in targets if not (t in seen or seen.add(t))]

    if patterns:
        targets = [t for t in targets if matches_any(patterns, t)]

    if excludes:
        targets = [t for t in targets if not matches_any(excludes, t)]

    if patterns and top is not None and not explicit:
        # Instance count first, id as the deterministic tie-breaker.
        targets.sort(key=lambda t: (-counts.get(t, 0), t))
        targets = targets[:top]

    return targets


def cmd_list(bsp_path: str, top: int) -> int:
    names, counts, prop_count = enumerate_props(bsp_path)
    print(f"BSP: {bsp_path}")
    print(f"sprp model name table: {len(names)} entries")
    print(f"prop instances: {prop_count}")
    print(f"models actually placed: {len(counts)}")
    print(f"total instances of the placed models: {sum(counts.values())}")
    print(f"top {top} models by instance count:")
    for name, n in counts.most_common(top):
        print(f"  {n:5d}  {model_id(name)}")
    return 0


def cmd_export(bsp_path: str, tf_dir: str, out_dir: str, top: int | None,
               explicit: list[str], patterns: list[str],
               exclude_patterns: list[str]) -> int:
    names, counts, prop_count = enumerate_props(bsp_path)
    print(f"BSP: {bsp_path}")
    print(f"prop instances: {prop_count}, placed models: {len(counts)}")

    compiled = compile_matches(patterns)
    excludes = compile_matches(exclude_patterns)
    targets = select_targets(names, counts, top, explicit, compiled, excludes)

    print(f"exporting {len(targets)} model(s) to {out_dir}")
    misc = mdl2glb.vpk.open_pack(tf_dir, "tf2_misc")
    textures = PropTextures(tf_dir)

    rows = []
    for model in targets:
        out_path = os.path.join(out_dir, sanitised_name(model))
        stats = export_model(tf_dir, model, out_path, textures, misc)
        print()
        print_stats(stats)
        rows.append(stats)

    instance_counts = {model_id(n): c for n, c in counts.items()}
    print_summary(rows, instance_counts)

    ok = sum(1 for s in rows if s["ok"])
    failed = len(rows) - ok
    bad_textures = sum(len(s["failed_textures"]) for s in rows)
    total_bytes = sum(s["bytes"] for s in rows)
    total_time = sum(s["seconds"] for s in rows)
    print()
    print(f"exported {ok}/{len(rows)} models, {failed} failed, "
          f"{bad_textures} unresolved textures")
    print(f"total {total_bytes} bytes ({total_bytes / 1024.0:.1f} KiB), "
          f"{total_time:.2f} s wall "
          f"({total_time / max(len(rows), 1) * 1000.0:.0f} ms/model)")
    if rows:
        write_manifest(out_dir, rows)
    return 0 if failed == 0 else 1


USAGE = """usage:
  props2glb.py <map.bsp> list [--top N]
  props2glb.py <map.bsp> export --tf <tf_dir> --out <dir>
                               [--top N] [--model M ...] [--match REGEX[,REGEX...]]
                               [--exclude REGEX[,REGEX...]]"""


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(USAGE, file=sys.stderr)
        return 2
    bsp_path, command = argv[1], argv[2]
    rest = argv[3:]
    top = 20
    top_given = False
    tf_dir = None
    out_dir = None
    explicit: list[str] = []
    match_values: list[str] = []
    exclude_values: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--top" and i + 1 < len(rest):
            top = int(rest[i + 1])
            top_given = True
            i += 2
        elif arg == "--tf" and i + 1 < len(rest):
            tf_dir = rest[i + 1]
            i += 2
        elif arg == "--out" and i + 1 < len(rest):
            out_dir = rest[i + 1]
            i += 2
        elif arg == "--model" and i + 1 < len(rest):
            explicit.append(rest[i + 1])
            i += 2
        elif arg == "--match" and i + 1 < len(rest):
            # Repeatable and comma-separated; empty fragments are ignored.
            match_values.extend(
                part for part in rest[i + 1].split(",") if part.strip())
            i += 2
        elif arg == "--exclude" and i + 1 < len(rest):
            exclude_values.extend(
                part for part in rest[i + 1].split(",") if part.strip())
            i += 2
        else:
            print(f"unknown argument {arg!r}\n{USAGE}", file=sys.stderr)
            return 2

    if command == "list":
        return cmd_list(bsp_path, top)
    if command == "export":
        if not tf_dir or not out_dir:
            print(f"export needs --tf and --out\n{USAGE}", file=sys.stderr)
            return 2
        if match_values:
            try:
                re.compile("|".join(f"(?:{p})" for p in match_values),
                           re.IGNORECASE)
            except re.error as exc:
                print(f"bad --match regex: {exc}\n{USAGE}", file=sys.stderr)
                return 2
        if exclude_values:
            try:
                re.compile("|".join(f"(?:{p})" for p in exclude_values),
                           re.IGNORECASE)
            except re.error as exc:
                print(f"bad --exclude regex: {exc}\n{USAGE}", file=sys.stderr)
                return 2
        return cmd_export(bsp_path, tf_dir, out_dir,
                          top if top_given else None, explicit, match_values,
                          exclude_values)
    print(f"unknown command {command!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
