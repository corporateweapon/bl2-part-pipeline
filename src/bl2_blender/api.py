"""M4 Blender API -- import / validate / deform / export one gestalt fragment.

Importable **outside** Blender: ``bpy`` and ``mathutils`` are imported inside the
functions that need them, so the module-level half (source-package reading,
checks, coordinate maths) can be unit-tested with plain CPython and the whole
thing runs under ``blender --background --python <script>``.

Coordinate frame
----------------
The gestalt mesh's own space is UE's: ``x`` sideways, ``-y`` = muzzle/forward,
``z`` up, left-handed, units ~cm.  Blender is right-handed, so the import
mirrors **one** axis and nothing else::

    blender = (-x,  y,  z)          # UE  -> Blender
    ue      = (-bx, by, bz)         # Blender -> UE   (same map; it is its own inverse)

* one mirror flips handedness, so the source triangle winding becomes
  counter-clockwise-outward in Blender and no index reordering is needed;
* the scale is exactly 1 (1 UE unit = 1 Blender unit), so a round trip is
  bit-exact -- a negation and a float32 store are both lossless;
* ``y`` and ``z`` are untouched, so the D7 numbers (``y_cut=-80``, ``dz=+8``)
  mean the same thing in Blender as in the package.

Directions (normals, tangents) use the same map.  Rotations (bones, sockets)
are conjugated by it: a quaternion ``(x, y, z, w)`` becomes ``(x, -y, -z, w)``
(verified against umodel's glTF export of the same skeleton, bone world
positions agreeing to 7e-5 units).

What survives a round trip
--------------------------
Preserved bit-exactly (copied from the source vertex on export): bone indices
and weights, all four raw UV half-pairs (UV2/UV3 are *not* UVs -- they carry
gestalt payload data, see the codec notes), the triangle list of the template
fragment, every other fragment of the mesh, and all 48 sockets.
Recomputed from the edited Blender geometry: vertex positions, ``TangentZ``
(normal) and ``TangentX`` (tangent), packed back to ``FPackedNormal``
(``byte = round((n + 1) * 127.5)``); the ``W`` byte of each packed normal is
kept from the source vertex.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

def _bootstrap_paths() -> None:
    """Make ``bl2_upk`` importable from a repo checkout *or* an installed zip."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[1], here.parent / "_vendor"):
        if (candidate / "bl2_upk").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


_bootstrap_paths()

from bl2_upk import Package, SkeletalMeshExport, float_to_half, parse_properties  # noqa: E402
from bl2_upk.fragment import (  # noqa: E402
    DEFAULT_FRAGMENT_TABLE,
    DEFAULT_SOURCE_PACKAGE,
    DEFAULT_TANGENT_X,
    DEFAULT_TANGENT_Z,
    GESTALT_AR_MESH,
    MAX_GPU_VERTICES,
    REPO_ROOT_ENV,
    FragmentSpec,
    NewVertex,
    build_fragment_package,
    build_multi_fragment_package,
    default_fragment_table,
    default_source_package,
    fragment_vertices,
    load_fragment_rows,
    pack_normal,
    repo_root,
    unpack_normal,
)

__all__ = [
    "ADDON_VERSION",
    "Check",
    "ExportBlocked",
    "ExportResult",
    "ImportResult",
    "SocketInfo",
    "SourceFragment",
    "apply_transforms",
    "blender_to_ue",
    "default_fragments_table",
    "default_out_package",
    "default_package",
    "default_socket_mappings",
    "repo_root",
    "ensure_triangulated",
    "export_fragment",
    "export_fragments",
    "import_fragment",
    "read_source_fragment",
    "standard_deformation",
    "suggest_fragment_name",
    "check_fragment_name",
    "ue_to_blender",
    "validate_for_export",
]

ADDON_VERSION = (0, 5, 0)

DEFAULT_MESH_PATH = GESTALT_AR_MESH
DEFAULT_FRAGMENT = "AR_Barrel_Vladof"
DEFAULT_NEW_FRAGMENT = "AR_Barrel_PL_Bent"
DEFAULT_PACKAGE_NAME = "PipelineMeshes"
DEFAULT_MESH_NAME = "PL_AR_Gestalt_Mesh"


def default_package() -> Path:
    """The decompressed package fragments are imported from."""
    return default_source_package()


def default_fragments_table() -> Path:
    """The gestalt fragment table (name -> index range)."""
    return default_fragment_table()


def default_socket_mappings() -> Path:
    """``GestaltSocketMappings`` dump; optional (a prefix rule works without it)."""
    return repo_root() / "scratch" / "gestalt_AR.txt"


#: Import-time snapshots, used as argument/operator defaults.  All of them
#: follow ``$BL2_PIPELINE_ROOT`` when the add-on is installed outside the repo.
REPO_ROOT = repo_root()
DEFAULT_PACKAGE = DEFAULT_SOURCE_PACKAGE
DEFAULT_SOCKET_MAPPINGS = default_socket_mappings()

TRANSFORM_NOTE = "blender = (-x, y, z) of the UE mesh space, scale 1.0 (self-inverse)"

# -- custom property keys on the imported object ----------------------------
P_PACKAGE = "bl2_package"
P_MESH_PATH = "bl2_mesh_path"
P_FRAGMENT = "bl2_fragment"
P_FIRST_INDEX = "bl2_first_index"
P_NUM_PRIMITIVES = "bl2_num_primitives"
P_VERTEX_COUNT = "bl2_source_vertex_count"
P_SRC_VIDS = "bl2_src_vids"
P_BONES = "bl2_bones"
P_SOCKETS = "bl2_sockets"
P_ARMATURE = "bl2_armature"
P_TABLE = "bl2_fragments_table"
P_TRANSFORM = "bl2_transform"
P_VERSION = "bl2_addon_version"
P_ROLE = "bl2_role"

#: name of the int attribute carrying the original global vertex ids
ATTR_SRC_VID = "bl2_src_vid"
UV0_NAME = "UV0"
RAW_UV_ATTRS = ("bl2_uv1", "bl2_uv2", "bl2_uv3")

_EPS_IDENTITY = 1e-6


class ExportBlocked(Exception):
    """Raised when :func:`validate_for_export` reported an error-level check."""

    def __init__(self, checks: list["Check"]) -> None:
        self.checks = checks
        bad = "; ".join(c.message or c.name for c in checks if c.level == "error")
        super().__init__(f"export blocked: {bad}")


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Check:
    """One validation result.  ``level`` is ``ok`` / ``warn`` / ``error``."""

    name: str
    level: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.level != "error"

    def __str__(self) -> str:
        mark = {"ok": "OK  ", "warn": "WARN", "error": "FAIL"}.get(self.level, "?   ")
        return f"[{mark}] {self.name}" + (f": {self.message}" if self.message else "")

    def to_dict(self) -> dict:
        return {"name": self.name, "level": self.level, "message": self.message}


def _checks_dicts(checks: list[Check]) -> list[dict]:
    return [c.to_dict() for c in checks]


def checks_ok(checks: list[Check]) -> bool:
    """True when no check is error-level."""
    return all(c.ok for c in checks)


