"""Gestalt-fragment operations shared by the M2 CLI and the M4 Blender addon.

A *fragment* is a contiguous triangle-index range of a gestalt ``SkeletalMesh``
(see ``docs/FINDINGS.md`` D1).  Everything in this module works on an already
parsed :class:`~bl2_upk.skelmesh.SkeletalMeshExport`.

The one write path the pipeline is allowed to use (``docs/FAILURE_MODES.md`` F12)
is :func:`build_fragment_package`: clone the source mesh into a **new** package
and append a **new** fragment to the clone.  Base packages are never edited, and
the template fragment inside the clone stays untouched, so stock and new parts
coexist.

:func:`build_multi_fragment_package` is the same write path for **several**
fragments at once (one part set, one package): each one is appended in turn, so
their index ranges tile onto the end of LOD0.  A fragment is either a copy of an
existing fragment's range with a per-vertex edit (:func:`append_fragment`, the
M2/M4 shape) or brand-new geometry with its own vertices, bone weights, UVs and
triangles (:func:`append_new_geometry`, the M6 shape).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .reader import Package
from .skelmesh import GpuVertex, LodModel, SkelMeshError, SkeletalMeshExport
from .writer import clone_skeletal_mesh

__all__ = [
    "DEFAULT_FRAGMENT_TABLE",
    "REPO_ROOT_ENV",
    "DEFAULT_SOURCE_PACKAGE",
    "DEFAULT_TANGENT_X",
    "DEFAULT_TANGENT_Z",
    "GESTALT_AR_MESH",
    "MAX_GPU_VERTICES",
    "FragmentRange",
    "FragmentSpec",
    "NewVertex",
    "VertexEdit",
    "append_fragment",
    "append_new_geometry",
    "build_fragment_package",
    "build_multi_fragment_package",
    "default_fragment_table",
    "default_source_package",
    "fragment_vertices",
    "load_fragments",
    "pack_normal",
    "repo_root",
    "standard_deformation_edit",
    "unpack_normal",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Env override for installs where this file is not inside the repo (the
#: Blender add-on zip vendors ``bl2_upk``, so ``parents[2]`` is meaningless there).
REPO_ROOT_ENV = "BL2_PIPELINE_ROOT"


def repo_root() -> Path:
    """Repo root: ``$BL2_PIPELINE_ROOT`` if set, else this file's own checkout."""
    override = os.environ.get(REPO_ROOT_ENV)
    return Path(override) if override else _REPO_ROOT


def default_fragment_table() -> Path:
    """``name,MaterialIndex=..,FirstIndex=..,NumPrimitives=..`` dumped from
    ``Weap_AssaultRifles.GestaltDef_AssaultRifle``."""
    return repo_root() / "scratch" / "gestalt_AR_fragments.txt"


def default_source_package() -> Path:
    """The decompressed ``Startup.upk`` every AR fragment comes from."""
    return repo_root() / "scratch" / "decomp" / "Startup.upk"


#: Import-time snapshots of the two paths above (handy as argument defaults).
DEFAULT_FRAGMENT_TABLE = default_fragment_table()
DEFAULT_SOURCE_PACKAGE = default_source_package()
GESTALT_AR_MESH = "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"

#: The LOD index buffer is uint16 (``IndexDataTypeSize == 2``), so a gestalt
#: mesh cannot address more than this many vertices.  Kept as a hard ceiling in
#: :func:`append_new_geometry`; lint check L9 warns long before it is reached.
MAX_GPU_VERTICES = 65535

#: ``FPackedNormal`` W bytes every vertex of the AR gestalt uses: TangentX.W is
#: always 128, TangentZ.W is the binormal sign (255 = +1, the common case).
DEFAULT_TANGENT_X = 128 << 24
DEFAULT_TANGENT_Z = 255 << 24


@dataclass(frozen=True, slots=True)
class FragmentRange:
    """One row of the gestalt definition's ``Parts`` array."""

    name: str
    material_index: int
    first_index: int
    num_primitives: int

    def as_tuple(self) -> tuple[int, int]:
        return (self.first_index, self.num_primitives)


