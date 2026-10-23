#!/usr/bin/env python3
"""Valve Material (VMT) reader: KeyValues parser + `patch` resolver.

Stdlib only. Supports the subset of KeyValues used by VMT files:

    "ShaderName"
    {
        "$basetexture"  "path/without/extension"
        "Proxies"
        {
            "AnimatedTexture" { "rate" "1" }
        }
    }

    "patch"
    {
        "include" "materials/OTHER/MATERIAL.vmt"
        "replace" { "$envmap" "maps/map/c0_0_0" }
        "insert"  { "$key"    "value" }
    }

Both quoted and bare (unquoted) keys/values are accepted (TF2 VMTs contain
a few bare keys such as `$underwateroverlay`), `//` line comments and `/* */`
block comments are skipped, and duplicate keys resolve last-wins.

`resolve()` returns a flat dict of the effective material keys plus two
metadata entries prefixed with `_` (VMT keys never start with `_`):

    "_shader"      shader name as written in the VMT ("LightmappedGeneric",
                   "WorldVertexTransition", "UnlitGeneric", "Water", ...)
    "_patch_chain" tuple of included VMT paths, outermost first (empty for
                   a non-patch material)

Usage:
    python3 tools/vmt.py <file.vmt>          # parse + print (no patch include)
"""
from __future__ import annotations

import copy
import json
import math
import sys

SHADER_KEY = "_shader"
CHAIN_KEY = "_patch_chain"

# Material keys that name a texture. $basetexture2/3 are used by
# WorldVertexTransition (blend between two materials), $blendmodulatetexture
# by the blend modulation stage.
TEXTURE_KEYS = ("$basetexture", "$basetexture2", "$basetexture3", "$blendmodulatetexture")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


class VmtError(Exception):
    pass