@dataclass(slots=True)
class ImportResult:
    object_name: str = ""
    armature_name: str = ""
    collection_name: str = ""
    package: str = ""
    mesh_path: str = ""
    fragment: str = ""
    first_index: int = 0
    num_primitives: int = 0
    vertex_count: int = 0
    triangle_count: int = 0
    bone_count: int = 0
    socket_names: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return checks_ok(self.checks)

    def to_dict(self) -> dict:
        return {
            "object": self.object_name,
            "armature": self.armature_name,
            "collection": self.collection_name,
            "package": self.package,
            "mesh_path": self.mesh_path,
            "fragment": self.fragment,
            "first_index": self.first_index,
            "num_primitives": self.num_primitives,
            "vertices": self.vertex_count,
            "triangles": self.triangle_count,
            "bones": self.bone_count,
            "sockets": list(self.socket_names),
            "transform": TRANSFORM_NOTE,
            "ok": self.ok,
            "checks": _checks_dicts(self.checks),
        }


@dataclass(slots=True)
class ExportResult:
    out_package: str = ""
    sidecar: str = ""
    package_name: str = ""
    mesh_path: str = ""
    fragment: str = ""
    template_fragment: str = ""
    part_name: str = ""
    first_index: int = 0
    num_primitives: int = 0
    new_vertices: int = 0
    moved: int = 0
    total_vertices: int = 0
    total_indices: int = 0
    dz: float = 0.0
    max_delta: float = 0.0
    normal_agreement: float = 0.0
    #: one entry per exported fragment (the sidecar's ``fragments`` array);
    #: a single-object export has exactly one, equal to the fields above
    fragments: list[dict] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return checks_ok(self.checks)

    def to_dict(self) -> dict:
        return {
            "out": self.out_package,
            "sidecar": self.sidecar,
            "package": self.package_name,
            "mesh_path": self.mesh_path,
            "fragment": self.fragment,
            "template_fragment": self.template_fragment,
            "part_name": self.part_name,
            "first_index": self.first_index,
            "num_primitives": self.num_primitives,
            "new_vertices": self.new_vertices,
            "moved": self.moved,
            "total_vertices": self.total_vertices,
            "total_indices": self.total_indices,
            "dz": self.dz,
            "max_delta": self.max_delta,
            "normal_agreement": self.normal_agreement,
            "fragments": [dict(f) for f in self.fragments],
            "ok": self.ok,
            "checks": _checks_dicts(self.checks),
        }


# ---------------------------------------------------------------------------
# coordinate transform  (no bpy)
# ---------------------------------------------------------------------------