#: ``edit(new_vertex, src_vertex_id, new_vertex_id) -> bool`` -- return True to
#: count the vertex as "moved" in the returned summary.
VertexEdit = Callable[[GpuVertex, int, int], bool | None]


# ---------------------------------------------------------------------------
# fragment table
# ---------------------------------------------------------------------------


def load_fragment_rows(path: str | Path | None = None) -> dict[str, FragmentRange]:
    """Parse the fragment table into ``name -> FragmentRange``."""
    table = Path(path) if path is not None else default_fragment_table()
    rows: dict[str, FragmentRange] = {}
    for line in Path(table).read_text().splitlines():
        if not line.strip():
            continue
        name, rest = line.strip().split(",", 1)
        kv = dict(item.split("=") for item in rest.split(","))
        rows[name] = FragmentRange(
            name=name,
            material_index=int(kv.get("MaterialIndex", 0)),
            first_index=int(kv["FirstIndex"]),
            num_primitives=int(kv["NumPrimitives"]),
        )
    return rows


def load_fragments(path: str | Path | None = None) -> dict[str, tuple[int, int]]:
    """``name -> (FirstIndex, NumPrimitives)`` (the M1/M2 call shape)."""
    return {name: row.as_tuple() for name, row in load_fragment_rows(path).items()}


def fragment_vertices(lod: LodModel, first: int, num: int) -> list[int]:
    """Sorted, de-duplicated vertex ids referenced by a fragment's triangles."""
    return sorted(set(lod.indices[first : first + 3 * num]))


# ---------------------------------------------------------------------------
# FPackedNormal
# ---------------------------------------------------------------------------


def unpack_normal(packed: int) -> tuple[float, float, float, float]:
    """Unpack an FPackedNormal u32 to four floats in [-1, 1]."""
    return tuple(((packed >> (8 * i)) & 0xFF) / 127.5 - 1.0 for i in range(4))  # type: ignore[return-value]


def pack_normal(vector: Sequence[float], w_byte: int) -> int:
    """Pack x/y/z into an FPackedNormal u32, keeping ``w_byte`` verbatim.

    ``byte = round((n + 1) * 127.5)``, clamped to 0..255 -- the inverse of
    :func:`unpack_normal` to within half a step.
    """
    out = 0
    for i in range(3):
        value = float(vector[i])
        byte = int(round((max(-1.0, min(1.0, value)) + 1.0) * 127.5))
        out |= max(0, min(255, byte)) << (8 * i)
    return out | ((w_byte & 0xFF) << 24)


# ---------------------------------------------------------------------------
# append a fragment
# ---------------------------------------------------------------------------


def _ensure_vertex_budget(current: int, adding: int) -> int:
    """Guard the uint16 index buffer; returns the new total."""
    total = current + adding
    if total > MAX_GPU_VERTICES:
        raise SkelMeshError(
            f"appending {adding} vertices would take the mesh to {total} vertices; "
            f"the LOD index buffer is uint16, so it must stay below {MAX_GPU_VERTICES} "
            "(split the part into fewer vertices, or decimate it)"
        )
    return total


def append_fragment(
    mesh: SkeletalMeshExport,
    first: int,
    num: int,
    edit: VertexEdit | None = None,
) -> dict:
    """Copy one fragment's vertices+triangles to the end of LOD0.

    The copied vertices keep their bone weights, raw UV values and (unless
    ``edit`` overwrites them) their packed tangents.  ``edit`` is called once
    per copied vertex, in ascending source-vertex-id order, with the *copy*.

    Returns the summary the ``.fragment.json`` sidecar is built from.
    """
    lod = mesh.lod0
    old_ids = fragment_vertices(lod, first, num)
    _ensure_vertex_budget(len(lod.vertices), len(old_ids))
    base = len(lod.vertices)
    remap: dict[int, int] = {}
    moved = 0
    for rank, vid in enumerate(old_ids):
        vertex = lod.vertices[vid].copy()
        new_vid = base + rank
        if edit is not None and edit(vertex, vid, new_vid):
            moved += 1
        lod.vertices.append(vertex)
        remap[vid] = new_vid
    new_first = len(lod.indices)
    lod.indices.extend(remap[i] for i in lod.indices[first : first + 3 * num])
    lod.sync_counts()
    lod.validate()
    return {
        "first_index": new_first,
        "num_primitives": num,
        "new_vertices": len(old_ids),
        "moved": moved,
        "total_vertices": len(lod.vertices),
        "total_indices": len(lod.indices),
        "source_first_index": first,
    }


