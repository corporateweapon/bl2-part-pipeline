"""``Texture2D`` exports: parse, serialize, and build new ones (M7 task 4).

The on-disk layout was read off Startup.upk's 565 ``Texture2D`` exports (every one
re-parses and re-serializes byte for byte through :func:`parse_texture2d` /
:func:`serialize_texture2d`):

.. code-block:: text

    int32   NetIndex
    tagged property list        SizeX, SizeY, OriginalSizeX/Y, Format (EPixelFormat),
                                [TextureFileCacheName], MipTailBaseIdx, [NeverStream],
                                LODGroup, SourceFilePath, ..., LightingGuid, None
    byte[16]  HASH_A            Borderlands-specific (ArLicenseeVer 46); a constant
    int32   mip count
    per mip:
        FByteBulkData header    int32 flags, int32 element count, int32 size on disk,
                                int32 offset in file (ABSOLUTE, even for inline data)
        byte[size on disk]      the mip's pixels, only when flags has no
                                BULKDATA_StoreInSeparateFile (0x01) -- inline
        int32 SizeX, int32 SizeY   (DXT levels below 4x4 are stored as 4x4)
    byte[16]  HASH_B            another constant (differs from HASH_A in one byte)
    byte[16]  TextureFileCacheGuid   unique per texture
    int32   0                   (CachedPVRTC / flash mips: none)

Streamed textures (``TextureFileCacheName`` set, flags 0x11) keep their pixels in a
``.tfc``; the textures this module *writes* are inline and ``NeverStream``, so the
game needs no TFC and no cooker.  Pixels come from Pillow: it writes DXT1/DXT5 DDS
files, and a DDS is a 128-byte header in front of exactly the block data UE3 wants.
"""

from __future__ import annotations

import io
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .props import PropertyTag, find_property, parse_properties, serialize_properties
from .reader import ExportEntry, Package
from .writer import OBJECT_FLAGS_TOP_LEVEL, PackageBuilder, WriterError

__all__ = [
    "BULKDATA_STORE_IN_SEPARATE_FILE",
    "HASH_A",
    "HASH_B",
    "MipLevel",
    "Texture2DExport",
    "TextureSpec",
    "build_texture_package",
    "dxt_mip_chain",
    "parse_texture2d",
    "serialize_texture2d",
    "texture2d_payload",
]

_I32 = struct.Struct("<i")

#: the constant 16 bytes before the mip array (559 of Startup's 565 textures; the
#: other six differ in one byte, ``9e`` -> ``9f``)
HASH_A = bytes.fromhex("6d37b5801b4f3d349d543a999e799abd")
#: the constant 16 bytes after it (all 565)
HASH_B = bytes.fromhex("6d37b5801b4f3d349d543a999d799abd")

BULKDATA_STORE_IN_SEPARATE_FILE = 0x01

#: EPixelFormat member -> bytes per 4x4 block (DXT) or per pixel (uncompressed)
_FORMATS = {"PF_DXT1": ("block", 8), "PF_DXT5": ("block", 16), "PF_A8R8G8B8": ("pixel", 4)}
#: what Pillow calls them
_PILLOW_DDS = {"PF_DXT1": "DXT1", "PF_DXT5": "DXT5"}


@dataclass(slots=True)
class MipLevel:
    flags: int
    element_count: int
    size_on_disk: int
    offset: int
    data: bytes | None
    size_x: int
    size_y: int

    @property
    def inline(self) -> bool:
        return not (self.flags & BULKDATA_STORE_IN_SEPARATE_FILE)


@dataclass(slots=True)
class Texture2DExport:
    net_index: int
    tags: list[PropertyTag]
    hash_a: bytes
    mips: list[MipLevel]
    hash_b: bytes
    tfc_guid: bytes
    trailer: int
    #: absolute file offset of the first byte of the export (for offset checks)
    base_offset: int = 0

    @property
    def size(self) -> tuple[int, int]:
        sx = find_property(self.tags, "SizeX")
        sy = find_property(self.tags, "SizeY")
        return (int(sx.value) if sx else 0, int(sy.value) if sy else 0)

    @property
    def format(self) -> str:
        tag = find_property(self.tags, "Format")
        return str(tag.value) if tag is not None and tag.value is not None else ""