def ue_to_blender(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Mesh-space UE coordinates -> Blender coordinates."""
    return (-x, y, z)


def blender_to_ue(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Blender coordinates -> mesh-space UE coordinates (same map)."""
    return (-x, y, z)


def _quat_ue_to_blender(orientation) -> tuple[float, float, float, float]:
    """UE ``(x, y, z, w)`` -> Blender ``(w, x, y, z)`` under the X mirror."""
    x, y, z, w = orientation
    return (w, x, -y, -z)


def _rotator_to_quat(rotator) -> tuple[float, float, float, float]:
    """UE ``FRotator`` (pitch, yaw, roll in 1/65536 turns) -> UE quat xyzw."""
    pitch, yaw, roll = (v * math.pi / 32768.0 for v in rotator)
    sp, cp = math.sin(pitch * 0.5), math.cos(pitch * 0.5)
    sy, cy = math.sin(yaw * 0.5), math.cos(yaw * 0.5)
    sr, cr = math.sin(roll * 0.5), math.cos(roll * 0.5)
    return (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


# ---------------------------------------------------------------------------
# source package reading  (no bpy)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SocketInfo:
    """One ``SkeletalMeshSocket`` sub-object of the gestalt mesh."""

    name: str
    bone: str
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[int, int, int] = (0, 0, 0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bone": self.bone,
            "location": list(self.location),
            "rotation": list(self.rotation),
            "scale": list(self.scale),
        }


@dataclass(slots=True)
class SourceFragment:
    """Everything one fragment needs from its source package."""

    package_path: Path
    mesh_path: str
    fragment: str
    first_index: int
    num_primitives: int
    package: Package
    mesh: SkeletalMeshExport
    vertex_ids: list[int]
    sockets: list[SocketInfo]
    expected_sockets: list[str]
    fragments_table: Path

    @property
    def bone_names(self) -> list[str]:
        return self.mesh.bone_names

    def triangles(self) -> list[tuple[int, int, int]]:
        """Fragment triangles, re-indexed to 0..len(vertex_ids)-1."""
        local = {vid: i for i, vid in enumerate(self.vertex_ids)}
        idx = self.mesh.lod0.indices
        first, num = self.first_index, self.num_primitives
        return [
            (local[idx[first + 3 * t]], local[idx[first + 3 * t + 1]], local[idx[first + 3 * t + 2]])
            for t in range(num)
        ]


_SOCKET_MAPPING_RE = re.compile(
    r'SkeletalMeshFragmentName="(?P<frag>[^"]*)",'
    r'OriginalSocketName="(?P<orig>[^"]*)",'
    r'MangledSocketName="(?P<mangled>[^"]*)"'
)


def load_socket_mappings(path: str | Path | None = None) -> dict[str, list[str]]:
    """``fragment -> [mangled socket name, ...]`` from a gestalt-def dump."""
    table = Path(path) if path is not None else default_socket_mappings()
    out: dict[str, list[str]] = {}
    if not Path(table).exists():
        return out
    for match in _SOCKET_MAPPING_RE.finditer(Path(table).read_text(errors="replace")):
        out.setdefault(match.group("frag"), []).append(match.group("mangled"))
    return out


def _read_sockets(package: Package, mesh_entry) -> list[SocketInfo]:
    sockets: list[SocketInfo] = []
    for entry in package.exports:
        if entry.outer_index != mesh_entry.index or entry.clsname != "SkeletalMeshSocket":
            continue
        tags, _ = parse_properties(package.read_export_bytes(entry), 4, package)
        values = {tag.name.text: tag.value for tag in tags}
        sockets.append(
            SocketInfo(
                name=str(values.get("SocketName", entry.name)),
                bone=str(values.get("BoneName", "Root")),
                location=tuple(values.get("RelativeLocation") or (0.0, 0.0, 0.0)),
                rotation=tuple(values.get("RelativeRotation") or (0, 0, 0)),
                scale=tuple(values.get("RelativeScale") or (1.0, 1.0, 1.0)),
            )
        )
    return sockets


def _sockets_for_fragment(
    fragment: str, sockets: list[SocketInfo], fragment_names: list[str],
    mappings: dict[str, list[str]],
) -> list[str]:
    """Mangled socket names that belong to ``fragment``.

    Uses ``GestaltSocketMappings`` when the dump is available, otherwise the
    ``<Fragment>_<Socket>`` prefix rule with longest-fragment-wins so that
    ``AR_Barrel_Vladof`` does not swallow ``AR_Barrel_Vladof_Alt``'s sockets.
    """
    if fragment in mappings:
        return list(mappings[fragment])
    longer = [f for f in fragment_names if f != fragment and f.startswith(fragment + "_")]
    names = []
    for socket in sockets:
        if not socket.name.startswith(fragment + "_"):
            continue
        if any(socket.name.startswith(other + "_") for other in longer):
            continue
        names.append(socket.name)
    return names


def read_source_fragment(
    package_path: str | Path | None = None,
    mesh_path: str | None = None,
    fragment_name: str = DEFAULT_FRAGMENT,
    fragments_table: str | Path | None = None,
    socket_mappings: str | Path | None = None,
) -> SourceFragment:
    """Parse the package and collect one fragment's geometry, bones and sockets."""
    package_path = Path(package_path or default_package())
    mesh_path = mesh_path or DEFAULT_MESH_PATH
    table = Path(fragments_table or default_fragments_table())
    rows = load_fragment_rows(table)
    if fragment_name not in rows:
        raise KeyError(f"fragment {fragment_name!r} is not in {table}")
    row = rows[fragment_name]

    package = Package.from_file(package_path)
    entry = package.find_export(mesh_path, "SkeletalMesh")
    mesh = SkeletalMeshExport.parse(package.read_export_bytes(entry), package)
    vertex_ids = fragment_vertices(mesh.lod0, row.first_index, row.num_primitives)
    sockets = _read_sockets(package, entry)
    expected = _sockets_for_fragment(
        fragment_name, sockets, list(rows), load_socket_mappings(socket_mappings)
    )
    return SourceFragment(
        package_path=package_path,
        mesh_path=mesh_path,
        fragment=fragment_name,
        first_index=row.first_index,
        num_primitives=row.num_primitives,
        package=package,
        mesh=mesh,
        vertex_ids=vertex_ids,
        sockets=sockets,
        expected_sockets=expected,
        fragments_table=table,
    )


def default_out_package(name: str = "PipelineMeshes_m4") -> Path:
    """Where the add-on writes by default: ``scratch/<name>.upk``."""
    return repo_root() / "scratch" / f"{name}.upk"


def suggest_fragment_name(template: str, fragments_table: str | Path | None = None) -> str:
    """A unique new fragment name, so nobody has to remember the rename rule.

    The new object MUST NOT reuse an existing fragment name -- that is the
    load-order/override failure the whole project exists to avoid (brief §0.3
    #2, F1).  ``AR_Barrel_Vladof`` becomes ``AR_Barrel_Vladof_PL``, then
    ``..._PL2`` and so on until the name is free in the fragment table.
    """
    taken = set(load_fragment_rows(fragments_table))
    candidate = f"{template}_PL"
    suffix = 2
    while candidate in taken:
        candidate = f"{template}_PL{suffix}"
        suffix += 1
    return candidate


def check_fragment_name(name: str, template: str,
                        fragments_table: str | Path | None = None) -> Check:
    """Guard the unique-name rule before a package is written."""
    if not name or not name.replace("_", "").isalnum():
        return Check("fragment name", "error",
                     f"{name!r} is not a usable object name (letters, digits, underscores)")
    if name == template:
        return Check("fragment name", "error",
                     f"{name!r} is the template fragment; a new part needs a NEW name (F1)")
    if name in load_fragment_rows(fragments_table):
        return Check("fragment name", "error",
                     f"{name!r} already exists in the gestalt fragment table (F1)")
    return Check("fragment name", "ok", f"{name!r} is unused")


# ---------------------------------------------------------------------------
# Blender: import
# ---------------------------------------------------------------------------


def _bone_matrices(mesh: SkeletalMeshExport):
    """Per-bone (local, world) Blender-space matrices from the RefSkeleton."""
    from mathutils import Matrix, Quaternion

    locals_: list = []
    worlds: list = []
    for i, bone in enumerate(mesh.bones):
        quat = Quaternion(_quat_ue_to_blender(bone.orientation))
        loc = ue_to_blender(*bone.position)
        local = Matrix.Translation(loc) @ quat.to_matrix().to_4x4()
        locals_.append(local)
        worlds.append(local if i == 0 else worlds[bone.parent_index] @ local)
    return locals_, worlds


def _socket_matrix(socket: SocketInfo):
    from mathutils import Matrix, Quaternion

    quat = Quaternion(_quat_ue_to_blender(_rotator_to_quat(socket.rotation)))
    loc = ue_to_blender(*socket.location)
    scale = Matrix.Diagonal((abs(socket.scale[0]) or 1.0, abs(socket.scale[1]) or 1.0,
                             abs(socket.scale[2]) or 1.0, 1.0))
    return Matrix.Translation(loc) @ quat.to_matrix().to_4x4() @ scale


def _new_collection(name: str):
    import bpy

    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    return collection


def _build_mesh_object(src: SourceFragment, name: str, collection):
    import bpy

    positions = [ue_to_blender(v.x, v.y, v.z)
                 for v in (src.mesh.lod0.vertices[i] for i in src.vertex_ids)]
    mesh_data = bpy.data.meshes.new(name)
    mesh_data.from_pydata(positions, [], src.triangles())
    mesh_data.update()
    obj = bpy.data.objects.new(name, mesh_data)
    collection.objects.link(obj)

    attr = mesh_data.attributes.new(ATTR_SRC_VID, "INT", "POINT")
    attr.data.foreach_set("value", src.vertex_ids)

    lod = src.mesh.lod0
    uv_sets = lod.num_tex_coords
    uv_layer = mesh_data.uv_layers.new(name=UV0_NAME)
    uvs = []
    for loop in mesh_data.loops:
        vertex = lod.vertices[src.vertex_ids[loop.vertex_index]]
        u, v = vertex.uv_floats(0)
        uvs.extend((u, 1.0 - v))  # UE's V runs downwards
    uv_layer.data.foreach_set("uv", uvs)

    for index in range(1, min(uv_sets, 4)):
        raw = mesh_data.attributes.new(RAW_UV_ATTRS[index - 1], "FLOAT2", "POINT")
        flat: list[float] = []
        for vid in src.vertex_ids:
            u, v = lod.vertices[vid].uv_floats(index)
            flat.extend((u, v))
        raw.data.foreach_set("vector", flat)
    mesh_data.update()
    return obj


def _build_armature(src: SourceFragment, name: str, collection):
    import bpy
    from mathutils import Matrix

    _, worlds = _bone_matrices(src.mesh)
    armature = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, armature)
    collection.objects.link(arm_obj)

    view_layer = bpy.context.view_layer
    previous = view_layer.objects.active
    view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    # Blender bones run along their local +Y; UE bones along local +X.
    axis_fix = Matrix.Rotation(math.radians(-90.0), 4, "Z")
    lengths: list[float] = []
    for i, bone in enumerate(src.mesh.bones):
        children = [j for j, b in enumerate(src.mesh.bones) if b.parent_index == i and j != i]
        if children:
            head = worlds[i].translation
            lengths.append(max(
                0.5, min(12.0, min((worlds[c].translation - head).length for c in children))))
        else:
            lengths.append(2.0)
    edit_bones = []
    for i, bone in enumerate(src.mesh.bones):
        eb = armature.edit_bones.new(bone.name.text)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, 1.0, 0.0)
        eb.matrix = worlds[i] @ axis_fix
        eb.length = lengths[i]
        edit_bones.append(eb)
    for i, bone in enumerate(src.mesh.bones):
        if i != 0 and 0 <= bone.parent_index < len(edit_bones):
            edit_bones[i].parent = edit_bones[bone.parent_index]
            edit_bones[i].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = previous
    return arm_obj, worlds