# ---------------------------------------------------------------------------
# append brand-new geometry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NewVertex:
    """One vertex of a *new* fragment, in the mesh's own (UE) space.

    ``bones`` are RefSkeleton bone **names or global indices** (the chunk-local
    indices the GPU buffer stores are worked out here); ``weights`` are 0..255
    and are normalised to sum to 255.  ``uvs`` are **raw** half pairs, one per
    texture-coordinate set, padded with ``(0, 0)`` and truncated to the LOD's
    ``NumTexCoords``.
    """

    x: float
    y: float
    z: float
    tangent_x: int = DEFAULT_TANGENT_X
    tangent_z: int = DEFAULT_TANGENT_Z
    bones: Sequence[int | str] = (0, 0, 0, 0)
    weights: Sequence[int] = (255, 0, 0, 0)
    uvs: Sequence[tuple[int, int]] = ()

    @classmethod
    def from_any(cls, value: "NewVertex | Sequence | dict") -> "NewVertex":
        """Accept a :class:`NewVertex`, a mapping, or the positional tuple
        ``(x, y, z, tangent_x, tangent_z, bones, weights, uvs)``."""
        if isinstance(value, NewVertex):
            return value
        if isinstance(value, dict):
            return cls(**value)
        return cls(*value)


def _normalised_weights(weights: Sequence[int]) -> list[int]:
    """Four uint8 weights summing to exactly 255."""
    raw = [max(0, int(w)) for w in list(weights)[:4]]
    raw += [0] * (4 - len(raw))
    total = sum(raw)
    if total == 0:
        raise SkelMeshError("vertex has no bone influence (all four weights are 0)")
    if total != 255:
        raw = [int(round(w * 255.0 / total)) for w in raw]
        drift = 255 - sum(raw)
        best = max(range(4), key=lambda i: raw[i])
        raw[best] = max(0, min(255, raw[best] + drift))
    return raw


def _chunk_bone_slot(mesh: SkeletalMeshExport, lod: LodModel, chunk, bone: int | str) -> int:
    """RefSkeleton bone (name or index) -> this chunk's bone-map slot."""
    index = mesh.bone_index(bone) if isinstance(bone, str) else int(bone)
    if not 0 <= index < len(mesh.bones):
        raise SkelMeshError(
            f"bone index {index} is out of range for a {len(mesh.bones)}-bone RefSkeleton"
        )
    if index in chunk.bone_map:
        return chunk.bone_map.index(index)
    if len(chunk.bone_map) >= 256:
        raise SkelMeshError(
            f"chunk bone map is full (256 bones); {mesh.bones[index].name.text!r} does not fit"
        )
    chunk.bone_map.append(index)
    if index not in lod.active_bone_indices:
        lod.active_bone_indices.append(index)
    if index not in lod.required_bones:
        lod.required_bones = bytes(sorted(set(lod.required_bones) | {index}))
    return len(chunk.bone_map) - 1


