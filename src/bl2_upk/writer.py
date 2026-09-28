"""Write a brand-new cooked UE3 package (ArVer 832 / LicenseeVer 46, PC).

Why this exists: the 12 base Borderlands 2 packages (``Startup.upk`` included)
are SHA1-verified by ``Borderlands2.exe`` (see ``docs/FAILURE_MODES.md`` F12),
so a mesh edit can never ship as a patch to one of them.  New package files are
not hashed, so the pipeline emits a *new* ``.upk`` that the game picks up with
``unrealsdk.load_package("<name>")``.

The on-disk layout reproduced here was derived byte for byte from
``scratch/decomp/Startup.upk``, ``MainGameRefs_SF.upk`` and ``Sanctuary_P.upk``;
every constant is documented in ``docs/BL2_UPK_WRITER_NOTES.md``.

Two layers:

:class:`PackageBuilder`
    Assembles name table, import table, export table, depends table, summary and
    export payloads from scratch.

:func:`clone_skeletal_mesh`
    Copies one ``SkeletalMesh`` export plus its ``SkeletalMeshSocket``
    sub-objects out of an existing package and into a new one, remapping every
    FName index and every object reference on the way.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .props import PropertyTag, find_property, parse_properties, serialize_properties
from .reader import FName, Package
from .skelmesh import SkeletalMeshExport

__all__ = [
    "NAME_FLAGS",
    "OBJECT_FLAGS_TOP_LEVEL",
    "OBJECT_FLAGS_SUB_OBJECT",
    "EXPORT_FLAGS_COOKED",
    "PACKAGE_FLAGS_COOKED_CONTENT",
    "PKG_COMPRESSION_BITS",
    "WriterError",
    "PayloadSource",
    "PackageBuilder",
    "CloneResult",
    "qualified_path",
    "clone_skeletal_mesh",
]

_I32 = struct.Struct("<i")
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_FNAME = struct.Struct("<ii")

_NONE = "None"

#: uint64 name-table flags.  28,571 of Startup.upk's 28,576 names use this value
#: (the other five use 0x0007101000000000); every name we emit gets the common one.
NAME_FLAGS = 0x0007001000000000

#: ObjectFlags of Startup's ``GestaltDef_AssaultRifle_GestaltSkeletalMesh`` export.
OBJECT_FLAGS_TOP_LEVEL = 0x0007000400000000

#: ObjectFlags of Startup's ``SkeletalMeshSocket_*`` sub-object exports.
OBJECT_FLAGS_SUB_OBJECT = 0x0007000000000000

#: ExportFlags on the template mesh/socket exports.  EF_ForcedExport (0x02) is not
#: set; 0x01 is what the cooker writes on every export of every template package.
EXPORT_FLAGS_COOKED = 0x00000000  # 0 = owned by this file's package; 1 (EF_ForcedExport) is only for embedded Package exports and their children (see FAILURE_MODES F13)

#: PKG_StoreCompressed | PKG_StoreFullyCompressed -- must never be set on a file
#: whose payloads we write uncompressed.
PKG_COMPRESSION_BITS = 0x02000000 | 0x04000000

#: PackageFlags for an uncompressed cooked content package.  This is
#: ``MainGameRefs_SF.upk``'s value verbatim: PKG_AllowDownload (0x1) |
#: PKG_Cooked (0x8) | PKG_DisallowLazyLoading (0x00080000) |
#: PKG_RequireImportsAlreadyLoaded (0x00800000) | PKG_FilterEditorOnly (0x80000000).
PACKAGE_FLAGS_COOKED_CONTENT = 0x80880009

#: Struct names whose bodies are native (fixed-size) rather than tagged property
#: lists, so the remapper must never try to walk into them.
_NATIVE_STRUCTS = frozenset(
    {
        "Vector", "Vector2D", "Vector4", "Rotator", "Quat", "Plane", "Matrix",
        "Color", "LinearColor", "Guid", "Box", "BoxSphereBounds2D", "IntPoint",
        "IntRect", "TwoVectors", "InterpCurvePointFloat", "Double",
    }
)

#: Property types whose 4-byte body is an object reference.
_OBJECT_TYPES = frozenset(
    {"ObjectProperty", "ClassProperty", "ComponentProperty", "InterfaceProperty"}
)

#: ArrayProperty names whose POD int32 elements are object references.  UE3 does
#: not store an array's element type, so this has to be driven by name; the only
#: one that matters for a SkeletalMesh is ``Sockets``.
_OBJECT_REF_ARRAYS = frozenset({"Sockets", "Materials", "ClothingAssets"})


class WriterError(Exception):
    """Raised when a package cannot be assembled consistently."""


#: An export payload: either the finished bytes, or a callable that is handed the
#: absolute file offset the payload will land at and returns the bytes.  The
#: callable is invoked twice -- once with a provisional offset purely to measure
#: the length, once with the real offset -- and must return the same length both
#: times (true for SkeletalMesh, whose only offset-dependent field is the 4-byte
#: ``RawPointIndices.OffsetInFile``).
PayloadSource = bytes | Callable[[int], bytes]


# ---------------------------------------------------------------------------
# table rows
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _NameRow:
    text: str
    flags: int = NAME_FLAGS

    def to_bytes(self) -> bytes:
        raw = self.text.encode("latin-1") + b"\0"
        return _I32.pack(len(raw)) + raw + _U64.pack(self.flags)


@dataclass(slots=True)
class _ImportRow:
    class_package: tuple[int, int]
    class_name: tuple[int, int]
    outer: int
    name: tuple[int, int]

    def to_bytes(self) -> bytes:
        return struct.pack(
            "<7i",
            self.class_package[0], self.class_package[1],
            self.class_name[0], self.class_name[1],
            self.outer,
            self.name[0], self.name[1],
        )


@dataclass(slots=True)
class _ExportRow:
    class_ref: int
    super_ref: int
    outer_ref: int
    name: tuple[int, int]
    archetype_ref: int
    object_flags: int
    export_flags: int
    net_objects: list[int]
    guid: bytes
    package_flags: int
    payload: PayloadSource
    #: Filled in by :meth:`PackageBuilder.write`.
    serial_offset: int = 0
    serial_size: int = 0

    @property
    def entry_size(self) -> int:
        return 68 + 4 * len(self.net_objects)

    def to_bytes(self) -> bytes:
        out = bytearray()
        out += struct.pack("<3i", self.class_ref, self.super_ref, self.outer_ref)
        out += _FNAME.pack(*self.name)
        out += _I32.pack(self.archetype_ref)
        out += _U64.pack(self.object_flags)
        out += _I32.pack(self.serial_size)
        out += _I32.pack(self.serial_offset)
        out += _U32.pack(self.export_flags)
        out += _I32.pack(len(self.net_objects))
        for value in self.net_objects:
            out += _I32.pack(value)
        out += self.guid
        out += _U32.pack(self.package_flags)
        return bytes(out)


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


class PackageBuilder:
    """Assemble a v832/46 cooked package from scratch.

    ``template`` is only read for the three values a fresh package has no way to
    invent (``EngineVersion``, ``CookerVersion``, ``PackageGroup``); everything
    else is either derived or taken from the documented constants above.
    """

    def __init__(
        self,
        package_name: str,
        template: Package | None = None,
        *,
        package_group: str | None = None,
        package_flags: int | None = None,
        engine_version: int | None = None,
        cooker_version: int | None = None,
        package_source: int | None = None,
        guid: bytes | None = None,
    ) -> None:
        self.package_name = package_name
        self.version = 832
        self.licensee = 46
        self.package_group = (
            package_group
            if package_group is not None
            else (template.summary.package_group if template else _NONE)
        )
        flags = (
            package_flags
            if package_flags is not None
            else (
                template.summary.package_flags
                if template is not None
                else PACKAGE_FLAGS_COOKED_CONTENT
            )
        )
        #: Compression bits are cleared unconditionally: we always write the
        #: payloads raw, and Startup.upk's own value has PKG_StoreFullyCompressed.
        self.package_flags = flags & ~PKG_COMPRESSION_BITS
        self.engine_version = (
            engine_version
            if engine_version is not None
            else (template.summary.engine_version if template else 1712575)
        )
        self.cooker_version = (
            cooker_version
            if cooker_version is not None
            else (template.summary.cooker_version if template else 134)
        )
        self.package_source = (
            package_source if package_source is not None else int.from_bytes(os.urandom(4), "little")
        )
        self.guid = guid if guid is not None else os.urandom(16)
        if len(self.guid) != 16:
            raise WriterError("package Guid must be exactly 16 bytes")

        self._names: list[_NameRow] = []
        self._name_index: dict[str, int] = {}
        self._imports: list[_ImportRow] = []
        self._import_index: dict[tuple[str, str, int, str, int], int] = {}
        self._exports: list[_ExportRow] = []

        # ``None`` is interned first so it is always present for property-list
        # terminators, and so a fresh package always has at least one name.
        self.name_index(_NONE)

    # -- names -------------------------------------------------------------

    def name_index(self, text: str) -> int:
        """Intern ``text`` in the name table and return its index."""
        existing = self._name_index.get(text)
        if existing is not None:
            return existing
        index = len(self._names)
        self._names.append(_NameRow(text))
        self._name_index[text] = index
        return index

    def fname(self, text: str, number: int = 0) -> FName:
        """Build an :class:`FName` against this package's name table."""
        index = self.name_index(text)
        display = f"{text}_{number - 1}" if number else text
        return FName(index, number, display)

    @property
    def none_fname(self) -> FName:
        return self.fname(_NONE)

    @property
    def names(self) -> list[str]:
        return [row.text for row in self._names]

    # -- imports -----------------------------------------------------------

    def add_import(
        self,
        class_package: str,
        class_name: str,
        outer: int,
        name: str,
        name_number: int = 0,
    ) -> int:
        """Add (or reuse) an import row; returns its negative object reference."""
        key = (class_package, class_name, outer, name, name_number)
        existing = self._import_index.get(key)
        if existing is not None:
            return existing
        row = _ImportRow(
            class_package=(self.name_index(class_package), 0),
            class_name=(self.name_index(class_name), 0),
            outer=outer,
            name=(self.name_index(name), name_number),
        )
        self._imports.append(row)
        ref = -len(self._imports)
        self._import_index[key] = ref
        return ref

    def import_package(self, name: str) -> int:
        """Import a top-level package object (``Core``/``Package``, Outer 0).

        Mirrors Startup.upk import rows 557/558 (``Core``, ``Engine``).
        """
        return self.add_import("Core", "Package", 0, name)

    def import_class(self, package: str, class_name: str) -> int:
        """Import a UClass (``Core``/``Class``) living in ``package``.

        Mirrors Startup.upk import rows 149/151 (``Engine.SkeletalMesh``,
        ``Engine.SkeletalMeshSocket``).
        """
        outer = self.import_package(package)
        return self.add_import("Core", "Class", outer, class_name)

    @property
    def import_count(self) -> int:
        return len(self._imports)

    # -- exports -----------------------------------------------------------

    def add_export(
        self,
        name: str,
        class_ref: int,
        outer: int = 0,
        payload: PayloadSource = b"",
        object_flags: int = OBJECT_FLAGS_TOP_LEVEL,
        archetype: int = 0,
        *,
        name_number: int = 0,
        super_ref: int = 0,
        export_flags: int = EXPORT_FLAGS_COOKED,
        net_objects: Sequence[int] | None = None,
        guid: bytes = b"\0" * 16,
        package_flags: int = 0,
    ) -> int:
        """Add an export row; returns its positive object reference (1-based)."""
        if len(guid) != 16:
            raise WriterError(f"export {name!r}: Guid must be 16 bytes")
        row = _ExportRow(
            class_ref=class_ref,
            super_ref=super_ref,
            outer_ref=outer,
            name=(self.name_index(name), name_number),
            archetype_ref=archetype,
            object_flags=object_flags,
            export_flags=export_flags,
            net_objects=list(net_objects or ()),
            guid=bytes(guid),
            package_flags=package_flags,
            payload=payload,
        )
        self._exports.append(row)
        return len(self._exports)

    @property
    def export_count(self) -> int:
        return len(self._exports)

    def export_name(self, ref: int) -> str:
        return self._names[self._exports[ref - 1].name[0]].text

    # -- layout ------------------------------------------------------------

    def _summary_bytes(
        self,
        headers_size: int,
        name_offset: int,
        export_offset: int,
        import_offset: int,
        depends_offset: int,
        net_object_count: int,
    ) -> bytes:
        out = bytearray()
        out += _U32.pack(0x9E2A83C1)
        out += struct.pack("<HH", self.version, self.licensee)
        out += _I32.pack(headers_size)
        group = self.package_group.encode("latin-1") + b"\0"
        out += _I32.pack(len(group)) + group
        out += _U32.pack(self.package_flags)
        out += struct.pack(
            "<7i",
            len(self._names), name_offset,
            len(self._exports), export_offset,
            len(self._imports), import_offset,
            depends_offset,
        )
        # ImportExportGuidsOffset / ImportGuidsCount / ExportGuidsCount.  Both
        # Startup.upk and MainGameRefs_SF.upk park the offset at HeadersSize with
        # zero counts; Sanctuary_P.upk, which does carry import Guids, puts it
        # *before* HeadersSize -- so HeadersSize is "end of all header data",
        # not "end of the depends table".  With zero Guids they coincide.
        out += struct.pack("<3i", headers_size, 0, 0)
        out += _I32.pack(0)  # ThumbnailTableOffset
        out += self.guid
        out += _I32.pack(1)  # GenerationCount
        out += struct.pack("<3i", len(self._exports), len(self._names), net_object_count)
        out += struct.pack("<2i", self.engine_version, self.cooker_version)
        out += _U32.pack(0)  # CompressionFlags
        out += _I32.pack(0)  # CompressedChunks count
        out += _U32.pack(self.package_source & 0xFFFFFFFF)
        out += _I32.pack(0)  # AdditionalPackagesToCook
        out += _I32.pack(0)  # TextureAllocations
        return bytes(out)

    def _resolve_payloads(self, base: int) -> list[bytes]:
        """Serialize every payload at its final absolute offset."""
        # First pass: measure.  Offsets are only consumed by absolute bulk-data
        # fields, which are fixed width, so the length cannot change.
        sizes: list[int] = []
        for row in self._exports:
            payload = row.payload
            sizes.append(len(payload) if isinstance(payload, bytes) else len(payload(base)))
        # Second pass: serialize where the bytes will actually live.
        blobs: list[bytes] = []
        offset = base
        for row, size in zip(self._exports, sizes):
            payload = row.payload
            blob = payload if isinstance(payload, bytes) else payload(offset)
            if len(blob) != size:
                raise WriterError(
                    f"export {self._names[row.name[0]].text!r} payload length changed with "
                    f"offset ({size} -> {len(blob)}); payload callables must be size-stable"
                )
            row.serial_offset = offset
            row.serial_size = size
            blobs.append(blob)
            offset += size
        return blobs

    def _validate(self) -> None:
        if not self._exports:
            raise WriterError("package has no exports")
        n_exports, n_imports = len(self._exports), len(self._imports)
        for i, row in enumerate(self._exports, start=1):
            for label, ref in (
                ("Class", row.class_ref),
                ("Super", row.super_ref),
                ("Outer", row.outer_ref),
                ("Archetype", row.archetype_ref),
            ):
                if ref > n_exports or -ref > n_imports:
                    raise WriterError(
                        f"export #{i} {label} reference {ref} is out of range "
                        f"({n_exports} exports, {n_imports} imports)"
                    )
            if row.outer_ref == i:
                raise WriterError(f"export #{i} is its own Outer")

    def build(self) -> bytes:
        """Return the finished package file contents."""
        self._validate()
        name_table = b"".join(row.to_bytes() for row in self._names)
        import_table = b"".join(row.to_bytes() for row in self._imports)
        export_entry_size = sum(row.entry_size for row in self._exports)
        depends_size = 4 * len(self._exports)
        net_object_count = sum(len(row.net_objects) for row in self._exports)

        # The summary's own length is fixed once the group string is fixed, but
        # it is measured rather than assumed.
        probe = self._summary_bytes(0, 0, 0, 0, 0, net_object_count)
        summary_size = len(probe)
        name_offset = summary_size
        import_offset = name_offset + len(name_table)
        export_offset = import_offset + len(import_table)
        depends_offset = export_offset + export_entry_size
        headers_size = depends_offset + depends_size

        blobs = self._resolve_payloads(headers_size)

        summary = self._summary_bytes(
            headers_size, name_offset, export_offset, import_offset,
            depends_offset, net_object_count,
        )
        if len(summary) != summary_size:
            raise WriterError("summary length is not stable")

        out = bytearray()
        out += summary
        out += name_table
        out += import_table
        for row in self._exports:
            out += row.to_bytes()
        # Depends table: ExportCount x TArray<int32>, all empty.  Verified against
        # all three templates (HeadersSize - DependsOffset == 4 * ExportCount, and
        # every count is zero).
        out += b"\0" * depends_size
        if len(out) != headers_size:
            raise WriterError(f"header assembled to {len(out)} bytes, expected {headers_size}")
        for blob in blobs:
            out += blob
        return bytes(out)

    def write(self, path: str | Path) -> Path:
        """Build the package and write it to ``path``."""
        dst = Path(path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(self.build())
        return dst


# ---------------------------------------------------------------------------
# FName / object-reference remapping
# ---------------------------------------------------------------------------


class _Remapper:
    """Rewrite FName indices and object references from ``src`` into ``builder``."""

    __slots__ = ("src", "builder", "objects", "_cache")

    def __init__(
        self, src: Package, builder: PackageBuilder, objects: dict[int, int]
    ) -> None:
        self.src = src
        self.builder = builder
        self.objects = objects
        self._cache: dict[tuple[int, int], FName] = {}

    # -- names -------------------------------------------------------------

    def fname(self, name: FName) -> FName:
        key = (name.index, name.number)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if not 0 <= name.index < len(self.src.names):
            raise WriterError(f"name index {name.index} out of range in source package")
        out = self.builder.fname(self.src.names[name.index], name.number)
        self._cache[key] = out
        return out

    def fname_bytes(self, raw: bytes, pos: int = 0) -> bytes:
        index, number = _FNAME.unpack_from(raw, pos)
        return self.fname(self.src.fname(index, number)).to_bytes()

    # -- object references --------------------------------------------------

    def objref(self, ref: int) -> int:
        """Map a source object reference; anything unmapped becomes ``None`` (0)."""
        if ref == 0:
            return 0
        return self.objects.get(ref, 0)

    # -- tagged property lists ---------------------------------------------

    def tags(self, tags: Iterable[PropertyTag]) -> list[PropertyTag]:
        return [self._tag(tag) for tag in tags]

    def _tag(self, tag: PropertyTag) -> PropertyTag:
        new = PropertyTag(
            name=self.fname(tag.name),
            type=self.fname(tag.type),
            array_index=tag.array_index,
            raw=tag.raw,
            struct_name=self.fname(tag.struct_name) if tag.struct_name else None,
            bool_value=tag.bool_value,
            enum_name=self.fname(tag.enum_name) if tag.enum_name else None,
            offset=tag.offset,
        )
        new.raw = self._body(tag)
        new.element_count = tag.element_count
        new.value = tag.value
        return new

    def _body(self, tag: PropertyTag) -> bytes:
        kind = tag.type.text
        raw = tag.raw
        if kind == "NameProperty" and len(raw) >= 8:
            return self.fname_bytes(raw) + raw[8:]
        if kind == "ByteProperty":
            if tag.enum_name is not None and tag.enum_name.text != _NONE and len(raw) >= 8:
                return self.fname_bytes(raw) + raw[8:]
            return raw
        if kind in _OBJECT_TYPES and len(raw) == 4:
            return _I32.pack(self.objref(_I32.unpack_from(raw, 0)[0]))
        if kind == "StructProperty":
            return self._struct_body(tag.struct_name, raw)
        if kind == "ArrayProperty":
            return self._array_body(tag.name.text, raw)
        return raw

    def _struct_body(self, struct_name: FName | None, raw: bytes) -> bytes:
        name = struct_name.text if struct_name else ""
        if name in _NATIVE_STRUCTS or len(raw) < 16:
            return raw
        nested = self._try_tagged_lists(raw, 0, 1)
        return nested if nested is not None else raw

    def _array_body(self, prop_name: str, raw: bytes) -> bytes:
        if len(raw) < 4:
            return raw
        (count,) = _I32.unpack_from(raw, 0)
        if count <= 0:
            return raw
        body = len(raw) - 4
        if body == count * 4:
            if prop_name in _OBJECT_REF_ARRAYS:
                refs = struct.unpack_from(f"<{count}i", raw, 4)
                return raw[:4] + struct.pack(
                    f"<{count}i", *(self.objref(r) for r in refs)
                )
            return raw
        nested = self._try_tagged_lists(raw, 4, count)
        return raw[:4] + nested if nested is not None else raw

    def _try_tagged_lists(self, raw: bytes, pos: int, count: int) -> bytes | None:
        """Re-emit ``count`` consecutive tagged lists, or ``None`` if they do not
        parse and consume ``raw[pos:]`` exactly."""
        out = bytearray()
        try:
            for _ in range(count):
                tags, pos = parse_properties(raw, pos, self.src)
                out += serialize_properties(
                    self.tags(tags), self.src, self.builder.none_fname
                )
        except (ValueError, struct.error, IndexError):
            return None
        if pos != len(raw):
            return None
        return bytes(out)


def _remap_name_index_map(tail: bytes, remap: _Remapper) -> bytes:
    """Rewrite the ``NameIndexMap`` FNames at the head of a SkeletalMesh tail.

    Layout (``docs/BL2_UPK_CODEC_NOTES.md`` §4): int32 count, then count x
    (FName 8 B, int32 value); everything after that is bone-count/UV-count
    bookkeeping with no names in it and is copied verbatim.
    """
    if len(tail) < 4:
        return tail
    (count,) = _I32.unpack_from(tail, 0)
    if count < 0 or 4 + count * 12 > len(tail):
        raise WriterError(f"SkeletalMesh tail has an implausible NameIndexMap count {count}")
    out = bytearray(tail[:4])
    pos = 4
    for _ in range(count):
        out += remap.fname_bytes(tail, pos)
        out += tail[pos + 8 : pos + 12]
        pos += 12
    out += tail[pos:]
    return bytes(out)


# ---------------------------------------------------------------------------
# high-level clone
# ---------------------------------------------------------------------------


def qualified_path(package_name: str, export_path: str) -> str:
    """Game-visible object path of an export.

    Our :class:`Package` reader builds paths from the export table alone, so a
    top-level export (Outer 0) has no package prefix.  UE3 treats the file's own
    package as the outermost object, so the runtime path -- the one
    ``unrealsdk.find_object`` wants after ``load_package`` -- is this.
    """
    return f"{package_name}.{export_path}" if export_path else package_name


@dataclass(slots=True)
class CloneResult:
    """What :func:`clone_skeletal_mesh` produced."""

    out_path: Path
    package_name: str
    mesh_name: str
    mesh_ref: int
    socket_refs: list[int] = field(default_factory=list)
    socket_names: list[str] = field(default_factory=list)

    @property
    def mesh_path(self) -> str:
        return qualified_path(self.package_name, self.mesh_name)

    def socket_path(self, name: str) -> str:
        return f"{self.mesh_path}.{name}"


def _socket_export_name(tags: list[PropertyTag], fallback: str) -> str:
    """Name a cloned socket export after its ``SocketName`` property."""
    tag = find_property(tags, "SocketName")
    if tag is not None and isinstance(tag.value, FName) and tag.value.text:
        return tag.value.text
    return fallback


def clone_skeletal_mesh(
    src_pkg: Package,
    src_export_path: str,
    out_path: str | Path,
    new_package_name: str,
    new_mesh_name: str,
    mutate: Callable[[SkeletalMeshExport], None] | None = None,
    *,
    template: Package | None = None,
    guid: bytes | None = None,
    package_source: int | None = None,
) -> CloneResult:
    """Copy one SkeletalMesh (and its sockets) into a brand-new package file.

    Everything that points anywhere is rewritten: FName indices in property tags
    (name, type, struct name, enum name and Name-typed values, recursively
    through struct and struct-array bodies), bone names in ``RefSkeleton``, the
    ``NameIndexMap`` FNames in the mesh tail, socket ``SocketName``/``BoneName``,
    the ``Sockets`` array (to the new socket export indices), ``Materials``
    (to ``None`` -- the source materials live in packages we do not import), and
    each export row's Class/Outer/Archetype.

    ``mutate`` is applied to the parsed mesh just before serialization.
    """
    mesh_entry = src_pkg.find_export(src_export_path, "SkeletalMesh")
    socket_entries = [
        e
        for e in src_pkg.exports
        if e.outer_index == mesh_entry.index and e.clsname == "SkeletalMeshSocket"
    ]

    builder = PackageBuilder(
        package_name=new_package_name,
        template=template if template is not None else src_pkg,
        package_flags=PACKAGE_FLAGS_COOKED_CONTENT,
        guid=guid,
        package_source=package_source,
    )
    mesh_class = builder.import_class("Engine", "SkeletalMesh")
    socket_class = builder.import_class("Engine", "SkeletalMeshSocket")
    if mesh_entry.archetype_index:
        raise WriterError(
            f"{src_export_path} carries Archetype {mesh_entry.archetype_index}; "
            "replicating class-default archetypes is not implemented "
            "(no BL2 SkeletalMesh export in the corpus has one)"
        )

    # Export rows have to exist before the payloads can reference them, so the
    # mesh and the sockets are added with placeholder payloads and filled in
    # afterwards via the returned refs.
    mesh_payload_slot: list[PayloadSource] = [b""]
    mesh_ref = builder.add_export(
        name=new_mesh_name,
        class_ref=mesh_class,
        outer=0,
        payload=lambda offset: mesh_payload_slot[0](offset),  # type: ignore[operator]
        object_flags=OBJECT_FLAGS_TOP_LEVEL,
        archetype=0,
    )

    socket_payload_slots: list[list[bytes]] = []
    socket_refs: list[int] = []
    socket_names: list[str] = []
    seen_socket_names: dict[str, int] = {}
    parsed_sockets: list[tuple[int, list[PropertyTag]]] = []
    for entry in socket_entries:
        raw = src_pkg.read_export_bytes(entry)
        (net_index,) = _I32.unpack_from(raw, 0)
        tags, end = parse_properties(raw, 4, src_pkg)
        if end != len(raw):
            raise WriterError(
                f"socket {entry.path!r} has {len(raw) - end} trailing bytes after its "
                "property list; SkeletalMeshSocket is supposed to be properties only"
            )
        parsed_sockets.append((net_index, tags))
        base = _socket_export_name(tags, entry.name)
        # Two sockets with the same SocketName would collide into one object
        # path; UE3's own answer is the FName instance number (``Name_1`` is
        # stored as number 2), so use that rather than mangling the text.
        number = seen_socket_names.get(base, 0)
        seen_socket_names[base] = number + 1
        name_number = 0 if number == 0 else number + 1
        socket_names.append(base if name_number == 0 else f"{base}_{number}")
        slot: list[bytes] = [b""]
        socket_payload_slots.append(slot)
        socket_refs.append(
            builder.add_export(
                name=base,
                class_ref=socket_class,
                outer=mesh_ref,
                payload=lambda _offset, slot=slot: slot[0],
                object_flags=OBJECT_FLAGS_SUB_OBJECT,
                archetype=0,
                name_number=name_number,
            )
        )

    objects: dict[int, int] = {mesh_entry.index: mesh_ref}
    for entry, ref in zip(socket_entries, socket_refs):
        objects[entry.index] = ref
    remap = _Remapper(src_pkg, builder, objects)

    # -- sockets -----------------------------------------------------------
    for (net_index, tags), slot in zip(parsed_sockets, socket_payload_slots):
        body = serialize_properties(remap.tags(tags), src_pkg, builder.none_fname)
        slot[0] = _I32.pack(net_index) + body

    # -- mesh ---------------------------------------------------------------
    mesh = SkeletalMeshExport.parse(src_pkg.read_export_bytes(mesh_entry), src_pkg)
    if not mesh.has_native_data:
        raise WriterError(f"{src_export_path} has no native SkeletalMesh data to clone")
    mesh.properties = remap.tags(mesh.properties)
    mesh.property_terminator = builder.none_fname
    mesh.materials = [remap.objref(ref) for ref in mesh.materials]
    for bone in mesh.bones:
        bone.name = remap.fname(bone.name)
    mesh.tail = _remap_name_index_map(mesh.tail, remap)
    if mutate is not None:
        mutate(mesh)
    # ``mesh.package`` still points at the source package.  That is deliberate
    # and harmless: ``serialize()`` only uses it to look up a ``None`` name for
    # the property terminator, and we pass the new package's ``None`` explicitly.
    mesh_payload_slot[0] = mesh.serialize

    out = builder.write(out_path)
    return CloneResult(
        out_path=out,
        package_name=new_package_name,
        mesh_name=new_mesh_name,
        mesh_ref=mesh_ref,
        socket_refs=socket_refs,
        socket_names=socket_names,
    )