def _assign_weights(obj, src: SourceFragment) -> None:
    lod = src.mesh.lod0
    bone_names = src.mesh.bone_names
    groups = {name: obj.vertex_groups.new(name=name) for name in bone_names}
    for local_index, vid in enumerate(src.vertex_ids):
        vertex = lod.vertices[vid]
        bone_map = lod.chunk_for_vertex_index(vid).bone_map
        for slot in range(4):
            weight = vertex.bone_wt[slot]
            if not weight:
                continue
            bone_index = bone_map[vertex.bone_idx[slot]]
            groups[bone_names[bone_index]].add([local_index], weight / 255.0, "REPLACE")


def _build_sockets(src: SourceFragment, arm_obj, worlds, collection, prefix: str) -> list[str]:
    import bpy

    by_name = {socket.name: socket for socket in src.sockets}
    bone_index = {bone.name.text: i for i, bone in enumerate(src.mesh.bones)}
    created: list[str] = []
    for socket_name in src.expected_sockets:
        socket = by_name.get(socket_name)
        if socket is None:
            continue
        empty = bpy.data.objects.new(socket_name, None)
        empty.empty_display_type = "ARROWS"
        empty.empty_display_size = 4.0
        collection.objects.link(empty)
        empty[P_ROLE] = "socket"
        empty[P_FRAGMENT] = prefix
        empty["bl2_socket"] = socket_name
        empty["bl2_bone"] = socket.bone
        empty["bl2_relative_location"] = list(socket.location)
        empty["bl2_relative_rotation"] = list(socket.rotation)
        if socket.bone in bone_index and socket.bone in arm_obj.data.bones:
            empty.parent = arm_obj
            empty.parent_type = "BONE"
            empty.parent_bone = socket.bone
            bpy.context.view_layer.update()
            empty.matrix_world = worlds[bone_index[socket.bone]] @ _socket_matrix(socket)
        else:
            empty.matrix_world = _socket_matrix(socket)
        # Blender may have uniquified the name (a second import of the same
        # fragment); remember what the object is actually called.
        created.append(empty.name)
    bpy.context.view_layer.update()
    return created


def _import_checks(src: SourceFragment, obj, arm_obj, socket_names: list[str]) -> list[Check]:
    checks: list[Check] = []
    bones = src.mesh.bone_names
    armature_names = [b.name for b in arm_obj.data.bones]
    checks.append(Check(
        "bone count", "ok" if len(bones) == len(armature_names) else "error",
        f"{len(armature_names)} bones in the armature, {len(bones)} in the RefSkeleton"))
    checks.append(Check(
        "bone names", "ok" if armature_names == bones else "error",
        "armature bone names match the RefSkeleton order" if armature_names == bones
        else "armature bone names differ from the RefSkeleton"))
    vertex_count = len(obj.data.vertices)
    checks.append(Check(
        "fragment vertices",
        "ok" if vertex_count == len(src.vertex_ids) else "error",
        f"{vertex_count} vertices (fragment references {len(src.vertex_ids)})"))
    contiguous = src.vertex_ids == list(range(src.vertex_ids[0], src.vertex_ids[-1] + 1))
    checks.append(Check(
        "vertex range", "ok" if contiguous else "warn",
        f"source ids {src.vertex_ids[0]}..{src.vertex_ids[-1]}"
        + ("" if contiguous else " are not contiguous (fragment shares vertices)")))
    tri_count = len(obj.data.polygons)
    checks.append(Check(
        "triangles", "ok" if tri_count == src.num_primitives else "error",
        f"{tri_count} triangles (NumPrimitives {src.num_primitives})"))
    import bpy

    made = {str(bpy.data.objects[name].get("bl2_socket", name)) for name in socket_names}
    missing = [name for name in src.expected_sockets if name not in made]
    checks.append(Check(
        "sockets", "ok" if not missing and src.expected_sockets else
        ("warn" if not src.expected_sockets else "error"),
        f"{len(socket_names)}/{len(src.expected_sockets)} sockets"
        + (f"; missing {missing}" if missing else "")))
    used = {i for tri in obj.data.polygons for i in tri.vertices}
    checks.append(Check(
        "triangle indices", "ok" if len(used) == vertex_count else "warn",
        f"{len(used)} of {vertex_count} vertices are referenced by triangles"))
    return checks


def import_fragment(
    package_path: str | Path | None = None,
    mesh_path: str | None = None,
    fragment_name: str = DEFAULT_FRAGMENT,
    fragments_table: str | Path | None = None,
) -> ImportResult:
    """Build a Blender object (+ armature, sockets, collection) for one fragment."""
    import bpy

    src = read_source_fragment(package_path, mesh_path, fragment_name, fragments_table)

    collection = _new_collection(fragment_name)
    obj = _build_mesh_object(src, fragment_name, collection)
    arm_obj, worlds = _build_armature(src, f"{fragment_name}_Armature", collection)
    _assign_weights(obj, src)
    obj.parent = arm_obj
    modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = arm_obj
    socket_names = _build_sockets(src, arm_obj, worlds, collection, fragment_name)

    obj[P_PACKAGE] = str(src.package_path)
    obj[P_MESH_PATH] = src.mesh_path
    obj[P_FRAGMENT] = src.fragment
    obj[P_FIRST_INDEX] = src.first_index
    obj[P_NUM_PRIMITIVES] = src.num_primitives
    obj[P_VERTEX_COUNT] = len(src.vertex_ids)
    obj[P_SRC_VIDS] = src.vertex_ids
    obj[P_BONES] = json.dumps(src.bone_names)
    obj[P_SOCKETS] = json.dumps(socket_names)
    obj[P_ARMATURE] = arm_obj.name
    obj[P_TABLE] = str(src.fragments_table)
    obj[P_TRANSFORM] = TRANSFORM_NOTE
    obj[P_VERSION] = list(ADDON_VERSION)
    obj[P_ROLE] = "fragment"
    arm_obj[P_ROLE] = "armature"
    arm_obj[P_FRAGMENT] = src.fragment
    # new objects built from scratch are validated against the armature they are
    # parented to, so it has to carry the RefSkeleton bone list too
    arm_obj[P_BONES] = json.dumps(src.bone_names)
    arm_obj[P_PACKAGE] = str(src.package_path)
    arm_obj[P_MESH_PATH] = src.mesh_path
    arm_obj[P_TABLE] = str(src.fragments_table)

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    return ImportResult(
        object_name=obj.name,
        armature_name=arm_obj.name,
        collection_name=collection.name,
        package=str(src.package_path),
        mesh_path=src.mesh_path,
        fragment=src.fragment,
        first_index=src.first_index,
        num_primitives=src.num_primitives,
        vertex_count=len(obj.data.vertices),
        triangle_count=len(obj.data.polygons),
        bone_count=len(arm_obj.data.bones),
        socket_names=socket_names,
        checks=_import_checks(src, obj, arm_obj, socket_names),
    )


# ---------------------------------------------------------------------------
# Blender: edit helpers
# ---------------------------------------------------------------------------