def append_new_geometry(
    mesh: SkeletalMeshExport,
    vertices: Sequence["NewVertex | Sequence | dict"],
    triangles: Sequence[Sequence[int]],
) -> dict:
    """Append brand-new vertices + triangles as one fragment at the end of LOD0.

    ``triangles`` index ``vertices`` locally (0..len-1).  New vertices land in
    the last chunk (that is what ``chunk_for_vertex_index`` resolves them to),
    whose bone map is extended when a bone it does not list is used.

    Returns the same summary shape as :func:`append_fragment`.
    """
    lod = mesh.lod0
    if not lod.chunks:
        raise SkelMeshError("LOD0 has no chunk to append vertices to")
    if not vertices or not triangles:
        raise SkelMeshError("a new fragment needs at least one vertex and one triangle")
    base = len(lod.vertices)
    _ensure_vertex_budget(base, len(vertices))
    chunk = lod.chunks[-1]
    num_uv = lod.num_tex_coords or lod.vb_num_tex_coords or 1
    for rank, raw in enumerate(vertices):
        vertex = NewVertex.from_any(raw)
        try:
            weights = _normalised_weights(vertex.weights)
        except SkelMeshError as exc:
            raise SkelMeshError(f"vertex {rank}: {exc}") from exc
        bones = list(vertex.bones)[:4]
        bones += [bones[0] if bones else 0] * (4 - len(bones))
        slots = [
            _chunk_bone_slot(mesh, lod, chunk, bone) if weights[i] else 0
            for i, bone in enumerate(bones)
        ]
        uvs = [tuple(pair) for pair in list(vertex.uvs)[:num_uv]]
        uvs += [(0, 0)] * (num_uv - len(uvs))
        lod.vertices.append(
            GpuVertex(
                int(vertex.tangent_x) & 0xFFFFFFFF,
                int(vertex.tangent_z) & 0xFFFFFFFF,
                slots,
                weights,
                float(vertex.x),
                float(vertex.y),
                float(vertex.z),
                [(int(u) & 0xFFFF, int(v) & 0xFFFF) for u, v in uvs],
            )
        )
    new_first = len(lod.indices)
    count = len(vertices)
    for rank, tri in enumerate(triangles):
        if len(tri) != 3:
            raise SkelMeshError(f"triangle {rank} has {len(tri)} corners; the mesh must be triangulated")
        for corner in tri:
            if not 0 <= int(corner) < count:
                raise SkelMeshError(
                    f"triangle {rank} references local vertex {corner}, "
                    f"but the fragment has {count} vertices"
                )
        lod.indices.extend(base + int(corner) for corner in tri)
    lod.sync_counts()
    lod.validate()
    return {
        "first_index": new_first,
        "num_primitives": len(triangles),
        "new_vertices": count,
        "moved": 0,
        "total_vertices": len(lod.vertices),
        "total_indices": len(lod.indices),
        "source_first_index": None,
    }


def standard_deformation_edit(
    y_cut: float = -80.0, dz: float = 8.0, x_scale: float = 1.5
) -> VertexEdit:
    """The D7 standard deformation as a :data:`VertexEdit` (mesh/UE space)."""

    def edit(vertex: GpuVertex, _src_vid: int, _new_vid: int) -> bool:
        if vertex.y < y_cut:
            vertex.z += dz
            vertex.x *= x_scale
            return True
        return False

    return edit


# ---------------------------------------------------------------------------
# the one sanctioned write path
# ---------------------------------------------------------------------------


def build_fragment_package(
    out_path: str | Path,
    fragment_name: str,
    *,
    source_package: str | Path | None = None,
    source_mesh: str = GESTALT_AR_MESH,
    package_name: str = "PipelineMeshes",
    mesh_name: str = "PL_AR_Gestalt_Mesh",
    template_fragment: str = "AR_Barrel_Vladof",
    edit: VertexEdit | None = None,
    dz: float = 0.0,
    part_name: str | None = None,
    fragments_table: str | Path | None = None,
    sidecar_extra: dict | None = None,
    write_sidecar: bool = True,
) -> dict:
    """Clone ``source_mesh`` into a new package with one extra fragment.

    Writes ``out_path`` plus the ``.fragment.json`` sidecar the M2 SDK mod
    consumes, re-parses the result from disk and returns the merged summary.
    """
    first, num = load_fragments(fragments_table)[template_fragment]
    info: dict = {}

    def mutate(mesh: SkeletalMeshExport) -> None:
        info.update(append_fragment(mesh, first, num, edit))

    src = Package.from_file(source_package or default_source_package())
    clone_skeletal_mesh(src, source_mesh, out_path, package_name, mesh_name, mutate=mutate)

    out_pkg = Package.from_file(out_path)
    entry = out_pkg.find_export(mesh_name, "SkeletalMesh")
    written = SkeletalMeshExport.parse(out_pkg.read_export_bytes(entry), out_pkg)
    ok = (
        len(written.lod0.vertices) == info["total_vertices"]
        and len(written.lod0.indices) == info["total_indices"]
    )
    sidecar = {
        "package": package_name,
        "mesh_path": f"{package_name}.{mesh_name}",
        "fragment": fragment_name,
        "template_fragment": template_fragment,
        "first_index": info["first_index"],
        "num_primitives": info["num_primitives"],
        "dz": dz,
        "part_name": part_name or fragment_name,
        "reparsed_ok": ok,
        "out": str(out_path),
    }
    if sidecar_extra:
        sidecar.update(sidecar_extra)
    sidecar_path = Path(out_path).with_suffix(".fragment.json")
    if write_sidecar:
        sidecar_path.write_text(json.dumps(sidecar, indent=1))
    return {**info, **sidecar, "sidecar": str(sidecar_path)}