def parse_texture2d(package: Package, export: ExportEntry) -> Texture2DExport:
    data = package.read_export_bytes(export)
    (net_index,) = _I32.unpack_from(data, 0)
    tags, pos = parse_properties(data, 4, package)
    hash_a = bytes(data[pos:pos + 16])
    pos += 16
    (count,) = _I32.unpack_from(data, pos)
    pos += 4
    mips: list[MipLevel] = []
    for _ in range(count):
        flags, element_count, size_on_disk, offset = struct.unpack_from("<4i", data, pos)
        pos += 16
        blob: bytes | None = None
        if not (flags & BULKDATA_STORE_IN_SEPARATE_FILE) and size_on_disk > 0:
            if offset != export.off + pos:
                raise ValueError(
                    f"{export.path}: inline mip offset {offset} != file position {export.off + pos}"
                )
            blob = bytes(data[pos:pos + size_on_disk])
            pos += size_on_disk
        size_x, size_y = struct.unpack_from("<2i", data, pos)
        pos += 8
        mips.append(MipLevel(flags, element_count, size_on_disk, offset, blob, size_x, size_y))
    hash_b = bytes(data[pos:pos + 16])
    pos += 16
    tfc_guid = bytes(data[pos:pos + 16])
    pos += 16
    (trailer,) = _I32.unpack_from(data, pos)
    pos += 4
    if pos != len(data):
        raise ValueError(f"{export.path}: {len(data) - pos} unexplained trailing bytes")
    return Texture2DExport(net_index, tags, hash_a, mips, hash_b, tfc_guid, trailer, export.off)


def serialize_texture2d(tex: Texture2DExport, package: Package, base_offset: int) -> bytes:
    """The export bytes as they would sit at ``base_offset`` in the file."""
    out = bytearray(_I32.pack(tex.net_index))
    out += serialize_properties(tex.tags, package)
    out += tex.hash_a
    out += _I32.pack(len(tex.mips))
    for mip in tex.mips:
        offset = mip.offset
        if mip.inline and mip.data is not None:
            offset = base_offset + len(out) + 16
        out += struct.pack("<4i", mip.flags, mip.element_count, mip.size_on_disk, offset)
        if mip.inline and mip.data is not None:
            out += mip.data
        out += struct.pack("<2i", mip.size_x, mip.size_y)
    out += tex.hash_b
    out += tex.tfc_guid
    out += _I32.pack(tex.trailer)
    return bytes(out)


# ---------------------------------------------------------------------------
# building new textures
# ---------------------------------------------------------------------------

def _dds_blocks(image, fmt: str) -> bytes:
    """Raw block data for one level: Pillow's DDS minus its 128-byte header."""
    buf = io.BytesIO()
    image.save(buf, "DDS", pixel_format=_PILLOW_DDS[fmt])
    raw = buf.getvalue()
    if raw[:4] != b"DDS " or len(raw) < 128:
        raise WriterError("Pillow did not produce a DDS file")
    return raw[128:]


def dxt_mip_chain(image, fmt: str) -> list[tuple[int, int, bytes]]:
    """``[(size_x, size_y, blocks), ...]`` from full size down to the 4x4 tail.

    UE3 stores the 2x2 and 1x1 DXT levels as 4x4 (one block each), and so does this:
    the chain for a 256-square is 256, 128, ..., 8, 4, 4, 4 -- nine levels, exactly
    what Startup's ``Logo_MoxxiSwirl`` has.  ``PF_A8R8G8B8`` goes all the way to 1x1.
    """
    from PIL import Image

    kind, _ = _FORMATS[fmt]
    width, height = image.size
    if width & (width - 1) or height & (height - 1):
        raise WriterError(f"texture must be power-of-two sized, got {width}x{height}")
    levels: list[tuple[int, int, bytes]] = []
    level = 0
    while True:
        w, h = max(1, width >> level), max(1, height >> level)
        resized = image if level == 0 else image.resize((w, h), Image.LANCZOS)
        if kind == "block":
            sx, sy = max(4, w), max(4, h)
            if (sx, sy) != (w, h):
                resized = resized.resize((sx, sy), Image.LANCZOS)
            levels.append((sx, sy, _dds_blocks(resized, fmt)))
        else:
            # PF_A8R8G8B8 is BGRA in memory
            rgba = resized.convert("RGBA").tobytes()
            bgra = bytearray(len(rgba))
            bgra[0::4] = rgba[2::4]
            bgra[1::4] = rgba[1::4]
            bgra[2::4] = rgba[0::4]
            bgra[3::4] = rgba[3::4]
            levels.append((w, h, bytes(bgra)))
        if w == 1 and h == 1:
            break
        level += 1
    return levels


@dataclass
class TextureSpec:
    """One texture to write: a Pillow image, a format and its texture settings."""

    name: str
    image: object  # PIL.Image.Image
    format: str = "PF_DXT1"
    lod_group: str = "TEXTUREGROUP_Weapon"
    srgb: bool = True
    #: ``TC_Normalmap`` for a normal map (with ``srgb=False``); ``None`` = default
    compression_settings: str | None = None
    address_x: str | None = None
    address_y: str | None = None
    source_file_path: str = ""
    tfc_guid: bytes = field(default_factory=lambda: os.urandom(16))
    lighting_guid: bytes = field(default_factory=lambda: os.urandom(16))
    net_index: int = -1

    def __post_init__(self) -> None:
        if self.format not in _FORMATS:
            raise WriterError(f"unsupported texture format {self.format!r}; "
                              f"one of {', '.join(_FORMATS)}")
        if len(self.tfc_guid) != 16 or len(self.lighting_guid) != 16:
            raise WriterError("guids must be 16 bytes")