def standard_deformation(obj=None, y_cut: float = -80.0, dz: float = 8.0,
                         x_scale: float = 1.5) -> int:
    """The D7 edit, in Blender coordinates (y and z mean what they mean in UE).

    Every vertex in front of ``y_cut`` (the muzzle half) moves ``+dz`` in Z and
    its X is scaled by ``x_scale``.  Returns the number of vertices moved.
    """
    obj = _resolve_object(obj)
    moved = 0
    for vertex in obj.data.vertices:
        if vertex.co.y < y_cut:
            vertex.co.z += dz
            vertex.co.x *= x_scale
            moved += 1
    obj.data.update()
    return moved


def ensure_triangulated(obj=None) -> int:
    """Triangulate the mesh as geometry (brief §0.5 / F3).  Returns faces split."""
    import bmesh

    obj = _resolve_object(obj)
    mesh = obj.data
    ngons = [p for p in mesh.polygons if len(p.vertices) > 3]
    if not ngons:
        return 0
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(ngons)


def apply_transforms(obj=None) -> bool:
    """Bake the object's transform into the mesh; returns True if it did work."""
    from mathutils import Matrix

    obj = _resolve_object(obj)
    world = obj.matrix_world.copy()
    if _is_identity(world):
        return False
    obj.data.transform(world)
    obj.matrix_world = Matrix.Identity(4)
    obj.data.update()
    return True


def _is_identity(matrix) -> bool:
    from mathutils import Matrix

    identity = Matrix.Identity(4)
    return all(abs(matrix[r][c] - identity[r][c]) <= _EPS_IDENTITY
               for r in range(4) for c in range(4))


def _resolve_object(obj=None):
    import bpy

    if obj is None:
        obj = bpy.context.view_layer.objects.active
    if isinstance(obj, str):
        obj = bpy.data.objects[obj]
    if obj is None or obj.type != "MESH":
        raise TypeError("expected an imported fragment mesh object")
    return obj


# ---------------------------------------------------------------------------
# Blender: validation
# ---------------------------------------------------------------------------


def _stored_src_vids(obj) -> list[int]:
    return [int(v) for v in obj.get(P_SRC_VIDS, [])]


def _attribute_src_vids(obj) -> list[int] | None:
    attr = obj.data.attributes.get(ATTR_SRC_VID)
    if attr is None or attr.domain != "POINT" or attr.data_type != "INT":
        return None
    out = [0] * len(obj.data.vertices)
    attr.data.foreach_get("value", out)
    return out


def _find_armature(obj):
    import bpy

    name = obj.get(P_ARMATURE)
    if name and name in bpy.data.objects:
        return bpy.data.objects[name]
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    for modifier in obj.modifiers:
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object
    return None


def is_imported_fragment(obj) -> bool:
    """True when the object came from :func:`import_fragment` (has provenance)."""
    return all(obj.get(key) not in (None, "") for key in (P_PACKAGE, P_MESH_PATH, P_FRAGMENT)) \
        and bool(_stored_src_vids(obj))


def _armature_bone_names(armature) -> list[str]:
    return [b.name for b in armature.data.bones]


def _weighted_group_names(obj) -> list[str]:
    """Vertex-group names that actually carry a non-zero weight."""
    names = {g.index: g.name for g in obj.vertex_groups}
    used: list[str] = []
    for vertex in obj.data.vertices:
        for item in vertex.groups:
            name = names.get(item.group)
            if name and item.weight > 0.0 and name not in used:
                used.append(name)
    return used


def _validate_new_object(obj) -> list[Check]:
    """Guardrails for a mesh that was never imported (M6: brand-new geometry).

    It carries no ``bl2_src_vid``, so nothing can be matched back to a source
    vertex; what it needs instead is the gestalt armature (so vertex groups name
    real bones) and a ``UV0`` map, and its faces must already be triangles.
    """
    checks: list[Check] = [
        Check("provenance", "ok",
              f"{obj.name}: new geometry (no {ATTR_SRC_VID}); exported with its own "
              "vertices, weights and UVs")
    ]
    armature = _find_armature(obj)
    if armature is None or armature.type != "ARMATURE":
        checks.append(Check("armature", "error",
                            "new geometry must be parented to (or modified by) the imported "
                            "gestalt armature; bone names cannot be resolved without it"))
        bone_names: list[str] = []
    else:
        bone_names = _armature_bone_names(armature)
        stored = json.loads(armature.get(P_BONES, "[]"))
        if stored and bone_names != stored:
            checks.append(Check("armature", "error",
                                f"armature bones changed since import ({len(bone_names)} now, "
                                f"{len(stored)} at import)"))
        else:
            checks.append(Check("armature", "ok", f"{armature.name}, {len(bone_names)} bones"))

    groups = _weighted_group_names(obj)
    unknown = [name for name in groups if bone_names and name not in bone_names]
    if not groups:
        checks.append(Check("bone weights", "error",
                            "no vertex group carries a weight; every vertex needs a bone"))
    elif unknown:
        checks.append(Check("bone weights", "error",
                            f"vertex group(s) {unknown} are not bones of the gestalt skeleton"))
    else:
        unweighted = sum(
            1 for v in obj.data.vertices if not any(g.weight > 0.0 for g in v.groups)
        )
        checks.append(Check("bone weights", "error" if unweighted else "ok",
                            f"{unweighted} unweighted vertices" if unweighted else
                            f"weighted to {', '.join(groups)}"))

    ngons = sum(1 for p in obj.data.polygons if len(p.vertices) != 3)
    checks.append(Check("triangulated", "ok" if ngons == 0 else "error",
                        "all faces are triangles" if ngons == 0 else
                        f"{ngons} non-triangle faces; run ensure_triangulated() (brief §0.5)"))

    has_uv0 = UV0_NAME in obj.data.uv_layers
    checks.append(Check("UV0", "ok" if has_uv0 else "error",
                        f"{len(obj.data.uv_layers)} UV map(s)" if has_uv0 else
                        f"no {UV0_NAME} map; the gestalt vertex buffer needs texture set 0"))

    count = len(obj.data.vertices)
    checks.append(Check("vertex count", "ok" if count < MAX_GPU_VERTICES else "error",
                        f"{count} vertices"
                        + ("" if count < MAX_GPU_VERTICES else
                           f" exceeds the uint16 index buffer ({MAX_GPU_VERTICES})")))
    checks.append(Check("triangle count", "ok" if obj.data.polygons else "error",
                        f"{len(obj.data.polygons)} triangles"))

    if _is_identity(obj.matrix_world):
        checks.append(Check("object transform", "ok", "identity"))
    else:
        loc, _rot, scale = obj.matrix_world.decompose()
        checks.append(Check("object transform", "warn",
                            f"unapplied transform: loc {tuple(round(v, 4) for v in loc)}, "
                            f"scale {tuple(round(v, 4) for v in scale)}; export bakes "
                            "matrix_world -- run apply_transforms() to make it explicit"))
    others = [m.name for m in obj.modifiers if m.type != "ARMATURE" and m.show_viewport]
    checks.append(Check("modifiers", "ok" if not others else "warn",
                        "only the armature modifier" if not others else
                        f"{others} are not applied and will not be exported"))
    return checks