# ---------------------------------------------------------------------------
# several fragments in one package
# ---------------------------------------------------------------------------


@dataclass
class FragmentSpec:
    """One fragment to append, in either of the two shapes the pipeline has.

    *New geometry* (the M6 shape): ``vertices`` + ``triangles`` carry their own
    arrays; ``template_fragment`` is then only the fragment whose sockets and
    bounds the runtime mod clones.

    *Copy a template range* (the M2/M4 shape): leave ``vertices`` as ``None``;
    the template fragment's vertices and triangles are copied and ``edit`` is
    called once per copied vertex (see :func:`append_fragment`).
    """

    name: str
    template_fragment: str = ""
    vertices: Sequence["NewVertex | Sequence | dict"] | None = None
    triangles: Sequence[Sequence[int]] | None = None
    edit: VertexEdit | None = None
    dz: float = 0.0
    part_name: str | None = None
    #: original (unmangled) socket names this fragment needs -- carried into the
    #: sidecar for ``bl2_lint`` / ``bl2_partgen``; nothing here reads them
    sockets: Sequence[str] = ()

    @property
    def is_new_geometry(self) -> bool:
        return self.vertices is not None

    @classmethod
    def from_any(cls, value: "FragmentSpec | dict") -> "FragmentSpec":
        return value if isinstance(value, FragmentSpec) else cls(**value)


def _grow_bounds(mesh: SkeletalMeshExport, first_new_vertex: int) -> bool:
    """Union the mesh bounds with the vertices appended from ``first_new_vertex``."""
    new = mesh.lod0.vertices[first_new_vertex:]
    if not new:
        return False
    origin = mesh.bounds_origin
    extent = mesh.bounds_extent
    lo = [origin[i] - extent[i] for i in range(3)]
    hi = [origin[i] + extent[i] for i in range(3)]
    for vertex in new:
        for i, value in enumerate(vertex.position):
            lo[i] = min(lo[i], value)
            hi[i] = max(hi[i], value)
    grown_origin = tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
    grown_extent = tuple((hi[i] - lo[i]) * 0.5 for i in range(3))
    if grown_origin == origin and grown_extent == extent:
        return False
    mesh.bounds_origin = grown_origin  # type: ignore[assignment]
    mesh.bounds_extent = grown_extent  # type: ignore[assignment]
    mesh.bounds_radius = max(
        mesh.bounds_radius,
        math.sqrt(sum(e * e for e in grown_extent)),
    )
    return True


