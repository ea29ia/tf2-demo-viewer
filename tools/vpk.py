#!/usr/bin/env python3
"""Minimal Valve Pak (VPK) v1/v2 reader, stdlib only.

A VPK set is a `*_dir.vpk` directory file plus optional `*_000.vpk`,
`*_001.vpk`, ... data archives. This module parses the directory tree and
returns file payloads without unpacking the whole archive.

Usage:
    python3 tools/vpk.py <dir.vpk> list [substring]
    python3 tools/vpk.py <dir.vpk> extract <internal/path> <out-file>

Internal paths include the extension, e.g.
    models/player/scout.mdl
    materials/models/player/scout/scout_red.vmt
"""
from __future__ import annotations

import os
import struct
import sys

MAGIC = 0x55AA1234
DIR_ARCHIVE = 0x7FFF


class VpkError(Exception):
    pass


class Vpk:
    def __init__(self, dir_path: str):
        self.dir_path = dir_path
        if dir_path.endswith("_dir.vpk"):
            self.base = dir_path[: -len("_dir.vpk")]
        else:
            self.base = dir_path[:-4]
        with open(dir_path, "rb") as f:
            self.data = f.read()
        self.entries: dict[str, tuple[int, int, int, int, int]] = {}
        self.preload: dict[str, bytes] = {}
        self._parse()

    def _parse(self) -> None:
        data = self.data
        magic, version, tree_size = struct.unpack_from("<III", data, 0)
        if magic != MAGIC:
            raise VpkError(f"bad VPK magic 0x{magic:08x}")
        if version not in (1, 2):
            raise VpkError(f"unsupported VPK version {version}")
        header_size = 12 if version == 1 else 28
        tree_end = header_size + tree_size
        if tree_end > len(data):
            raise VpkError("directory tree exceeds file size")
        # Preload payloads are stored after the tree, in entry order.
        preload_cursor = tree_end
        pos = header_size

        def read_cstr(p: int) -> tuple[str, int]:
            end = data.find(b"\0", p)
            if end < 0:
                raise VpkError("unterminated string in tree")
            return data[p:end].decode("utf-8", errors="replace"), end + 1

        while pos < tree_end:
            ext, pos = read_cstr(pos)
            if ext == "":
                break
            while True:
                path, pos = read_cstr(pos)
                if path == "":
                    break
                while True:
                    name, pos = read_cstr(pos)
                    if name == "":
                        break
                    crc, preload_len, archive, offset, length = struct.unpack_from(
                        "<IHHII", data, pos)
                    pos += 18
                    key = ((path + "/" if path else "") + name + "." + ext).lower()
                    self.entries[key] = (crc, preload_len, archive, offset, length)
                    if preload_len:
                        self.preload[key] = data[preload_cursor:preload_cursor + preload_len]
                        preload_cursor += preload_len

    def _archive_path(self, archive: int) -> str:
        return f"{self.base}_{archive:03d}.vpk"

    def read(self, path: str) -> bytes:
        key = path.lower().replace("\\", "/")
        entry = self.entries.get(key)
        if entry is None:
            raise VpkError(f"not in archive: {path}")
        _crc, preload_len, archive, offset, length = entry
        out = bytearray(self.preload.get(key, b""))
        payload = length - preload_len
        if payload > 0:
            if archive == DIR_ARCHIVE:
                out += self.data[offset:offset + payload]
            else:
                with open(self._archive_path(archive), "rb") as f:
                    f.seek(offset)
                    out += f.read(payload)
        return bytes(out)

    def exists(self, path: str) -> bool:
        return path.lower().replace("\\", "/") in self.entries

    def find(self, substring: str) -> list[str]:
        needle = substring.lower()
        return sorted(p for p in self.entries if needle in p)


class LoosePack:
    """Reads files from a plain directory tree (the layout used by the
    offline extraction in ~/Documents/tf2/tf). Same interface as Vpk:
    `entries` maps path -> entry tuple (archive field is 0x7FFF),
    `read`/`exists`/`find` behave like the VPK equivalents."""

    DIR_ARCHIVE = 0x7FFF

    def __init__(self, root: str):
        self.root = root
        self.entries: dict[str, tuple[int, int, int, int, int]] = {}
        self.preload: dict[str, bytes] = {}
        for base, _dirs, files in os.walk(root):
            for fn in files:
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/").lower()
                size = os.path.getsize(full)
                self.entries[rel] = (0, 0, self.DIR_ARCHIVE, 0, size)

    def read(self, path: str) -> bytes:
        key = path.lower().replace("\\", "/")
        entry = self.entries.get(key)
        if entry is None:
            raise VpkError(f"not in pack: {path}")
        with open(os.path.join(self.root, key), "rb") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        return path.lower().replace("\\", "/") in self.entries

    def find(self, substring: str) -> list[str]:
        needle = substring.lower()
        return sorted(p for p in self.entries if needle in p)


def open_pack(tf_dir: str, name: str):
    """Open `<tf_dir>/<name>_dir.vpk` when present, else the loose
    directory tree `<tf_dir>` (offline extraction)."""
    dir_path = os.path.join(tf_dir, name + "_dir.vpk")
    if os.path.exists(dir_path):
        return Vpk(dir_path)
    return LoosePack(tf_dir)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: vpk.py <dir.vpk> list [substring] | extract <path> <out>", file=sys.stderr)
        return 2
    vpk = Vpk(argv[1])
    cmd = argv[2]
    if cmd == "list":
        for path in vpk.find(argv[3] if len(argv) > 3 else ""):
            print(path)
        return 0
    if cmd == "extract":
        if len(argv) != 5:
            print("usage: vpk.py <dir.vpk> extract <path> <out>", file=sys.stderr)
            return 2
        payload = vpk.read(argv[3])
        os.makedirs(os.path.dirname(os.path.abspath(argv[4])), exist_ok=True)
        with open(argv[4], "wb") as f:
            f.write(payload)
        print(f"{argv[3]} -> {argv[4]} ({len(payload)} bytes)")
        return 0
    print(f"unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