def validate_for_export(obj=None) -> list[Check]:
    """Guardrails between an edited fragment and a package write.

    An object imported by the add-on is checked against its source fragment; a
    mesh that was **never** imported (M6 new geometry) is checked against the
    armature it is bound to instead (:func:`_validate_new_object`).
    """
    import bpy

    obj = _resolve_object(obj)
    checks: list[Check] = []

    missing = [key for key in (P_PACKAGE, P_MESH_PATH, P_FRAGMENT, P_SRC_VIDS)
               if obj.get(key) in (None, "")]
    if missing:
        if len(missing) == 4:
            return _validate_new_object(obj)
        checks.append(Check("provenance", "error",
                            f"object is missing {missing}; re-import the fragment"))
        return checks
    checks.append(Check("provenance", "ok",
                        f"{obj[P_FRAGMENT]} from {obj[P_MESH_PATH]}"))

    package = Path(obj[P_PACKAGE])
    checks.append(Check("source package", "ok" if package.exists() else "error",
                        str(package) + ("" if package.exists() else " is missing")))

    stored = _stored_src_vids(obj)
    count = len(obj.data.vertices)
    expected = int(obj.get(P_VERTEX_COUNT, len(stored)))
    checks.append(Check("vertex count", "ok" if count == expected else "error",
                        f"{count} vertices (import had {expected})"))

    attr_ids = _attribute_src_vids(obj)
    if attr_ids is None:
        checks.append(Check(ATTR_SRC_VID, "error",
                            "int POINT attribute is missing; export cannot match vertices"))
    elif len(set(attr_ids)) != len(attr_ids):
        checks.append(Check(ATTR_SRC_VID, "error", "duplicate source vertex ids"))
    elif sorted(attr_ids) != sorted(stored):
        checks.append(Check(ATTR_SRC_VID, "error",
                            "source vertex ids no longer match the imported set"))
    else:
        checks.append(Check(ATTR_SRC_VID, "ok", f"{len(attr_ids)} ids intact"))

    ngons = sum(1 for p in obj.data.polygons if len(p.vertices) != 3)
    checks.append(Check("triangulated", "ok" if ngons == 0 else "error",
                        "all faces are triangles" if ngons == 0 else
                        f"{ngons} non-triangle faces; run ensure_triangulated() (brief §0.5)"))

    tris = len(obj.data.polygons)
    expected_tris = int(obj.get(P_NUM_PRIMITIVES, tris))
    checks.append(Check("triangle count", "ok" if tris == expected_tris else "warn",
                        f"{tris} triangles (template fragment has {expected_tris}; "
                        "the exported fragment always reuses the template's index list)"))

    if _is_identity(obj.matrix_world):
        checks.append(Check("object transform", "ok", "identity"))
    else:
        loc, rot, scale = obj.matrix_world.decompose()
        uniform = abs(scale.x - scale.y) < 1e-6 and abs(scale.y - scale.z) < 1e-6
        detail = (f"loc {tuple(round(v, 4) for v in loc)}, "
                  f"scale {tuple(round(v, 4) for v in scale)}"
                  f"{'' if uniform else ' (non-uniform)'}")
        checks.append(Check("object transform", "warn",
                            f"unapplied transform: {detail}; export bakes matrix_world -- "
                            "run apply_transforms() to make it explicit"))

    others = [m.name for m in obj.modifiers if m.type != "ARMATURE" and m.show_viewport]
    checks.append(Check("modifiers", "ok" if not others else "warn",
                        "only the armature modifier" if not others else
                        f"{others} are not applied and will not be exported"))

    armature = _find_armature(obj)
    stored_bones = json.loads(obj.get(P_BONES, "[]"))
    if armature is None:
        checks.append(Check("armature", "error", "the imported armature is gone"))
    else:
        names = [b.name for b in armature.data.bones]
        same = names == stored_bones
        checks.append(Check("armature", "ok" if same else "error",
                            f"{len(names)} bones unchanged" if same else
                            f"bones changed ({len(names)} now, {len(stored_bones)} at import)"))
        posed = [b.name for b in armature.pose.bones
                 if not _is_identity(b.matrix_basis)] if armature.pose else []
        if posed:
            checks.append(Check("armature pose", "warn",
                                f"{len(posed)} posed bones are ignored; export reads rest geometry"))

    stored_sockets = json.loads(obj.get(P_SOCKETS, "[]"))
    # look only at this fragment's own empties: a second import of the same
    # fragment makes same-named objects, which would mask a deletion
    own = {child.name for child in (armature.children if armature is not None else [])}
    own |= {o.name for o in bpy.data.objects if o.parent is obj}
    present = [name for name in stored_sockets if name in own]
    gone = [name for name in stored_sockets if name not in present]
    checks.append(Check("sockets", "ok" if not gone else "error",
                        f"{len(present)}/{len(stored_sockets)} socket empties present"
                        + (f"; missing/renamed {gone}" if gone else "")))
    return checks


# ---------------------------------------------------------------------------
# Blender: export
# ---------------------------------------------------------------------------


def _blender_normals(mesh) -> list[tuple[float, float, float]]:
    """Per-vertex normals, averaged from corner normals when available."""
    count = len(mesh.vertices)
    sums = [[0.0, 0.0, 0.0] for _ in range(count)]
    try:
        raw = [0.0] * (len(mesh.loops) * 3)
        mesh.corner_normals.foreach_get("vector", raw)
        for loop_index, loop in enumerate(mesh.loops):
            acc = sums[loop.vertex_index]
            acc[0] += raw[3 * loop_index]
            acc[1] += raw[3 * loop_index + 1]
            acc[2] += raw[3 * loop_index + 2]
    except Exception:  # corner_normals is version-sensitive; vertex_normals is not
        sums = []
    out: list[tuple[float, float, float]] = []
    for i in range(count):
        vec = sums[i] if sums else list(mesh.vertex_normals[i].vector)
        length = math.sqrt(sum(c * c for c in vec))
        if length < 1e-8:
            vec = list(mesh.vertex_normals[i].vector)
            length = math.sqrt(sum(c * c for c in vec)) or 1.0
        out.append((vec[0] / length, vec[1] / length, vec[2] / length))
    return out


def _blender_tangents(mesh) -> list[tuple[float, float, float]] | None:
    """Per-vertex tangents from UV0, or None when they cannot be computed."""
    if not mesh.uv_layers:
        return None
    uv_name = UV0_NAME if UV0_NAME in mesh.uv_layers else mesh.uv_layers[0].name
    try:
        mesh.calc_tangents(uvmap=uv_name)
    except Exception:
        return None
    sums = [[0.0, 0.0, 0.0] for _ in range(len(mesh.vertices))]
    for loop in mesh.loops:
        acc = sums[loop.vertex_index]
        tangent = loop.tangent
        acc[0] += tangent[0]
        acc[1] += tangent[1]
        acc[2] += tangent[2]
    out: list[tuple[float, float, float]] = []
    for vec in sums:
        length = math.sqrt(sum(c * c for c in vec))
        out.append((0.0, 0.0, 0.0) if length < 1e-8
                   else (vec[0] / length, vec[1] / length, vec[2] / length))
    try:
        mesh.free_tangents()
    except AttributeError:
        pass
    return out


