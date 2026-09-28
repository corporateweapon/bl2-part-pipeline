"""USkeletalMesh codec for UE3 ArVer 832 / LicenseeVer 46 (Borderlands 2, PC).

Layout follows ``docs/UPK_SKELMESH_LAYOUT.md`` §§2-6, verified byte for byte
against ``Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh``.

Everything after ``LODModels`` is kept verbatim in :attr:`SkeletalMeshExport.tail`
(see ``docs/BL2_UPK_CODEC_NOTES.md`` for what it contains).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .props import PropertyTag, find_property, parse_properties, serialize_properties
from .reader import FName, Package

__all__ = [
    "half_to_float",
    "float_to_half",
    "MeshBone",
    "Section",
    "Chunk",
    "GpuVertex",
    "BulkData",
    "LodModel",
    "SkeletalMeshExport",
    "SkelMeshError",
]

_I32 = struct.Struct("<i")
_U32 = struct.Struct("<I")
_F32 = struct.Struct("<f")
_VEC = struct.Struct("<fff")
_QUAT = struct.Struct("<ffff")
_SECTION = struct.Struct("<hhii")
_BONE = struct.Struct("<iiI4f3fiiI")
_VERT_HEAD = struct.Struct("<II4B4B3f")

#: Bulk-data flags that mean "payload is not stored inline right here".
BULKDATA_STORE_IN_SEPARATE_FILE = 0x01
BULKDATA_UNUSED = 0x20
BULKDATA_SEPARATE_STORAGE = 0x40
_BULK_NOT_INLINE = (
    BULKDATA_STORE_IN_SEPARATE_FILE | BULKDATA_UNUSED | BULKDATA_SEPARATE_STORAGE
)


class SkelMeshError(Exception):
    """Raised when the byte stream does not match the expected layout."""


# ---------------------------------------------------------------------------
# half <-> float
# ---------------------------------------------------------------------------


def half_to_float(value: int) -> float:
    """Convert an IEEE-754 binary16 bit pattern to a Python float."""
    return struct.unpack("<e", struct.pack("<H", value & 0xFFFF))[0]


def float_to_half(value: float) -> int:
    """Convert a float to an IEEE-754 binary16 bit pattern."""
    return struct.unpack("<H", struct.pack("<e", value))[0]


# ---------------------------------------------------------------------------
# small records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MeshBone:
    """FMeshBone (52 bytes at v832)."""

    name: FName
    flags: int
    orientation: tuple[float, float, float, float]
    position: tuple[float, float, float]
    num_children: int
    parent_index: int
    color: int = 0xFFFFFFFF

    def to_bytes(self) -> bytes:
        return _BONE.pack(
            self.name.index,
            self.name.number,
            self.flags,
            *self.orientation,
            *self.position,
            self.num_children,
            self.parent_index,
            self.color,
        )


@dataclass(slots=True)
class Section:
    """FSkelMeshSection (13 bytes)."""

    material_index: int
    chunk_index: int
    first_index: int
    num_triangles: int
    triangle_sorting: int

    def to_bytes(self) -> bytes:
        return _SECTION.pack(
            self.material_index, self.chunk_index, self.first_index, self.num_triangles
        ) + bytes((self.triangle_sorting,))


@dataclass(slots=True)
class Chunk:
    """FSkelMeshChunk.  Rigid/soft CPU vertex arrays are empty in cooked data."""

    base_vertex_index: int
    bone_map: list[int]
    num_rigid_vertices: int
    num_soft_vertices: int
    max_bone_influences: int
    rigid_vertices: bytes = b""
    soft_vertices: bytes = b""
    rigid_vertex_count: int = 0
    soft_vertex_count: int = 0

    def to_bytes(self) -> bytes:
        out = bytearray()
        out += _I32.pack(self.base_vertex_index)
        out += _I32.pack(self.rigid_vertex_count)
        out += self.rigid_vertices
        out += _I32.pack(self.soft_vertex_count)
        out += self.soft_vertices
        out += _I32.pack(len(self.bone_map))
        out += struct.pack(f"<{len(self.bone_map)}h", *self.bone_map)
        out += _I32.pack(self.num_rigid_vertices)
        out += _I32.pack(self.num_soft_vertices)
        out += _I32.pack(self.max_bone_influences)
        return bytes(out)


@dataclass(slots=True)
class GpuVertex:
    """One FGPUSkinVertex.  UVs stay as raw half pairs; positions are floats."""

    tangent_x: int
    tangent_z: int
    bone_idx: list[int]
    bone_wt: list[int]
    x: float
    y: float
    z: float
    uvs: list[tuple[int, int]]
    """One (u, v) pair per texture coordinate set, raw uint16 (half) values."""

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def influence_count(self) -> int:
        return sum(1 for w in self.bone_wt if w)

    def uv_floats(self, index: int) -> tuple[float, float]:
        u, v = self.uvs[index]
        return half_to_float(u), half_to_float(v)

    def set_uv_floats(self, index: int, u: float, v: float) -> None:
        self.uvs[index] = (float_to_half(u), float_to_half(v))

    def copy(self) -> "GpuVertex":
        return GpuVertex(
            self.tangent_x,
            self.tangent_z,
            list(self.bone_idx),
            list(self.bone_wt),
            self.x,
            self.y,
            self.z,
            list(self.uvs),
        )

    def to_bytes(self) -> bytes:
        head = _VERT_HEAD.pack(
            self.tangent_x, self.tangent_z, *self.bone_idx, *self.bone_wt,
            self.x, self.y, self.z,
        )
        flat: list[int] = []
        for pair in self.uvs:
            flat.extend(pair)
        return head + struct.pack(f"<{len(flat)}H", *flat)


@dataclass(slots=True)
class BulkData:
    """FUntypedBulkData header plus its inline payload."""

    flags: int
    element_count: int
    size_on_disk: int
    offset_in_file: int
    payload: bytes = b""
    element_size: int = 4

    @property
    def is_inline(self) -> bool:
        """True when the payload follows the 16-byte header in this stream."""
        return not (self.flags & _BULK_NOT_INLINE)

    def to_bytes(self, absolute_offset: int) -> bytes:
        """Serialize; ``absolute_offset`` is where the payload will land."""
        size_on_disk = len(self.payload) if self.is_inline else self.size_on_disk
        if self.is_inline:
            self.element_count = len(self.payload) // self.element_size
            self.size_on_disk = size_on_disk
        self.offset_in_file = absolute_offset
        header = struct.pack(
            "<Iiii", self.flags, self.element_count, size_on_disk, absolute_offset
        )
        return header + (self.payload if self.is_inline else b"")


# ---------------------------------------------------------------------------
# reader helper
# ---------------------------------------------------------------------------


class _Cursor:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def i32(self) -> int:
        v = _I32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def u32(self) -> int:
        v = _U32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def f32(self) -> float:
        v = _F32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def vec(self) -> tuple[float, float, float]:
        v = _VEC.unpack_from(self.data, self.pos)
        self.pos += 12
        return v

    def quat(self) -> tuple[float, float, float, float]:
        v = _QUAT.unpack_from(self.data, self.pos)
        self.pos += 16
        return v

    def i16_array(self, count: int) -> list[int]:
        v = list(struct.unpack_from(f"<{count}h", self.data, self.pos))
        self.pos += count * 2
        return v

    def take(self, count: int) -> bytes:
        v = bytes(self.data[self.pos : self.pos + count])
        if len(v) != count:
            raise SkelMeshError(f"truncated stream at {self.pos} (wanted {count} bytes)")
        self.pos += count
        return v


def _pack_i16_array(values: list[int]) -> bytes:
    return _I32.pack(len(values)) + struct.pack(f"<{len(values)}h", *values)


# ---------------------------------------------------------------------------
# FStaticLODModel
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LodModel:
    """One FStaticLODModel (spec §5)."""

    sections: list[Section] = field(default_factory=list)
    index_f0: int = 1
    index_data_type_size: int = 2
    indices: list[int] = field(default_factory=list)
    active_bone_indices: list[int] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    size: int = 0
    num_vertices: int = 0
    required_bones: bytes = b""
    raw_point_indices: BulkData | None = None
    num_tex_coords: int = 0
    vb_num_tex_coords: int = 0
    vb_use_full_precision_uvs: int = 0
    vb_use_packed_position: int = 0
    mesh_extension: tuple[float, float, float] = (1.0, 1.0, 1.0)
    mesh_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vertices: list[GpuVertex] = field(default_factory=list)
    color_element_size: int = 4
    colors: bytes | None = None

    # -- derived -----------------------------------------------------------

    @property
    def vertex_stride(self) -> int:
        uv_bytes = 8 if self.vb_use_full_precision_uvs else 4
        return 28 + uv_bytes * self.vb_num_tex_coords

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3

    def bone_index_for(self, vertex_index: int, slot: int = 0) -> int:
        """Map a vertex's chunk-local bone slot to a RefSkeleton bone index."""
        chunk = self.chunk_for_vertex_index(vertex_index)
        return chunk.bone_map[self.vertices[vertex_index].bone_idx[slot]]

    def chunk_for_vertex_index(self, vertex_index: int) -> Chunk:
        chosen = self.chunks[0]
        for chunk in self.chunks:
            if chunk.base_vertex_index <= vertex_index:
                chosen = chunk
        return chosen

    # -- parsing -----------------------------------------------------------

    @classmethod
    def parse(cls, cur: _Cursor, has_vertex_colors: bool) -> "LodModel":
        lod = cls()
        lod.sections = [_read_section(cur) for _ in range(cur.i32())]
        lod.index_f0 = cur.i32()
        lod.index_data_type_size = cur.u8()
        elem_size = cur.i32()
        count = cur.i32()
        if elem_size != lod.index_data_type_size:
            raise SkelMeshError(
                f"index bulk ElementSize {elem_size} != DataTypeSize {lod.index_data_type_size}"
            )
        fmt = "H" if elem_size == 2 else "I"
        lod.indices = list(struct.unpack_from(f"<{count}{fmt}", cur.data, cur.pos))
        cur.pos += count * elem_size
        lod.active_bone_indices = cur.i16_array(cur.i32())
        lod.chunks = [_read_chunk(cur) for _ in range(cur.i32())]
        lod.size = cur.i32()
        lod.num_vertices = cur.i32()
        lod.required_bones = cur.take(cur.i32())
        lod.raw_point_indices = _read_bulk(cur, element_size=4)
        lod.num_tex_coords = cur.i32()
        _read_vertex_buffer(cur, lod)
        if has_vertex_colors:
            lod.color_element_size = cur.i32()
            lod.colors = cur.take(cur.i32() * lod.color_element_size)
        influence_count = cur.i32()
        if influence_count:
            raise SkelMeshError(
                f"VertexInfluences has {influence_count} entries; layout unknown"
            )
        return lod

    # -- self checks --------------------------------------------------------

    def validate(self) -> None:
        tri_sum = sum(s.num_triangles for s in self.sections)
        if tri_sum * 3 != len(self.indices):
            raise SkelMeshError(
                f"section triangles {tri_sum} * 3 != index count {len(self.indices)}"
            )
        if len(self.vertices) != self.num_vertices:
            raise SkelMeshError(
                f"GPU vertex count {len(self.vertices)} != NumVertices {self.num_vertices}"
            )
        if self.vb_num_tex_coords != self.num_tex_coords:
            raise SkelMeshError(
                f"vertex buffer NumTexCoords {self.vb_num_tex_coords} "
                f"!= LOD NumTexCoords {self.num_tex_coords}"
            )
        chunk_total = sum(c.num_rigid_vertices + c.num_soft_vertices for c in self.chunks)
        if chunk_total != self.num_vertices:
            raise SkelMeshError(
                f"chunk rigid+soft {chunk_total} != NumVertices {self.num_vertices}"
            )
        if self.indices and max(self.indices) >= self.num_vertices:
            raise SkelMeshError(
                f"index {max(self.indices)} out of range for {self.num_vertices} vertices"
            )

    # -- count synchronisation ---------------------------------------------

    def sync_counts(self) -> None:
        """Recompute every count that is derivable from the mutable data."""
        self.num_vertices = len(self.vertices)
        self.vb_num_tex_coords = self.num_tex_coords = (
            len(self.vertices[0].uvs) if self.vertices else self.num_tex_coords
        )
        self._sync_sections()
        self._sync_chunks()

    def _sync_sections(self) -> None:
        if not self.sections:
            return
        declared = sum(s.num_triangles for s in self.sections) * 3
        delta = len(self.indices) - declared
        if delta:
            if delta % 3:
                raise SkelMeshError(f"index count {len(self.indices)} is not a multiple of 3")
            self.sections[-1].num_triangles += delta // 3

    def _sync_chunks(self) -> None:
        if not self.chunks:
            return
        bounds = [c.base_vertex_index for c in self.chunks] + [len(self.vertices)]
        for i, chunk in enumerate(self.chunks):
            start, end = bounds[i], max(bounds[i], bounds[i + 1])
            rigid = soft = 0
            influences = 1
            for vertex in self.vertices[start:end]:
                n = vertex.influence_count
                if n <= 1:
                    rigid += 1
                else:
                    soft += 1
                influences = max(influences, n)
            chunk.num_rigid_vertices = rigid
            chunk.num_soft_vertices = soft
            chunk.max_bone_influences = max(chunk.max_bone_influences, influences)

    # -- serialization ------------------------------------------------------

    def to_bytes(self, export_offset: int, start: int) -> bytes:
        """Serialize; ``export_offset + start`` is this LOD's absolute position."""
        self.sync_counts()
        out = bytearray()
        out += _I32.pack(len(self.sections))
        for section in self.sections:
            out += section.to_bytes()
        out += _I32.pack(self.index_f0)
        out += bytes((self.index_data_type_size,))
        fmt = "H" if self.index_data_type_size == 2 else "I"
        out += _I32.pack(self.index_data_type_size)
        out += _I32.pack(len(self.indices))
        out += struct.pack(f"<{len(self.indices)}{fmt}", *self.indices)
        out += _pack_i16_array(self.active_bone_indices)
        out += _I32.pack(len(self.chunks))
        for chunk in self.chunks:
            out += chunk.to_bytes()
        out += _I32.pack(self.size)
        out += _I32.pack(self.num_vertices)
        out += _I32.pack(len(self.required_bones))
        out += self.required_bones
        bulk = self.raw_point_indices
        if bulk is None:
            raise SkelMeshError("RawPointIndices missing")
        out += bulk.to_bytes(export_offset + start + len(out) + 16)
        out += _I32.pack(self.num_tex_coords)
        out += _I32.pack(self.vb_num_tex_coords)
        out += _I32.pack(self.vb_use_full_precision_uvs)
        out += _I32.pack(self.vb_use_packed_position)
        out += _VEC.pack(*self.mesh_extension)
        out += _VEC.pack(*self.mesh_origin)
        out += _I32.pack(self.vertex_stride)
        out += _I32.pack(len(self.vertices))
        for vertex in self.vertices:
            out += vertex.to_bytes()
        if self.colors is not None:
            out += _I32.pack(self.color_element_size)
            out += _I32.pack(len(self.colors) // self.color_element_size)
            out += self.colors
        out += _I32.pack(0)  # VertexInfluences
        return bytes(out)


def _read_section(cur: _Cursor) -> Section:
    material, chunk, first, tris = _SECTION.unpack_from(cur.data, cur.pos)
    cur.pos += 12
    return Section(material, chunk, first, tris, cur.u8())


def _read_chunk(cur: _Cursor) -> Chunk:
    base = cur.i32()
    rigid_count = cur.i32()
    rigid = cur.take(rigid_count * 61)
    soft_count = cur.i32()
    soft = cur.take(soft_count * 68)
    bone_map = cur.i16_array(cur.i32())
    num_rigid = cur.i32()
    num_soft = cur.i32()
    max_influences = cur.i32()
    return Chunk(
        base_vertex_index=base,
        bone_map=bone_map,
        num_rigid_vertices=num_rigid,
        num_soft_vertices=num_soft,
        max_bone_influences=max_influences,
        rigid_vertices=rigid,
        soft_vertices=soft,
        rigid_vertex_count=rigid_count,
        soft_vertex_count=soft_count,
    )


def _read_bulk(cur: _Cursor, element_size: int) -> BulkData:
    flags = cur.u32()
    element_count = cur.i32()
    size_on_disk = cur.i32()
    offset_in_file = cur.i32()
    bulk = BulkData(flags, element_count, size_on_disk, offset_in_file,
                    element_size=element_size)
    if bulk.is_inline:
        bulk.payload = cur.take(size_on_disk)
    return bulk


def _read_vertex_buffer(cur: _Cursor, lod: LodModel) -> None:
    lod.vb_num_tex_coords = cur.i32()
    lod.vb_use_full_precision_uvs = cur.i32()
    lod.vb_use_packed_position = cur.i32()
    lod.mesh_extension = cur.vec()
    lod.mesh_origin = cur.vec()
    stride = cur.i32()
    count = cur.i32()
    if lod.vb_use_full_precision_uvs:
        raise SkelMeshError("full-precision (float2) UVs are not supported")
    # bUsePackedPosition is set on some BL2 meshes but is inert on PC at v832:
    # positions stay 12-byte FVectors, which the stride check below confirms.
    expected = lod.vertex_stride
    if stride != expected:
        raise SkelMeshError(f"vertex stride {stride} != expected {expected}")
    data = cur.data
    base = cur.pos
    n = lod.vb_num_tex_coords
    uv_fmt = struct.Struct(f"<{2 * n}H")
    vertices: list[GpuVertex] = []
    for i in range(count):
        off = base + i * stride
        tx, tz, b0, b1, b2, b3, w0, w1, w2, w3, x, y, z = _VERT_HEAD.unpack_from(data, off)
        flat = uv_fmt.unpack_from(data, off + 28)
        vertices.append(
            GpuVertex(
                tx, tz, [b0, b1, b2, b3], [w0, w1, w2, w3], x, y, z,
                [(flat[k * 2], flat[k * 2 + 1]) for k in range(n)],
            )
        )
    cur.pos = base + count * stride
    lod.vertices = vertices


# ---------------------------------------------------------------------------
# USkeletalMesh
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SkeletalMeshExport:
    """A fully decoded ``SkeletalMesh`` export payload."""

    package: Package | None = None
    net_index: int = 0
    properties: list[PropertyTag] = field(default_factory=list)
    property_terminator: FName | None = None
    bounds_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_extent: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_radius: float = 0.0
    materials: list[int] = field(default_factory=list)
    mesh_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot_origin: tuple[int, int, int] = (0, 0, 0)
    bones: list[MeshBone] = field(default_factory=list)
    skeletal_depth: int = 0
    lods: list[LodModel] = field(default_factory=list)
    tail: bytes = b""
    source_size: int = 0
    has_native_data: bool = True
    """False for the class-default object, which serializes properties only."""

    # -- convenience --------------------------------------------------------

    @property
    def lod0(self) -> LodModel:
        return self.lods[0]

    @property
    def bone_names(self) -> list[str]:
        return [b.name.text for b in self.bones]

    def bone_index(self, name: str) -> int:
        for i, bone in enumerate(self.bones):
            if bone.name.text == name:
                return i
        raise KeyError(f"bone {name!r} not in RefSkeleton")

    @property
    def has_vertex_colors(self) -> bool:
        tag = find_property(self.properties, "bHasVertexColors")
        return bool(tag.value) if tag is not None else False

    @property
    def socket_refs(self) -> list[int]:
        tag = find_property(self.properties, "Sockets")
        return list(tag.value) if tag is not None and tag.value else []

    # -- parsing ------------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes, package: Package) -> "SkeletalMeshExport":
        mesh = cls(package=package, source_size=len(data))
        cur = _Cursor(data, 0)
        mesh.net_index = cur.i32()
        mesh.properties, cur.pos = parse_properties(data, cur.pos, package)
        mesh.property_terminator = package.read_fname(data, cur.pos - 8)[0]
        if cur.pos >= len(data):
            mesh.has_native_data = False
            return mesh
        mesh.bounds_origin = cur.vec()
        mesh.bounds_extent = cur.vec()
        mesh.bounds_radius = cur.f32()
        mesh.materials = [cur.i32() for _ in range(cur.i32())]
        mesh.mesh_origin = cur.vec()
        mesh.rot_origin = (cur.i32(), cur.i32(), cur.i32())
        mesh.bones = [_read_bone(cur, package) for _ in range(cur.i32())]
        mesh.skeletal_depth = cur.i32()
        colors = mesh.has_vertex_colors
        mesh.lods = [LodModel.parse(cur, colors) for _ in range(cur.i32())]
        mesh.tail = bytes(data[cur.pos :])
        for lod in mesh.lods:
            lod.validate()
        return mesh

    # -- serialization ------------------------------------------------------

    def serialize(self, export_offset: int) -> bytes:
        """Rebuild the payload for placement at absolute ``export_offset``."""
        if self.package is None:
            raise SkelMeshError("serialize() needs the source Package for name lookups")
        out = bytearray()
        out += _I32.pack(self.net_index)
        out += serialize_properties(self.properties, self.package, self.property_terminator)
        if not self.has_native_data:
            return bytes(out)
        out += _VEC.pack(*self.bounds_origin)
        out += _VEC.pack(*self.bounds_extent)
        out += _F32.pack(self.bounds_radius)
        out += _I32.pack(len(self.materials))
        for material in self.materials:
            out += _I32.pack(material)
        out += _VEC.pack(*self.mesh_origin)
        out += struct.pack("<3i", *self.rot_origin)
        out += _I32.pack(len(self.bones))
        for bone in self.bones:
            out += bone.to_bytes()
        out += _I32.pack(self.skeletal_depth)
        out += _I32.pack(len(self.lods))
        for lod in self.lods:
            out += lod.to_bytes(export_offset, len(out))
        out += self.tail
        return bytes(out)


def _read_bone(cur: _Cursor, package: Package) -> MeshBone:
    values = _BONE.unpack_from(cur.data, cur.pos)
    cur.pos += 52
    name = package.fname(values[0], values[1])
    return MeshBone(
        name=name,
        flags=values[2],
        orientation=values[3:7],
        position=values[7:10],
        num_children=values[10],
        parent_index=values[11],
        color=values[12],
    )