def _tag(builder: PackageBuilder, name: str, ptype: str, raw: bytes, **kw) -> PropertyTag:
    return PropertyTag(name=builder.fname(name), type=builder.fname(ptype),
                       array_index=0, raw=raw, **kw)


def texture2d_payload(
    builder: PackageBuilder, spec: TextureSpec
) -> tuple[Callable[[int], bytes], list[tuple[int, int, int]]]:
    """A size-stable payload callable for one ``Texture2D`` export, plus the mip table.

    The mip data is encoded once, here; the callable only re-stamps the absolute
    offsets, which is what the builder needs (it calls it twice).
    """
    levels = dxt_mip_chain(spec.image, spec.format)
    width, height = spec.image.size
    fmt_enum = builder.fname("EPixelFormat")
    tags = [
        _tag(builder, "SizeX", "IntProperty", _I32.pack(width)),
        _tag(builder, "SizeY", "IntProperty", _I32.pack(height)),
        _tag(builder, "OriginalSizeX", "IntProperty", _I32.pack(width)),
        _tag(builder, "OriginalSizeY", "IntProperty", _I32.pack(height)),
        _tag(builder, "Format", "ByteProperty", builder.fname(spec.format).to_bytes(),
             enum_name=fmt_enum),
    ]
    if spec.address_x:
        tags.append(_tag(builder, "AddressX", "ByteProperty",
                         builder.fname(spec.address_x).to_bytes(),
                         enum_name=builder.fname("TextureAddress")))
    if spec.address_y:
        tags.append(_tag(builder, "AddressY", "ByteProperty",
                         builder.fname(spec.address_y).to_bytes(),
                         enum_name=builder.fname("TextureAddress")))
    tags.append(_tag(builder, "MipTailBaseIdx", "IntProperty", _I32.pack(len(levels) - 1)))
    tags.append(_tag(builder, "NeverStream", "BoolProperty", b"", bool_value=1))
    if not spec.srgb:
        tags.append(_tag(builder, "SRGB", "BoolProperty", b"", bool_value=0))
    if spec.compression_settings:
        tags.append(_tag(builder, "CompressionSettings", "ByteProperty",
                         builder.fname(spec.compression_settings).to_bytes(),
                         enum_name=builder.fname("TextureCompressionSettings")))
    tags.append(_tag(builder, "LODGroup", "ByteProperty",
                     builder.fname(spec.lod_group).to_bytes(),
                     enum_name=builder.fname("TextureGroup")))
    if spec.source_file_path:
        text = spec.source_file_path.encode("latin-1") + b"\0"
        tags.append(_tag(builder, "SourceFilePath", "StrProperty",
                         _I32.pack(len(text)) + text))
    tags.append(_tag(builder, "LightingGuid", "StructProperty", spec.lighting_guid,
                     struct_name=builder.fname("Guid")))
    head = _I32.pack(spec.net_index)
    # serialize_properties wants a Package for the terminator; hand it the builder's
    # own None FName instead
    props = b"".join(t.to_bytes() for t in tags) + builder.none_fname.to_bytes()
    prefix = head + props + HASH_A + _I32.pack(len(levels))
    suffix = HASH_B + spec.tfc_guid + _I32.pack(0)

    def payload(offset: int) -> bytes:
        out = bytearray(prefix)
        for sx, sy, blob in levels:
            data_offset = offset + len(out) + 16
            out += struct.pack("<4i", 0, len(blob), len(blob), data_offset)
            out += blob
            out += struct.pack("<2i", sx, sy)
        out += suffix
        return bytes(out)

    return payload, [(sx, sy, len(blob)) for sx, sy, blob in levels]


def build_texture_package(
    out_path: str | Path,
    package_name: str,
    textures: Sequence[TextureSpec],
    *,
    guid: bytes | None = None,
    package_source: int | None = None,
) -> dict:
    """Write a loose, Texture2D-only package and return a sidecar dict describing it."""
    if not textures:
        raise WriterError("no textures to write")
    builder = PackageBuilder(package_name, guid=guid, package_source=package_source)
    texture_class = builder.import_class("Engine", "Texture2D")
    rows = []
    for spec in textures:
        payload, mip_table = texture2d_payload(builder, spec)
        builder.add_export(spec.name, texture_class, 0, payload, OBJECT_FLAGS_TOP_LEVEL)
        rows.append({
            "name": spec.name,
            "path": f"{package_name}.{spec.name}",
            "format": spec.format,
            "size": list(spec.image.size),
            "mips": [{"size_x": sx, "size_y": sy, "bytes": n} for sx, sy, n in mip_table],
            "srgb": spec.srgb,
            "lod_group": spec.lod_group,
            "compression_settings": spec.compression_settings,
        })
    out = Path(out_path)
    builder.write(out)
    return {"package": package_name, "out": str(out), "textures": rows}