def _vertex_uv0(mesh) -> list[tuple[float, float]]:
    """One (u, v) per vertex, taken from the first loop that uses it."""
    out = [(0.0, 0.0)] * len(mesh.vertices)
    layer = mesh.uv_layers.get(UV0_NAME) or (mesh.uv_layers[0] if mesh.uv_layers else None)
    if layer is None:
        return out
    seen = [False] * len(mesh.vertices)
    for loop in mesh.loops:
        index = loop.vertex_index
        if seen[index]:
            continue
        uv = layer.data[loop.index].uv
        out[index] = (uv[0], uv[1])
        seen[index] = True
    return out


def _vertex_raw_uvs(mesh) -> dict[int, list[tuple[float, float]]]:
    """``uv set -> per-vertex raw (u, v)`` from the bl2_uv1..3 point attributes."""
    out: dict[int, list[tuple[float, float]]] = {}
    for offset, name in enumerate(RAW_UV_ATTRS, start=1):
        attr = mesh.attributes.get(name)
        if attr is None or attr.domain != "POINT" or attr.data_type != "FLOAT2":
            continue
        flat = [0.0] * (len(mesh.vertices) * 2)
        attr.data.foreach_get("vector", flat)
        out[offset] = [(flat[2 * i], flat[2 * i + 1]) for i in range(len(mesh.vertices))]
    return out


def _bone_weights(obj, bone_names: list[str]) -> list[tuple[list[str], list[int]]]:
    """Per vertex: the top four (bone name, 0..255 weight) influences, normalised."""
    names = {g.index: g.name for g in obj.vertex_groups}
    known = set(bone_names)
    unknown: set[str] = set()
    out: list[tuple[list[str], list[int]]] = []
    for vertex in obj.data.vertices:
        influences = []
        for item in vertex.groups:
            name = names.get(item.group)
            if name is None or item.weight <= 0.0:
                continue
            if name not in known:
                unknown.add(name)
                continue
            influences.append((float(item.weight), name))
        influences.sort(key=lambda pair: (-pair[0], pair[1]))
        influences = influences[:4]
        total = sum(w for w, _ in influences)
        if not influences or total <= 0.0:
            raise ExportBlocked([Check("bone weights", "error",
                                       f"{obj.name}: vertex {len(out)} has no bone influence")])
        weights = [int(round(255.0 * w / total)) for w, _ in influences]
        drift = 255 - sum(weights)
        weights[0] = max(0, min(255, weights[0] + drift))
        out.append(([name for _, name in influences], weights))
    if unknown:
        raise ExportBlocked([Check("bone weights", "error",
                                   f"{obj.name}: vertex group(s) {sorted(unknown)} are not bones "
                                   "of the gestalt skeleton")])
    return out


def _gather_new_geometry(obj, bone_names: list[str], uv_sets: int):
    """A never-imported mesh -> (``NewVertex`` list, triangle list) in UE space."""
    mesh = obj.data
    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted_safe().transposed()
    normals = _blender_normals(mesh)
    tangents = _blender_tangents(mesh)
    uv0 = _vertex_uv0(mesh)
    raw_uvs = _vertex_raw_uvs(mesh)
    weights = _bone_weights(obj, bone_names)

    vertices: list[NewVertex] = []
    for index, vertex in enumerate(mesh.vertices):
        co = matrix @ vertex.co
        position = blender_to_ue(co.x, co.y, co.z)
        normal = (normal_matrix @ _as_vector(normals[index])).normalized()
        tangent_z = pack_normal(blender_to_ue(normal.x, normal.y, normal.z),
                                (DEFAULT_TANGENT_Z >> 24) & 0xFF)
        tangent_x = DEFAULT_TANGENT_X
        if tangents is not None:
            tangent = normal_matrix @ _as_vector(tangents[index])
            if tangent.length > 1e-8:
                tangent = tangent.normalized()
                tangent_x = pack_normal(blender_to_ue(tangent.x, tangent.y, tangent.z),
                                        (DEFAULT_TANGENT_X >> 24) & 0xFF)
        u, v = uv0[index]
        uvs = [(float_to_half(u), float_to_half(1.0 - v))]  # UE's V runs downwards
        for slot in range(1, uv_sets):
            pair = raw_uvs.get(slot)
            uvs.append((float_to_half(pair[index][0]), float_to_half(pair[index][1]))
                       if pair is not None else (0, 0))
        bones, bone_weights = weights[index]
        vertices.append(NewVertex(
            x=position[0], y=position[1], z=position[2],
            tangent_x=tangent_x, tangent_z=tangent_z,
            bones=bones, weights=bone_weights, uvs=uvs,
        ))
    triangles = [tuple(int(i) for i in poly.vertices) for poly in mesh.polygons]
    return vertices, triangles


def _gather_copy_edit(obj, source_vertices, stats: dict):
    """An imported fragment -> the ``VertexEdit`` that writes its Blender state back."""
    mesh = obj.data
    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted_safe().transposed()
    attr_ids = _attribute_src_vids(obj) or []
    normals = _blender_normals(mesh)
    tangents = _blender_tangents(mesh)

    positions: dict[int, tuple[float, float, float]] = {}
    new_normals: dict[int, tuple[float, float, float]] = {}
    new_tangents: dict[int, tuple[float, float, float]] = {}
    for local_index, vid in enumerate(attr_ids):
        co = matrix @ mesh.vertices[local_index].co
        positions[vid] = blender_to_ue(co.x, co.y, co.z)
        normal = (normal_matrix @ _as_vector(normals[local_index])).normalized()
        new_normals[vid] = blender_to_ue(normal.x, normal.y, normal.z)
        if tangents is not None:
            tangent = normal_matrix @ _as_vector(tangents[local_index])
            if tangent.length > 1e-8:
                tangent = tangent.normalized()
                new_tangents[vid] = blender_to_ue(tangent.x, tangent.y, tangent.z)

    def edit(vertex, src_vid: int, _new_vid: int) -> bool:
        target = positions.get(src_vid)
        if target is None:
            return False
        original = source_vertices[src_vid]
        delta = max(abs(target[0] - original.x), abs(target[1] - original.y),
                    abs(target[2] - original.z))
        stats["max_delta"] = max(stats["max_delta"], delta)
        stats["max_dz"] = max(stats["max_dz"], target[2] - original.z)
        vertex.x, vertex.y, vertex.z = target
        normal = new_normals.get(src_vid)
        if normal is not None:
            source_normal = unpack_normal(original.tangent_z)
            stats["dot"] += sum(a * b for a, b in zip(normal, source_normal[:3]))
            stats["n"] += 1
            vertex.tangent_z = pack_normal(normal, (original.tangent_z >> 24) & 0xFF)
        tangent = new_tangents.get(src_vid)
        if tangent is not None:
            vertex.tangent_x = pack_normal(tangent, (original.tangent_x >> 24) & 0xFF)
        return delta > 1e-9

    return edit


