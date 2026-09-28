"""UE3 (Borderlands 2: ArVer 832 / LicenseeVer 46) package table reader.

Handles *decompressed* packages only: a package whose header advertises
compressed chunks is rejected, because the chunk payloads have to be inflated
by an external tool first.

Refactored from ``scratch/upkread.py`` (kept intact there for other tooling).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PACKAGE_TAG",
    "EXPORT_ENTRY_FIXED_SIZE",
    "EXPORT_SERIAL_SIZE_OFFSET",
    "EXPORT_SERIAL_OFFSET_OFFSET",
    "FName",
    "PackageSummary",
    "ImportEntry",
    "ExportEntry",
    "Package",
    "PackageError",
]

PACKAGE_TAG = 0x9E2A83C1

#: Bytes of an export table entry excluding the NetObjectCount array payload.
#: Class/Super/Outer (12) + Name FName (8) + Archetype (4) + ObjectFlags (8)
#: + SerialSize (4) + SerialOffset (4) + ExportFlags (4) + NetObjectCount (4)
#: + Guid (16) + PackageFlags (4) == 68.
EXPORT_ENTRY_FIXED_SIZE = 68

#: Byte offsets of the two patchable fields inside an export table entry.
EXPORT_SERIAL_SIZE_OFFSET = 32
EXPORT_SERIAL_OFFSET_OFFSET = 36

_I32 = struct.Struct("<i")
_U32 = struct.Struct("<I")
_I64 = struct.Struct("<q")
_FNAME = struct.Struct("<ii")


class PackageError(Exception):
    """Raised when a file is not a readable uncompressed UE3 package."""


@dataclass(slots=True, frozen=True)
class FName:
    """A serialized UE3 name reference: name-table index plus instance number."""

    index: int
    number: int
    text: str = ""

    def __str__(self) -> str:
        return self.text

    def to_bytes(self) -> bytes:
        return _FNAME.pack(self.index, self.number)


@dataclass(slots=True)
class PackageSummary:
    version: int
    licensee: int
    headers_size: int
    package_group: str
    package_flags: int
    name_count: int
    name_offset: int
    export_count: int
    export_offset: int
    import_count: int
    import_offset: int
    depends_offset: int
    guid: bytes
    engine_version: int
    cooker_version: int
    compression_flags: int
    chunk_count: int
    file_size: int


@dataclass(slots=True)
class ImportEntry:
    class_package: str
    class_name: str
    outer: int
    name: str


@dataclass(slots=True)
class ExportEntry:
    """One row of the export table, plus its on-disk position."""

    index: int
    class_index: int
    super_index: int
    outer_index: int
    name: str
    archetype_index: int
    object_flags: int
    size: int
    off: int
    export_flags: int
    net_object_count: int
    entry_offset: int
    entry_size: int
    clsname: str = ""
    path: str = ""


def _decode_fstring(data: bytes, pos: int) -> tuple[str, int]:
    (n,) = _I32.unpack_from(data, pos)
    pos += 4
    if n == 0:
        return "", pos
    if n < 0:
        end = pos - 2 * n
        text = data[pos:end].decode("utf-16-le", "replace")
    else:
        end = pos + n
        text = data[pos:end].decode("latin-1", "replace")
    return text.rstrip("\0"), end


class Package:
    """Parsed name/import/export tables of one uncompressed UE3 package."""

    def __init__(self, path: str | Path, data: bytes) -> None:
        self.path = Path(path)
        self.data = data
        self.names: list[str] = []
        self.imports: list[ImportEntry] = []
        self.exports: list[ExportEntry] = []
        self._by_path: dict[str, ExportEntry] = {}
        self.summary = self._read_summary()
        self._read_names()
        self._read_imports()
        self._read_exports()
        self._resolve_paths()

    @classmethod
    def from_file(cls, path: str | Path) -> "Package":
        p = Path(path)
        return cls(p, p.read_bytes())

    # -- header ----------------------------------------------------------

    def _read_summary(self) -> PackageSummary:
        data = self.data
        (tag,) = _U32.unpack_from(data, 0)
        if tag != PACKAGE_TAG:
            raise PackageError(f"bad package tag 0x{tag:08X} (fully compressed?)")
        version, licensee = struct.unpack_from("<HH", data, 4)
        (headers_size,) = _I32.unpack_from(data, 8)
        group, pos = _decode_fstring(data, 12)
        (package_flags,) = _U32.unpack_from(data, pos)
        pos += 4
        nc, no, ec, eo, ic, io, depends = struct.unpack_from("<7i", data, pos)
        pos += 28
        # ArVer >= 623: ImportExportGuidsOffset, ImportGuidsCount, ExportGuidsCount
        # ArVer >= 584: ThumbnailTableOffset
        pos += 16
        guid = bytes(data[pos : pos + 16])
        pos += 16
        (gen_count,) = _I32.unpack_from(data, pos)
        pos += 4 + gen_count * 12
        engine_version, cooker_version = struct.unpack_from("<ii", data, pos)
        pos += 8
        (compression_flags,) = _U32.unpack_from(data, pos)
        pos += 4
        (chunk_count,) = _I32.unpack_from(data, pos)
        if chunk_count:
            raise PackageError(
                f"{self.path.name} has {chunk_count} compressed chunks; decompress it first"
            )
        return PackageSummary(
            version=version,
            licensee=licensee,
            headers_size=headers_size,
            package_group=group,
            package_flags=package_flags,
            name_count=nc,
            name_offset=no,
            export_count=ec,
            export_offset=eo,
            import_count=ic,
            import_offset=io,
            depends_offset=depends,
            guid=guid,
            engine_version=engine_version,
            cooker_version=cooker_version,
            compression_flags=compression_flags,
            chunk_count=chunk_count,
            file_size=len(data),
        )

    # -- tables ----------------------------------------------------------

    def _read_names(self) -> None:
        data = self.data
        pos = self.summary.name_offset
        names = self.names
        for _ in range(self.summary.name_count):
            text, pos = _decode_fstring(data, pos)
            names.append(text)
            pos += 8  # uint64 name flags

    def fname(self, index: int, number: int) -> FName:
        """Build an :class:`FName` with its display text resolved."""
        base = self.names[index] if 0 <= index < len(self.names) else f"<name {index}>"
        text = f"{base}_{number - 1}" if number else base
        return FName(index, number, text)

    def read_fname(self, data: bytes, pos: int) -> tuple[FName, int]:
        index, number = _FNAME.unpack_from(data, pos)
        return self.fname(index, number), pos + 8

    def _read_imports(self) -> None:
        data = self.data
        pos = self.summary.import_offset
        for _ in range(self.summary.import_count):
            pi, pn, ci, cn, outer, ni, nn = struct.unpack_from("<7i", data, pos)
            pos += 28
            self.imports.append(
                ImportEntry(
                    class_package=self.fname(pi, pn).text,
                    class_name=self.fname(ci, cn).text,
                    outer=outer,
                    name=self.fname(ni, nn).text,
                )
            )

    def _read_exports(self) -> None:
        data = self.data
        pos = self.summary.export_offset
        names = self.names
        for i in range(self.summary.export_count):
            entry_offset = pos
            cls, sup, outer, ni, nn = struct.unpack_from("<5i", data, pos)
            (arch,) = _I32.unpack_from(data, pos + 20)
            (oflags,) = _I64.unpack_from(data, pos + 24)
            ssize, soff, eflags, ncomp = struct.unpack_from("<iiIi", data, pos + 32)
            entry_size = EXPORT_ENTRY_FIXED_SIZE + ncomp * 4
            base = names[ni] if 0 <= ni < len(names) else f"<name {ni}>"
            self.exports.append(
                ExportEntry(
                    index=i + 1,
                    class_index=cls,
                    super_index=sup,
                    outer_index=outer,
                    name=f"{base}_{nn - 1}" if nn else base,
                    archetype_index=arch,
                    object_flags=oflags,
                    size=ssize,
                    off=soff,
                    export_flags=eflags,
                    net_object_count=ncomp,
                    entry_offset=entry_offset,
                    entry_size=entry_size,
                )
            )
            pos = entry_offset + entry_size

    def _resolve_paths(self) -> None:
        cache: dict[int, str] = {0: ""}
        exports = self.exports
        imports = self.imports

        def full_path(obj_index: int) -> str:
            cached = cache.get(obj_index)
            if cached is not None:
                return cached
            if obj_index > 0:
                entry = exports[obj_index - 1]
                name, outer = entry.name, entry.outer_index
            else:
                imp = imports[-obj_index - 1]
                name, outer = imp.name, imp.outer
            parent = full_path(outer)
            value = f"{parent}.{name}" if parent else name
            cache[obj_index] = value
            return value

        for entry in exports:
            entry.path = full_path(entry.index)
            entry.clsname = self.object_name(entry.class_index)
            self._by_path.setdefault(entry.path, entry)

    # -- object references ------------------------------------------------

    def object_name(self, obj_index: int) -> str:
        if obj_index > 0:
            return self.exports[obj_index - 1].name
        if obj_index < 0:
            return self.imports[-obj_index - 1].name
        return "None"

    def object_path(self, obj_index: int) -> str:
        """Resolve a UE3 object index to a dotted path (``None`` for index 0)."""
        if obj_index == 0:
            return "None"
        if obj_index > 0:
            return self.exports[obj_index - 1].path
        parts: list[str] = []
        idx = obj_index
        while idx:
            if idx > 0:
                entry = self.exports[idx - 1]
                parts.append(entry.name)
                idx = entry.outer_index
            else:
                imp = self.imports[-idx - 1]
                parts.append(imp.name)
                idx = imp.outer
        return ".".join(reversed(parts))

    # -- lookups ----------------------------------------------------------

    def find_export(self, path: str, clsname: str | None = None) -> ExportEntry:
        """Find one export by its dotted path, optionally filtering by class."""
        entry = self._by_path.get(path)
        if entry is not None and (clsname is None or entry.clsname == clsname):
            return entry
        for candidate in self.exports:
            if candidate.path == path and (clsname is None or candidate.clsname == clsname):
                return candidate
        raise KeyError(f"export {path!r} not found in {self.path.name}")

    def exports_of_class(self, clsname: str) -> list[ExportEntry]:
        return [e for e in self.exports if e.clsname == clsname]

    def read_export_bytes(self, export: ExportEntry) -> bytes:
        """Return the serialized payload of one export."""
        end = export.off + export.size
        if end > len(self.data):
            raise PackageError(
                f"export {export.path!r} payload [{export.off}:{end}] exceeds file size"
            )
        return bytes(self.data[export.off : end])

    def find_name(self, text: str) -> int:
        """Return the name-table index of ``text`` (raises ValueError if absent)."""
        return self.names.index(text)
