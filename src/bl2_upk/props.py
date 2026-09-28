"""UE3 v832 tagged-property list parser / serializer.

The parser is deliberately lossless: every tag keeps the exact value bytes it
was built from, so :func:`serialize_properties` reproduces the input byte for
byte.  Decoding is a convenience layer on top of those bytes and is only
attempted for the property types listed in ``UPK_SKELMESH_LAYOUT.md`` §3.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from .reader import FName, Package

__all__ = [
    "PropertyTag",
    "parse_properties",
    "serialize_properties",
    "find_property",
]

_I32 = struct.Struct("<i")
_F32 = struct.Struct("<f")
_VEC = struct.Struct("<fff")
_ROT = struct.Struct("<iii")

_NONE = "None"


@dataclass(slots=True)
class PropertyTag:
    """One FPropertyTag plus its raw (and, where possible, decoded) value."""

    name: FName
    type: FName
    array_index: int
    raw: bytes
    struct_name: FName | None = None
    bool_value: int | None = None
    enum_name: FName | None = None
    value: Any = None
    """Decoded value, or ``None`` when the type is kept raw."""
    element_count: int | None = None
    """Element count for ArrayProperty."""
    offset: int = -1
    """Offset of the tag inside the payload it was parsed from."""

    @property
    def size(self) -> int:
        """DataSize as it will be written (always ``len(raw)``)."""
        return len(self.raw)

    @property
    def type_name(self) -> str:
        return self.type.text

    def to_bytes(self) -> bytes:
        parts = [
            self.name.to_bytes(),
            self.type.to_bytes(),
            _I32.pack(len(self.raw)),
            _I32.pack(self.array_index),
        ]
        kind = self.type.text
        if kind == "StructProperty":
            if self.struct_name is None:
                raise ValueError(f"StructProperty {self.name.text!r} has no struct name")
            parts.append(self.struct_name.to_bytes())
        elif kind == "BoolProperty":
            if self.bool_value is None:
                raise ValueError(f"BoolProperty {self.name.text!r} has no bool value")
            parts.append(bytes((self.bool_value,)))
        elif kind == "ByteProperty":
            if self.enum_name is None:
                raise ValueError(f"ByteProperty {self.name.text!r} has no enum name")
            parts.append(self.enum_name.to_bytes())
        parts.append(self.raw)
        return b"".join(parts)


def _decode_fstring(data: bytes, pos: int) -> str:
    (n,) = _I32.unpack_from(data, pos)
    pos += 4
    if n == 0:
        return ""
    if n < 0:
        return data[pos : pos - 2 * n].decode("utf-16-le", "replace").rstrip("\0")
    return data[pos : pos + n].decode("latin-1", "replace").rstrip("\0")


def _decode_array(raw: bytes, package: Package) -> tuple[int | None, Any]:
    """Decode an ArrayProperty body; POD 4-byte elements become an int list."""
    if len(raw) < 4:
        return None, None
    (count,) = _I32.unpack_from(raw, 0)
    body = len(raw) - 4
    if count <= 0:
        return count, []
    if body == count * 4:
        # Object references, int32s or floats.  ``Sockets`` is the object-ref
        # case; callers that know the element type reinterpret from here.
        return count, list(struct.unpack_from(f"<{count}i", raw, 4))
    return count, None


def _decode_value(tag: PropertyTag, package: Package) -> None:
    kind = tag.type.text
    raw = tag.raw
    try:
        if kind == "IntProperty":
            tag.value = _I32.unpack_from(raw, 0)[0]
        elif kind == "FloatProperty":
            tag.value = _F32.unpack_from(raw, 0)[0]
        elif kind == "BoolProperty":
            tag.value = bool(tag.bool_value)
        elif kind == "NameProperty":
            idx, num = struct.unpack_from("<ii", raw, 0)
            tag.value = package.fname(idx, num)
        elif kind in ("ObjectProperty", "InterfaceProperty", "ComponentProperty",
                      "ClassProperty"):
            tag.value = _I32.unpack_from(raw, 0)[0]
        elif kind == "StrProperty":
            tag.value = _decode_fstring(raw, 0)
        elif kind == "ByteProperty":
            if tag.enum_name is not None and tag.enum_name.text != _NONE:
                idx, num = struct.unpack_from("<ii", raw, 0)
                tag.value = package.fname(idx, num)
            elif raw:
                tag.value = raw[0]
        elif kind == "StructProperty":
            sname = tag.struct_name.text if tag.struct_name else ""
            if sname == "Vector" and len(raw) >= 12:
                tag.value = _VEC.unpack_from(raw, 0)
            elif sname == "Rotator" and len(raw) >= 12:
                tag.value = _ROT.unpack_from(raw, 0)
        elif kind == "ArrayProperty":
            tag.element_count, tag.value = _decode_array(raw, package)
    except struct.error:  # pragma: no cover - malformed value bytes
        tag.value = None


def parse_properties(
    data: bytes, pos: int, package: Package
) -> tuple[list[PropertyTag], int]:
    """Parse a tagged property list; returns the tags and the position after it.

    The returned position is just past the terminating ``None`` FName.
    """
    tags: list[PropertyTag] = []
    while True:
        start = pos
        name, pos = package.read_fname(data, pos)
        if name.text == _NONE:
            return tags, pos
        ptype, pos = package.read_fname(data, pos)
        size, array_index = struct.unpack_from("<ii", data, pos)
        pos += 8
        struct_name: FName | None = None
        bool_value: int | None = None
        enum_name: FName | None = None
        kind = ptype.text
        if kind == "StructProperty":
            struct_name, pos = package.read_fname(data, pos)
        elif kind == "BoolProperty":
            bool_value = data[pos]
            pos += 1
        elif kind == "ByteProperty":
            enum_name, pos = package.read_fname(data, pos)
        if size < 0 or pos + size > len(data):
            raise ValueError(
                f"property {name.text!r} at {start} has bad DataSize {size}"
            )
        tag = PropertyTag(
            name=name,
            type=ptype,
            array_index=array_index,
            raw=bytes(data[pos : pos + size]),
            struct_name=struct_name,
            bool_value=bool_value,
            enum_name=enum_name,
            offset=start,
        )
        _decode_value(tag, package)
        tags.append(tag)
        pos += size


def serialize_properties(
    tags: list[PropertyTag], package: Package, terminator: FName | None = None
) -> bytes:
    """Serialize a tag list, terminated by the ``None`` FName."""
    out = bytearray()
    for tag in tags:
        out += tag.to_bytes()
    end = terminator if terminator is not None else package.fname(package.find_name(_NONE), 0)
    out += end.to_bytes()
    return bytes(out)


def find_property(tags: list[PropertyTag], name: str) -> PropertyTag | None:
    """Return the first tag called ``name``, or ``None``."""
    for tag in tags:
        if tag.name.text == name:
            return tag
    return None