def _export_source(objs):
    """Source package / mesh path / fragment table for a batch of objects.

    Taken from the first object (or armature) the add-on imported; a batch of
    nothing but new meshes falls back to the add-on defaults.
    """
    for obj in objs:
        candidates = [obj]
        armature = _find_armature(obj)
        if armature is not None:
            candidates.append(armature)
        for candidate in candidates:
            if candidate.get(P_PACKAGE) and candidate.get(P_MESH_PATH):
                return (str(candidate[P_PACKAGE]), str(candidate[P_MESH_PATH]),
                        candidate.get(P_TABLE) or None)
    return (str(default_package()), DEFAULT_MESH_PATH, None)


#: sidecar keys written at the top level, for the M2 SDK mod and bl2_lint
SIDECAR_KEYS = ("package", "mesh_path", "fragment", "template_fragment", "first_index",
                "num_primitives", "dz", "part_name", "new_vertices", "reparsed_ok", "out",
                "total_vertices", "total_indices", "fragments")


def export_fragments(
    objs,
    out_package_path: str | Path | None = None,
    new_package_name: str = DEFAULT_PACKAGE_NAME,
    new_mesh_name: str = DEFAULT_MESH_NAME,
    names: list[str] | None = None,
    template_fragments: list[str] | None = None,
    *,
    part_names: list[str] | None = None,
    socket_dz: float | None = None,
    force: bool = False,
) -> ExportResult:
    """Write ONE new package holding **several** new fragments, in order.

    Each object is either an imported fragment (its edited positions are written
    over a copy of its template's vertices -- the M2/M4 shape) or a mesh that was
    never imported: new geometry with its own vertices, bone weights and UVs.
    Both kinds may appear in the same call; the fragments tile onto the end of
    LOD0 in the order given.  The source package is never touched (F12).
    """
    objs = [_resolve_object(obj) for obj in objs]
    if not objs:
        raise ValueError("export_fragments needs at least one object")
    if names is None:
        names = [suggest_fragment_name(str(obj.get(P_FRAGMENT, DEFAULT_FRAGMENT)))
                 for obj in objs]
    if len(names) != len(objs):
        raise ValueError(f"{len(objs)} objects but {len(names)} fragment names")
    if template_fragments is None:
        template_fragments = [str(obj.get(P_FRAGMENT) or DEFAULT_FRAGMENT) for obj in objs]
    if len(template_fragments) != len(objs):
        raise ValueError(f"{len(objs)} objects but {len(template_fragments)} template fragments")
    if part_names is None:
        part_names = list(names)

    package_path, mesh_path, table = _export_source(objs)
    checks: list[Check] = []
    for obj, name, template in zip(objs, names, template_fragments):
        checks.extend(validate_for_export(obj))
        checks.append(check_fragment_name(name, template, table))
        if is_imported_fragment(obj) and str(obj[P_FRAGMENT]) != template:
            checks.append(Check("template fragment", "error",
                                f"{obj.name} was imported from {obj[P_FRAGMENT]!r}, so it cannot "
                                f"be exported against template {template!r}"))
    if len(set(names)) != len(names):
        checks.append(Check("fragment name", "error", f"duplicate fragment names in {names}"))
    if not checks_ok(checks) and not force:
        raise ExportBlocked(checks)

    out_path = Path(out_package_path or default_out_package())
    src = read_source_fragment(package_path, mesh_path, template_fragments[0], table)
    bone_names = src.mesh.bone_names
    uv_sets = src.mesh.lod0.num_tex_coords

    stats = [{"max_delta": 0.0, "max_dz": 0.0, "dot": 0.0, "n": 0} for _ in objs]
    specs: list[FragmentSpec] = []
    for index, (obj, name, template) in enumerate(zip(objs, names, template_fragments)):
        if is_imported_fragment(obj):
            specs.append(FragmentSpec(
                name=name,
                template_fragment=template,
                edit=_gather_copy_edit(obj, src.mesh.lod0.vertices, stats[index]),
                part_name=part_names[index],
            ))
        else:
            vertices, triangles = _gather_new_geometry(obj, bone_names, uv_sets)
            specs.append(FragmentSpec(
                name=name,
                template_fragment=template,
                vertices=vertices,
                triangles=triangles,
                part_name=part_names[index],
            ))

    info = build_multi_fragment_package(
        out_path,
        specs,
        source_package=src.package_path,
        source_mesh=src.mesh_path,
        package_name=new_package_name,
        mesh_name=new_mesh_name,
        fragments_table=src.fragments_table,
        write_sidecar=False,
    )
    # `edit` only runs inside the build, so the measured Z offsets are known just
    # now; patch them into the sidecar before it is written.
    for index, entry in enumerate(info["fragments"]):
        entry["dz"] = (float(socket_dz) if socket_dz is not None
                       else round(max(0.0, stats[index]["max_dz"]), 4))
    info["dz"] = info["fragments"][0]["dz"]
    sidecar_path = Path(info["sidecar"])
    sidecar_path.write_text(json.dumps({k: info[k] for k in SIDECAR_KEYS}, indent=1))

    checks.append(Check("written", "ok" if info["reparsed_ok"] else "error",
                        f"{info['total_vertices']} verts / {info['total_indices']} indices, "
                        f"{len(specs)} fragment(s) in {out_path}"))
    measured = [s for s in stats if s["n"]]
    return ExportResult(
        out_package=str(out_path),
        sidecar=str(sidecar_path),
        package_name=new_package_name,
        mesh_path=info["mesh_path"],
        fragment=info["fragment"],
        template_fragment=info["template_fragment"],
        part_name=info["part_name"],
        first_index=info["first_index"],
        num_primitives=info["num_primitives"],
        new_vertices=info["new_vertices"],
        moved=info["moved"],
        total_vertices=info["total_vertices"],
        total_indices=info["total_indices"],
        dz=info["dz"],
        max_delta=round(max((s["max_delta"] for s in stats), default=0.0), 6),
        normal_agreement=(round(sum(s["dot"] for s in measured)
                                / sum(s["n"] for s in measured), 4) if measured else 0.0),
        fragments=[dict(entry) for entry in info["fragments"]],
        checks=checks,
    )


def export_fragment(
    obj=None,
    out_package_path: str | Path | None = None,
    new_package_name: str = DEFAULT_PACKAGE_NAME,
    new_mesh_name: str = DEFAULT_MESH_NAME,
    new_fragment_name: str = DEFAULT_NEW_FRAGMENT,
    socket_dz: float | None = None,
    *,
    part_name: str | None = None,
    force: bool = False,
) -> ExportResult:
    """Write a NEW package: the source mesh cloned, with this object appended.

    The single-object form of :func:`export_fragments`, which it delegates to.
    Never touches the source package (F12) and never replaces the template
    fragment (M2 shape) -- the edit always lands as an extra fragment whose
    triangle range is appended at the end of LOD0.
    """
    obj = _resolve_object(obj)
    template = str(obj.get(P_FRAGMENT) or DEFAULT_FRAGMENT)
    return export_fragments(
        [obj],
        out_package_path,
        new_package_name,
        new_mesh_name,
        [new_fragment_name],
        [template],
        part_names=[part_name or new_fragment_name],
        socket_dz=socket_dz,
        force=force,
    )


def _as_vector(triple):
    from mathutils import Vector

    return Vector(triple)