# ---------------------------------------------------------------------------
# Tokenizer / parser
# ---------------------------------------------------------------------------
def _tokenize(text: str):
    """Yield KeyValues tokens: `{`, `}`, and strings (quotes stripped)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise VmtError("unterminated /* comment")
            i = j + 2
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n and text[j + 1] in '"\\':
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if j >= n:
                raise VmtError("unterminated quoted string")
            yield "".join(buf)
            i = j + 1
        else:
            # Bare token: ends at whitespace, brace, quote or comment start.
            j = i
            while j < n and text[j] not in ' \t\r\n"{}':
                if text.startswith("//", j):
                    break
                j += 1
            yield text[i:j]
            i = j


def parse(text: str) -> dict:
    """Parse KeyValues text into nested dicts (values are str or dict)."""
    tokens = list(_tokenize(text))
    pos = 0

    def block(top: bool) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                if top:
                    raise VmtError("unbalanced '}' at top level")
                pos += 1
                return out
            if tok == "{":
                raise VmtError(f"unexpected '{{' at token {pos}")
            key = tok
            pos += 1
            if pos >= len(tokens):
                # Dangling key at end of file: KeyValues tolerates it.
                out[key.lower()] = ""
                break
            val = tokens[pos]
            if val == "{":
                pos += 1
                out[key.lower()] = block(False)
            elif val == "}":
                # `"key" }` -> empty value, leave the brace for the caller.
                out[key.lower()] = ""
            else:
                out[key.lower()] = val
                pos += 1
        if not top:
            raise VmtError("unterminated block (missing '}')")
        return out

    result = block(True)
    if pos != len(tokens):
        raise VmtError("trailing tokens after top-level block")
    return result


def _shader_block(root: dict) -> tuple[str, dict]:
    """Return (shader_name, key block) from a parsed VMT root."""
    if not root:
        raise VmtError("empty VMT")
    # Normal case: exactly one top-level entry, shader -> block.
    candidates = [(k, v) for k, v in root.items() if isinstance(v, dict)]
    if len(candidates) == 1:
        return candidates[0]
    if len(root) == 1:
        key, val = next(iter(root.items()))
        if isinstance(val, str):
            # Broken/flat VMT: treat the single key as the shader name with
            # an empty body rather than crashing the whole resolution.
            return key, {}
    raise VmtError(f"VMT has {len(candidates)} shader blocks, expected 1")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def normalize_vmt_path(path: str) -> str:
    """Normalise a VMT path to the form used by vpk.Vpk (`materials/...vmt`)."""
    p = str(path).strip().strip('"').replace("\\", "/").lower()
    if p.startswith("./"):
        p = p[2:]
    # `include` takes a single token; drop anything after whitespace.
    p = p.split()[0] if p else ""
    if not p.startswith("materials/"):
        p = "materials/" + p
    if not p.endswith(".vmt"):
        p += ".vmt"
    return p


def normalize_texture_path(texture: str) -> str:
    """Normalise a material texture value to `dir/name` (lowercase, no .vtf).

    Shader arguments after the path (e.g. `"tex 0 0 1 1"`) are dropped, and
    runtime/proxy pseudo-textures (`_rt_*`, `$...`) yield an empty string.
    """
    s = str(texture).strip().strip('"').replace("\\", "/").lower()
    if not s:
        return ""
    if any(ch in s for ch in " \t"):
        s = s.split()[0]
    if s.startswith("_rt_") or s.startswith("$") or s.startswith("<"):
        return ""
    if s.endswith(".vtf"):
        s = s[:-4]
    return s


def _read_text(reader, path: str) -> str:
    """Call `reader(path)` (or `reader.read(path)`) and return decoded text."""
    if callable(reader):
        data = reader(path)
    elif hasattr(reader, "read"):
        data = reader.read(path)
    else:
        raise VmtError("reader must be callable or provide .read(path)")
    if data is None:
        raise VmtError(f"not found: {path}")
    if isinstance(data, (bytes, bytearray)):
        return data.decode("latin-1")
    return str(data)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def resolve(vmt_text: str, reader, _stack: tuple[str, ...] = ()) -> dict:
    """Resolve a VMT into its effective material dict.

    `reader` resolves an included VMT path to text (a callable taking the path
    or an object with `.read(path)` and VPK-compatible path keys). Patch
    includes are followed recursively; `replace` overwrites keys (a whole
    nested block is replaced) and `insert` only adds keys that are absent.
    Include cycles raise VmtError instead of recursing forever.
    """
    root = parse(vmt_text)
    shader, body = _shader_block(root)
    if shader.lower() != "patch":
        mat = dict(body)
        mat[SHADER_KEY] = shader
        mat[CHAIN_KEY] = tuple(_stack)
        return mat

    include = body.get("include")
    if not isinstance(include, str) or not include.strip():
        raise VmtError("patch material without an include")
    path = normalize_vmt_path(include)
    if path in _stack:
        chain = " -> ".join(_stack + (path,))
        raise VmtError(f"patch include cycle: {chain}")
    base = resolve(_read_text(reader, path), reader, _stack + (path,))

    replace = body.get("replace")
    if isinstance(replace, dict):
        for key, value in replace.items():
            base[key] = copy.deepcopy(value)
    insert = body.get("insert")
    if isinstance(insert, dict):
        for key, value in insert.items():
            if key not in base:
                base[key] = copy.deepcopy(value)
    base[SHADER_KEY] = shader_name_of(base) or base.get(SHADER_KEY, "")
    base[CHAIN_KEY] = tuple(_stack + (path,))
    return base


def shader_name_of(mat: dict) -> str:
    """Return the shader name of a resolved material ('' when unknown)."""
    return str(mat.get(SHADER_KEY, ""))


def get_texture_paths(mat: dict) -> list[str]:
    """Return unique `$basetexture[2,3]`/`$blendmodulatetexture` paths.

    Paths are lowercase with forward slashes and without the `.vtf` extension,
    ready to be looked up as `materials/<path>.vtf`.
    """
    out: list[str] = []
    for key in TEXTURE_KEYS:
        value = mat.get(key)
        if not isinstance(value, str):
            continue
        path = normalize_texture_path(value)
        if path and path not in out:
            out.append(path)
    return out


def _flag(mat: dict, key: str, default: bool = False) -> bool:
    value = mat.get(key)
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


def is_alpha_tested(mat: dict) -> bool:
    """`$alphatest 1` (with no `$translucent`) — render with alpha cutoff."""
    return _flag(mat, "$alphatest") and not _flag(mat, "$translucent")


def is_translucent(mat: dict) -> bool:
    """`$translucent 1`, or `$alpha 1` without alpha testing."""
    if _flag(mat, "$translucent"):
        return True
    return _flag(mat, "$alpha") and not _flag(mat, "$alphatest")


def is_decal(mat: dict) -> bool:
    """`$decal 1` — decal/overlay material, rendered with alpha blending."""
    return _flag(mat, "$decal")


def has_vertex_color(mat: dict) -> bool:
    """`$vertexcolor 1` — the shader multiplies COLOR_0 into the albedo."""
    return _flag(mat, "$vertexcolor")


def has_vertex_alpha(mat: dict) -> bool:
    """`$vertexalpha 1` — the shader takes opacity from COLOR_0.a."""
    return _flag(mat, "$vertexalpha")


def alpha_test_reference(mat: dict, default: float = 0.5) -> float:
    """`$alphatestreference` as a glTF alphaCutoff, clamped to [0, 1]."""
    value = mat.get("$alphatestreference")
    if not isinstance(value, str):
        return default
    try:
        ref = float(value.strip())
    except ValueError:
        return default
    if not math.isfinite(ref):
        return default
    return min(1.0, max(0.0, ref))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: vmt.py <file.vmt>", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="latin-1") as f:
        text = f.read()
    print(json.dumps(parse(text), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