def build_multi_fragment_package(
    out_path: str | Path,
    fragments: Sequence["FragmentSpec | dict"],
    *,
    source_package: str | Path | None = None,
    source_mesh: str = GESTALT_AR_MESH,
    package_name: str = "PipelineMeshes",
    mesh_name: str = "PL_AR_Gestalt_Mesh",
    fragments_table: str | Path | None = None,
    sidecar_extra: dict | None = None,
    write_sidecar: bool = True,
    grow_bounds: bool = True,
) -> dict:
    """Clone ``source_mesh`` into a new package with **several** extra fragments.

    The fragments are appended in the order given, each one taking the index
    range that starts where the previous one ended, so they tile onto the end of
    LOD0 and every stock fragment stays where it was.

    Writes ``out_path`` plus a ``.fragment.json`` sidecar that lists every
    fragment under ``fragments`` *and* repeats the first one's keys at the top
    level, so single-fragment consumers (the M2 SDK mod, ``bl2_lint``) keep
    working unchanged.  The package is re-parsed from disk before returning.
    """
    specs = [FragmentSpec.from_any(f) for f in fragments]
    if not specs:
        raise ValueError("build_multi_fragment_package needs at least one fragment")
    names = [spec.name for spec in specs]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate fragment names in {names}")

    table = load_fragments(fragments_table)
    for spec in specs:
        if spec.is_new_geometry:
            if not spec.triangles:
                raise ValueError(f"fragment {spec.name!r}: new geometry needs triangles")
        elif spec.template_fragment not in table:
            raise KeyError(
                f"fragment {spec.name!r}: template fragment {spec.template_fragment!r} "
                "is not in the gestalt fragment table"
            )

    infos: list[dict] = []
    grown: list[bool] = []

    def mutate(mesh: SkeletalMeshExport) -> None:
        first_new_vertex = len(mesh.lod0.vertices)
        for spec in specs:
            if spec.is_new_geometry:
                info = append_new_geometry(mesh, spec.vertices or (), spec.triangles or ())
            else:
                first, num = table[spec.template_fragment]
                info = append_fragment(mesh, first, num, spec.edit)
            infos.append({
                "fragment": spec.name,
                "template_fragment": spec.template_fragment,
                "dz": float(spec.dz),
                "part_name": spec.part_name or spec.name,
                "sockets": [str(s) for s in spec.sockets],
                "new_geometry": spec.is_new_geometry,
                **info,
            })
        grown.append(_grow_bounds(mesh, first_new_vertex) if grow_bounds else False)

    src = Package.from_file(source_package or default_source_package())
    clone_skeletal_mesh(src, source_mesh, out_path, package_name, mesh_name, mutate=mutate)

    out_pkg = Package.from_file(out_path)
    entry = out_pkg.find_export(mesh_name, "SkeletalMesh")
    written = SkeletalMeshExport.parse(out_pkg.read_export_bytes(entry), out_pkg)
    lod = written.lod0
    last = infos[-1]
    ok = (
        len(lod.vertices) == last["total_vertices"]
        and len(lod.indices) == last["total_indices"]
    )
    for info in infos:
        first, num = info["first_index"], info["num_primitives"]
        span = lod.indices[first : first + 3 * num]
        info["reparsed_ok"] = (
            len(span) == 3 * num and bool(span) and max(span) < len(lod.vertices)
        )
        ok = ok and info["reparsed_ok"]

    first_info = infos[0]
    sidecar = {
        "package": package_name,
        "mesh_path": f"{package_name}.{mesh_name}",
        "fragment": first_info["fragment"],
        "template_fragment": first_info["template_fragment"],
        "first_index": first_info["first_index"],
        "num_primitives": first_info["num_primitives"],
        "dz": first_info["dz"],
        "part_name": first_info["part_name"],
        "new_vertices": first_info["new_vertices"],
        "reparsed_ok": ok,
        "out": str(out_path),
        "total_vertices": last["total_vertices"],
        "total_indices": last["total_indices"],
        "bounds_grown": bool(grown and grown[0]),
        "fragments": [
            {
                "fragment": info["fragment"],
                "template_fragment": info["template_fragment"],
                "first_index": info["first_index"],
                "num_primitives": info["num_primitives"],
                "new_vertices": info["new_vertices"],
                "dz": info["dz"],
                "part_name": info["part_name"],
                "sockets": info["sockets"],
                "new_geometry": info["new_geometry"],
            }
            for info in infos
        ],
    }
    if sidecar_extra:
        sidecar.update(sidecar_extra)
    sidecar_path = Path(out_path).with_suffix(".fragment.json")
    if write_sidecar:
        sidecar_path.write_text(json.dumps(sidecar, indent=1))
    return {
        **first_info,
        **sidecar,
        "moved": sum(int(info["moved"]) for info in infos),
        "sidecar": str(sidecar_path),
        "fragment_infos": infos,
    }
