"""Borderlands 2 exe package-hash table.

The shipped `Borderlands2.exe` embeds a table of ``<lowercase filename>\\0<20-byte SHA1>``
entries (12 packages + shaders + DLC license files). At load time the engine hashes the
package's *uncompressed* bytes and aborts (RaiseException code 1 from appErrorf) on a
mismatch. Only the listed files are checked; new package files are not.

Verified 2026-09-19: table entry ``startup.upk`` == SHA1(decompressed Startup.upk).

Usage:
    python -m bl2_upk.exe_sha list  <exe>
    python -m bl2_upk.exe_sha set   <exe> <out_exe> <name> <package_file>   # writes a patched COPY
    python -m bl2_upk.exe_sha check <exe> <name> <package_file>
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_NAME_RE = re.compile(rb"[a-z0-9_\-\.]+")
_ANCHOR = b"core.upk\x00"


@dataclass(frozen=True)
class HashEntry:
    name: str
    sha1: str
    name_offset: int
    hash_offset: int


def read_table(exe_bytes: bytes) -> list[HashEntry]:
    start = exe_bytes.find(_ANCHOR)
    if start < 0:
        raise ValueError("hash table anchor 'core.upk' not found")
    pos = start
    out: list[HashEntry] = []
    while True:
        end = exe_bytes.find(b"\x00", pos)
        name = exe_bytes[pos:end]
        if not _NAME_RE.fullmatch(name):
            break
        out.append(HashEntry(name.decode(), exe_bytes[end + 1 : end + 21].hex(), pos, end + 1))
        pos = end + 21
    return out


def sha1_file(path: str | Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def patched_exe(exe_bytes: bytes, name: str, new_sha1_hex: str) -> bytes:
    entry = next((e for e in read_table(exe_bytes) if e.name == name.lower()), None)
    if entry is None:
        raise KeyError(f"{name} not in hash table")
    new = bytes.fromhex(new_sha1_hex)
    if len(new) != 20:
        raise ValueError("sha1 must be 20 bytes")
    return exe_bytes[: entry.hash_offset] + new + exe_bytes[entry.hash_offset + 20 :]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    exe = Path(argv[1])
    data = exe.read_bytes()
    if cmd == "list":
        for e in read_table(data):
            print(f"{e.name:40s} {e.sha1}  @{e.hash_offset}")
        return 0
    if cmd == "check":
        name, pkg = argv[2], argv[3]
        entry = next(e for e in read_table(data) if e.name == name.lower())
        actual = sha1_file(pkg)
        print(f"table {entry.sha1}\nfile  {actual}\n{'MATCH' if actual == entry.sha1 else 'MISMATCH'}")
        return 0 if actual == entry.sha1 else 1
    if cmd == "set":
        out, name, pkg = Path(argv[2]), argv[3], argv[4]
        new = sha1_file(pkg)
        out.write_bytes(patched_exe(data, name, new))
        entry = next(e for e in read_table(out.read_bytes()) if e.name == name.lower())
        print(f"wrote {out} with {name} = {entry.sha1} (from {pkg})")
        return 0 if entry.sha1 == new else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
