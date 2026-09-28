"""Write a modified export payload back into a UE3 package file.

Two strategies:

``inplace``
    The new payload must have exactly the original SerialSize; the bytes are
    overwritten where they are.  The export table is untouched.

``relocate_to_end``
    The new payload is appended to the end of the file and the export's
    ``SerialOffset``/``SerialSize`` are rewritten in place inside the export
    table.  Nothing else in the file moves, so every other export's offsets
    stay valid.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Literal

from .reader import (
    EXPORT_SERIAL_OFFSET_OFFSET,
    EXPORT_SERIAL_SIZE_OFFSET,
    ExportEntry,
    Package,
)
from .skelmesh import SkeletalMeshExport

__all__ = ["PatchError", "PatchResult", "replace_export_payload"]

Strategy = Literal["inplace", "relocate_to_end"]

_I32 = struct.Struct("<i")


class PatchError(Exception):
    """Raised when a payload cannot be written back safely."""


class PatchResult:
    """Where the patched export ended up, plus the re-read verification."""

    __slots__ = ("out_path", "export_offset", "serial_size", "package", "mesh")

    def __init__(
        self,
        out_path: Path,
        export_offset: int,
        serial_size: int,
        package: Package,
        mesh: SkeletalMeshExport | None,
    ) -> None:
        self.out_path = out_path
        self.export_offset = export_offset
        self.serial_size = serial_size
        self.package = package
        self.mesh = mesh


def _patch_export_entry(buf: bytearray, entry: ExportEntry, size: int, offset: int) -> None:
    base = entry.entry_offset
    _I32.pack_into(buf, base + EXPORT_SERIAL_SIZE_OFFSET, size)
    _I32.pack_into(buf, base + EXPORT_SERIAL_OFFSET_OFFSET, offset)


def _verify(out_path: Path, export_path: str, expect_offset: int, expect_size: int,
            verify_mesh: bool) -> PatchResult:
    package = Package.from_file(out_path)
    entry = package.find_export(export_path)
    if entry.off != expect_offset or entry.size != expect_size:
        raise PatchError(
            f"verification failed: export table says {entry.off}/{entry.size}, "
            f"expected {expect_offset}/{expect_size}"
        )
    mesh = None
    if verify_mesh:
        mesh = SkeletalMeshExport.parse(package.read_export_bytes(entry), package)
        for lod in mesh.lods:
            lod.validate()
    return PatchResult(out_path, entry.off, entry.size, package, mesh)


def replace_export_payload(
    package_path: str | Path,
    export_path: str,
    new_bytes: bytes,
    out_path: str | Path,
    strategy: Strategy = "inplace",
    verify_mesh: bool = True,
) -> PatchResult:
    """Write ``new_bytes`` as the payload of ``export_path`` into ``out_path``.

    The output is always re-read with the reader (and, for a SkeletalMesh,
    re-parsed) before returning.
    """
    src = Path(package_path)
    dst = Path(out_path)
    package = Package.from_file(src)
    entry = package.find_export(export_path)
    buf = bytearray(package.data)

    if strategy == "inplace":
        if len(new_bytes) != entry.size:
            raise PatchError(
                f"inplace needs exactly {entry.size} bytes, got {len(new_bytes)}"
            )
        buf[entry.off : entry.off + entry.size] = new_bytes
        new_offset, new_size = entry.off, entry.size
    elif strategy == "relocate_to_end":
        new_offset = len(buf)
        new_size = len(new_bytes)
        buf += new_bytes
        _patch_export_entry(buf, entry, new_size, new_offset)
    else:  # pragma: no cover - guarded by the Literal type
        raise PatchError(f"unknown strategy {strategy!r}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(buf))
    return _verify(
        dst, export_path, new_offset, new_size,
        verify_mesh and entry.clsname == "SkeletalMesh",
    )
