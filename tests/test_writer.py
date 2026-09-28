"""Tests for the from-scratch package writer (``bl2_upk.writer``).

Runnable either with pytest or directly::

    python tests/test_writer.py

Checks fall into three groups:

a. the written package re-reads with our own reader and every payload decodes;
b. Gildor's umodel -- the only pre-game loader we have -- lists the objects and
   exports a glTF identical to the one it makes from stock ``Startup.upk``;
c. the ``mutate`` hook bends a barrel and the change survives both round trips.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_upk import (  # noqa: E402
    Package,
    PackageBuilder,
    SkeletalMeshExport,
    WriterError,
    clone_skeletal_mesh,
    find_property,
    parse_properties,
    qualified_path,
)
from bl2_upk.writer import (  # noqa: E402
    NAME_FLAGS,
    OBJECT_FLAGS_SUB_OBJECT,
    OBJECT_FLAGS_TOP_LEVEL,
    PACKAGE_FLAGS_COOKED_CONTENT,
    PKG_COMPRESSION_BITS,
)

SOURCE = REPO / "scratch" / "decomp" / "Startup.upk"
#: every test here clones from the real, decompressed Startup.upk (game data, never committed)
pytestmark = pytest.mark.skipif(not SOURCE.exists(),
                                reason="scratch/decomp/Startup.upk absent (bl2 doctor --fix)")
SOURCE_MESH = "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"
REFERENCE_GLTF = (
    REPO / "scratch" / "umodel_out" / "Startup" / "SkeletalMesh3"
    / "GestaltDef_AssaultRifle_GestaltSkeletalMesh.gltf"
)
UMODEL = REPO / "tools" / "gildor" / "umodel_64.exe"

CLONE_PACKAGE_NAME = "PipelineMeshes"
CLONE_OUT = REPO / "scratch" / "PipelineMeshes_clone.upk"
M1_PACKAGE_NAME = "PipelineMeshes_m1"
M1_OUT = REPO / "scratch" / "PipelineMeshes_m1.upk"
MESH_NAME = "PL_AR_Gestalt_Mesh"

EXPECTED_VERTICES = 27211
EXPECTED_INDICES = 68085
EXPECTED_BONES = 38
EXPECTED_SOCKETS = 48

#: ``AR_Barrel_Vladof`` in ``scratch/gestalt_AR_fragments.txt``.
FRAGMENT = "AR_Barrel_Vladof"
FRAG_FIRST_INDEX = 23484
FRAG_NUM_PRIMITIVES = 1164
Y_CUT = -80.0
DZ = 8.0
X_SCALE = 1.5
EXPECTED_MOVED = 281

_I32 = struct.Struct("<i")

#: Extra detail collected while running, printed by the script entry point.
NOTES: dict[str, str] = {}

_CACHE: dict[str, object] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def source_package() -> Package:
    pkg = _CACHE.get("src")
    if pkg is None:
        pkg = Package.from_file(SOURCE)
        _CACHE["src"] = pkg
    return pkg  # type: ignore[return-value]


def fragment_vertex_ids(lod) -> list[int]:
    end = FRAG_FIRST_INDEX + 3 * FRAG_NUM_PRIMITIVES
    return sorted(set(lod.indices[FRAG_FIRST_INDEX:end]))


def bend_barrel(mesh: SkeletalMeshExport) -> int:
    """The ``mutate`` callback: lift and widen the forward half of one fragment."""
    lod = mesh.lod0
    moved = 0
    for i in fragment_vertex_ids(lod):
        vertex = lod.vertices[i]
        if vertex.y < Y_CUT:
            vertex.z += DZ
            vertex.x *= X_SCALE
            moved += 1
    return moved


def build_clone() -> Path:
    if "clone" not in _CACHE:
        result = clone_skeletal_mesh(
            source_package(), SOURCE_MESH, CLONE_OUT, CLONE_PACKAGE_NAME, MESH_NAME
        )
        _CACHE["clone"] = result
    return CLONE_OUT


def build_m1() -> Path:
    if "m1" not in _CACHE:
        counter: list[int] = []
        result = clone_skeletal_mesh(
            source_package(),
            SOURCE_MESH,
            M1_OUT,
            M1_PACKAGE_NAME,
            MESH_NAME,
            mutate=lambda mesh: counter.append(bend_barrel(mesh)),
        )
        _CACHE["m1"] = result
        _CACHE["m1_moved"] = counter[0] if counter else 0
    return M1_OUT


def open_mesh(path: Path) -> tuple[Package, SkeletalMeshExport]:
    pkg = Package.from_file(path)
    entry = pkg.find_export(MESH_NAME, "SkeletalMesh")
    return pkg, SkeletalMeshExport.parse(pkg.read_export_bytes(entry), pkg)


def socket_properties(pkg: Package, entry) -> dict[str, object]:
    raw = pkg.read_export_bytes(entry)
    tags, end = parse_properties(raw, 4, pkg)
    assert end == len(raw)
    out: dict[str, object] = {"_net_index": _I32.unpack_from(raw, 0)[0]}
    for tag in tags:
        out[tag.name.text] = tag.value
    return out


def umodel_workspace(package_path: Path, deployed_name: str) -> Path:
    """Copy one package into an otherwise empty directory, named as it would
    be deployed (umodel keys the package name off the file name)."""
    key = f"ws:{deployed_name}"
    cached = _CACHE.get(key)
    if cached is None:
        cached = Path(tempfile.mkdtemp(prefix="bl2upk_"))
        shutil.copyfile(package_path, cached / f"{deployed_name}.upk")
        _CACHE[key] = cached
    return cached  # type: ignore[return-value]


def run_umodel(*args: str) -> str:
    proc = subprocess.run(
        [str(UMODEL), *args], capture_output=True, text=True, cwd=str(REPO)
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise AssertionError(f"umodel exited {proc.returncode}:\n{output}")
    for line in output.splitlines():
        low = line.lower()
        if low.startswith("error") or "bad command line" in low or "fatal" in low:
            raise AssertionError(f"umodel rejected the package:\n{output}")
    return output


def _position_accessor(data: dict) -> dict:
    for accessor in data["accessors"]:
        if accessor.get("type") == "VEC3" and accessor.get("min"):
            return accessor
    raise AssertionError("glTF has no POSITION accessor with bounds")


def gltf_position_bounds(gltf_path: Path) -> tuple[list[float], list[float], int, int]:
    data = json.loads(gltf_path.read_text())
    accessor = _position_accessor(data)
    joints = len(data["skins"][0]["joints"])
    return accessor["min"], accessor["max"], accessor["count"], joints


def gltf_positions(gltf_path: Path) -> list[tuple[float, float, float]]:
    """Decode the POSITION accessor out of the glTF's companion .bin."""
    data = json.loads(gltf_path.read_text())
    accessor = _position_accessor(data)
    view = data["bufferViews"][accessor["bufferView"]]
    blob = (gltf_path.parent / data["buffers"][0]["uri"]).read_bytes()
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"]
    return [
        struct.unpack_from("<3f", blob, start + 12 * i) for i in range(count)
    ]


def umodel_gltf(package_path: Path, deployed_name: str) -> Path:
    """Run ``umodel -export -gltf`` and return the produced .gltf path."""
    key = f"gltf:{deployed_name}"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    workspace = umodel_workspace(package_path, deployed_name)
    out_dir = Path(tempfile.mkdtemp(prefix="bl2upk_out_"))
    run_umodel(
        "-export", "-game=border", "-gltf",
        f"-path={workspace}", f"-out={out_dir}",
        f"{deployed_name}.upk", MESH_NAME,
    )
    hits = list(out_dir.rglob(f"{MESH_NAME}.gltf"))
    assert hits, f"umodel wrote no {MESH_NAME}.gltf under {out_dir}"
    _CACHE[key] = hits[0]
    return hits[0]


# ---------------------------------------------------------------------------
# a. our own reader
# ---------------------------------------------------------------------------


def test_summary_matches_derived_layout() -> None:
    path = build_clone()
    pkg = Package.from_file(path)
    s = pkg.summary
    assert (s.version, s.licensee) == (832, 46)
    assert s.package_group == "None"
    assert s.package_flags == PACKAGE_FLAGS_COOKED_CONTENT
    assert s.package_flags & PKG_COMPRESSION_BITS == 0
    assert s.compression_flags == 0 and s.chunk_count == 0
    assert (s.engine_version, s.cooker_version) == (1712575, 134)
    assert s.guid != b"\0" * 16

    # Table order and adjacency, exactly as the templates lay it out:
    # summary | names | imports | exports | depends | payloads.
    assert s.name_offset < s.import_offset < s.export_offset < s.depends_offset
    assert s.export_offset == s.import_offset + 28 * s.import_count
    assert s.depends_offset == s.export_offset + 68 * s.export_count
    assert s.depends_offset + 4 * s.export_count == s.headers_size
    assert pkg.data[s.depends_offset : s.headers_size] == b"\0" * (4 * s.export_count)

    # Payloads start at HeadersSize and are contiguous to EOF, with no padding.
    entries = sorted(pkg.exports, key=lambda e: e.off)
    cursor = s.headers_size
    for entry in entries:
        assert entry.off == cursor, f"{entry.path} starts at {entry.off}, expected {cursor}"
        cursor += entry.size
    assert cursor == s.file_size == path.stat().st_size

    # Generation: 1 entry, (ExportCount, NameCount, NetObjectCount) -- and like
    # Startup.upk, NetObjectCount is 0 even though the payloads carry NetIndexes.
    pos = 12 + 4 + len(s.package_group) + 1 + 4 + 28 + 16 + 16
    (gen_count,) = _I32.unpack_from(pkg.data, pos)
    assert gen_count == 1
    gen = struct.unpack_from("<3i", pkg.data, pos + 4)
    assert gen == (s.export_count, s.name_count, 0)
    NOTES["summary"] = (
        f"headers {s.headers_size} B, {s.name_count} names, {s.import_count} imports, "
        f"{s.export_count} exports, file {s.file_size} B"
    )


def test_name_and_import_tables() -> None:
    path = build_clone()
    pkg = Package.from_file(path)
    assert pkg.names[0] == "None"
    assert len(set(pkg.names)) == len(pkg.names), "name table has duplicates"
    # Every name row carries the flags value 28,571 of Startup's 28,576 names use.
    pos = pkg.summary.name_offset
    for _ in range(pkg.summary.name_count):
        (n,) = _I32.unpack_from(pkg.data, pos)
        pos += 4 + (n if n >= 0 else -2 * n)
        (flags,) = struct.unpack_from("<Q", pkg.data, pos)
        assert flags == NAME_FLAGS
        pos += 8
    assert pos == pkg.summary.import_offset

    rows = [(i.class_package, i.class_name, i.outer, i.name) for i in pkg.imports]
    assert rows == [
        ("Core", "Package", 0, "Engine"),
        ("Core", "Class", -1, "SkeletalMesh"),
        ("Core", "Class", -1, "SkeletalMeshSocket"),
    ]
    assert pkg.object_path(-2) == "Engine.SkeletalMesh"
    assert pkg.object_path(-3) == "Engine.SkeletalMeshSocket"


def test_exports_resolve_to_expected_paths() -> None:
    path = build_clone()
    pkg = Package.from_file(path)
    assert pkg.summary.export_count == 1 + EXPECTED_SOCKETS

    mesh_entry = pkg.exports[0]
    assert mesh_entry.clsname == "SkeletalMesh"
    assert mesh_entry.outer_index == 0
    assert mesh_entry.object_flags == OBJECT_FLAGS_TOP_LEVEL
    assert mesh_entry.archetype_index == 0 and mesh_entry.super_index == 0
    assert mesh_entry.net_object_count == 0
    assert qualified_path(CLONE_PACKAGE_NAME, mesh_entry.path) == "PipelineMeshes.PL_AR_Gestalt_Mesh"

    sockets = [e for e in pkg.exports if e.outer_index == mesh_entry.index]
    assert len(sockets) == EXPECTED_SOCKETS
    paths = {qualified_path(CLONE_PACKAGE_NAME, e.path) for e in sockets}
    for expected in (
        "PipelineMeshes.PL_AR_Gestalt_Mesh.AR_Barrel_Dahl_Muzzle",
        "PipelineMeshes.PL_AR_Gestalt_Mesh.AR_Barrel_Vladof_Muzzle",
        "PipelineMeshes.PL_AR_Gestalt_Mesh.AR_Scope_Torgue_SightFX",
        "PipelineMeshes.PL_AR_Gestalt_Mesh.AR_Barrel_Alien_EyeSocket2",
    ):
        assert expected in paths, expected
    for entry in sockets:
        assert entry.clsname == "SkeletalMeshSocket"
        assert entry.object_flags == OBJECT_FLAGS_SUB_OBJECT
        assert entry.archetype_index == 0


def test_cloned_mesh_parses_and_self_checks() -> None:
    src = source_package()
    original = SkeletalMeshExport.parse(
        src.read_export_bytes(src.find_export(SOURCE_MESH, "SkeletalMesh")), src
    )
    pkg, mesh = open_mesh(build_clone())
    for lod in mesh.lods:
        lod.validate()
    assert mesh.has_native_data
    assert mesh.net_index == original.net_index == 48
    assert mesh.bone_names == original.bone_names
    assert len(mesh.bones) == EXPECTED_BONES
    assert len(mesh.lod0.vertices) == EXPECTED_VERTICES
    assert len(mesh.lod0.indices) == EXPECTED_INDICES
    assert mesh.materials == [0], "source Materials must become None, not a dangling ref"
    assert mesh.bounds_origin == original.bounds_origin
    assert mesh.bounds_extent == original.bounds_extent
    assert mesh.bounds_radius == original.bounds_radius
    assert mesh.skeletal_depth == original.skeletal_depth
    # Positions/UVs/tangents copied bit-exactly.
    for a, b in zip(mesh.lod0.vertices, original.lod0.vertices):
        assert (a.x, a.y, a.z) == (b.x, b.y, b.z)
        assert a.uvs == b.uvs and a.tangent_x == b.tangent_x

    # The tail's NameIndexMap must name the same bones, in the same order.
    (count,) = _I32.unpack_from(mesh.tail, 0)
    assert count == EXPECTED_BONES
    mapped = []
    for i in range(count):
        idx, num, value = struct.unpack_from("<3i", mesh.tail, 4 + 12 * i)
        mapped.append((pkg.fname(idx, num).text, value))
    assert mapped == [(name, i) for i, name in enumerate(mesh.bone_names)]
    assert len(mesh.tail) == len(original.tail)

    # Re-serializing where it actually lives must reproduce the stored bytes,
    # which proves the absolute bulk-data offsets match the placement.
    entry = pkg.find_export(MESH_NAME, "SkeletalMesh")
    assert mesh.serialize(entry.off) == pkg.read_export_bytes(entry)
    bulk = mesh.lod0.raw_point_indices
    assert bulk is not None
    assert entry.off < bulk.offset_in_file < entry.off + entry.size


def test_nested_property_names_are_remapped() -> None:
    pkg, mesh = open_mesh(build_clone())
    kinds = [(t.name.text, t.type.text) for t in mesh.properties]
    assert kinds == [
        ("ReferencePoseBounds", "StructProperty"),
        ("Sockets", "ArrayProperty"),
        ("LODInfo", "ArrayProperty"),
    ]

    bounds = find_property(mesh.properties, "ReferencePoseBounds")
    assert bounds is not None and bounds.struct_name is not None
    assert bounds.struct_name.text == "BoxSphereBounds"
    inner, end = parse_properties(bounds.raw, 0, pkg)
    assert end == len(bounds.raw)
    assert [t.name.text for t in inner] == ["Origin", "BoxExtent", "SphereRadius"]

    lod_info = find_property(mesh.properties, "LODInfo")
    assert lod_info is not None
    elem, pos = parse_properties(lod_info.raw, 4, pkg)
    assert pos == len(lod_info.raw)
    assert [t.name.text for t in elem] == [
        "DisplayFactor", "LODHysteresis", "LODMaterialMap",
        "bEnableShadowCasting", "TriangleSortSettings", "bDisableCompressions",
    ]
    # Three levels deep: LODInfo[0].TriangleSortSettings[0].TriangleSorting is a
    # ByteProperty whose enum name *and* value are both FNames.
    sort = find_property(elem, "TriangleSortSettings")
    assert sort is not None
    deep, deep_end = parse_properties(sort.raw, 4, pkg)
    assert deep_end == len(sort.raw)
    values = {t.name.text: t.value for t in deep}
    assert str(values["TriangleSorting"]) == "TRISORT_None"
    assert str(values["CustomLeftRightAxis"]) == "TSA_X_Axis"
    assert str(values["CustomLeftRightBoneName"]) == "None"


def test_socket_refs_and_socket_payloads() -> None:
    src = source_package()
    src_mesh_entry = src.find_export(SOURCE_MESH, "SkeletalMesh")
    src_sockets = [
        e for e in src.exports
        if e.outer_index == src_mesh_entry.index and e.clsname == "SkeletalMeshSocket"
    ]
    pkg, mesh = open_mesh(build_clone())
    mesh_entry = pkg.find_export(MESH_NAME, "SkeletalMesh")
    new_sockets = [e for e in pkg.exports if e.outer_index == mesh_entry.index]

    refs = mesh.socket_refs
    assert len(refs) == EXPECTED_SOCKETS
    assert refs == [e.index for e in new_sockets], "Sockets must point at the new exports"
    assert all(1 <= r <= pkg.summary.export_count for r in refs)

    for src_entry, new_entry in zip(src_sockets, new_sockets):
        before = socket_properties(src, src_entry)
        after = socket_properties(pkg, new_entry)
        assert set(before) == set(after)
        assert str(before["SocketName"]) == str(after["SocketName"])
        assert str(before["BoneName"]) == str(after["BoneName"])
        assert before["_net_index"] == after["_net_index"]
        for key in ("RelativeLocation", "RelativeRotation", "RelativeScale"):
            if key in before:
                assert before[key] == after[key]
        # The export is named after its SocketName, so the object path is usable.
        assert new_entry.name == str(after["SocketName"])
        # Every BoneName must actually be a bone of the cloned skeleton.
        assert str(after["BoneName"]) in mesh.bone_names
    NOTES["sockets"] = f"{len(new_sockets)} sockets, refs {refs[0]}..{refs[-1]}"


def test_builder_rejects_dangling_references() -> None:
    builder = PackageBuilder("Broken", template=source_package())
    cls = builder.import_class("Engine", "SkeletalMesh")
    builder.add_export(name="Orphan", class_ref=cls, outer=99, payload=b"\0" * 8)
    try:
        builder.build()
    except WriterError as exc:
        assert "Outer reference 99" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("builder accepted an out-of-range Outer")


# ---------------------------------------------------------------------------
# b. independent loader: Gildor's umodel
# ---------------------------------------------------------------------------


def test_umodel_lists_mesh_and_sockets() -> None:
    workspace = umodel_workspace(build_clone(), CLONE_PACKAGE_NAME)
    output = run_umodel(
        "-list", "-game=border", f"-path={workspace}", f"{CLONE_PACKAGE_NAME}.upk"
    )
    assert "Ver: 832/46" in output
    assert f"SkeletalMesh {MESH_NAME}" in output
    sockets = [
        line.split("SkeletalMeshSocket ", 1)[1].strip()
        for line in output.splitlines()
        if "SkeletalMeshSocket " in line
    ]
    assert len(sockets) == EXPECTED_SOCKETS, sockets
    assert "AR_Barrel_Vladof_Muzzle" in sockets
    assert "AR_Barrel_Dahl_Muzzle" in sockets
    NOTES["umodel_list"] = f"1 SkeletalMesh + {len(sockets)} SkeletalMeshSocket"


def test_umodel_exports_matching_gltf() -> None:
    gltf = umodel_gltf(build_clone(), CLONE_PACKAGE_NAME)
    lo, hi, count, joints = gltf_position_bounds(gltf)
    assert count == EXPECTED_VERTICES
    assert joints == EXPECTED_BONES
    if REFERENCE_GLTF.exists():
        ref_lo, ref_hi, ref_count, ref_joints = gltf_position_bounds(REFERENCE_GLTF)
        assert (count, joints) == (ref_count, ref_joints)
        assert lo == ref_lo and hi == ref_hi
        # umodel's own binary buffer must match the stock export byte for byte.
        assert gltf.with_suffix(".bin").read_bytes() == REFERENCE_GLTF.with_suffix(".bin").read_bytes()
        NOTES["umodel_gltf"] = (
            f"{count} verts / {joints} joints, bounds + .bin identical to stock Startup export"
        )
    else:  # pragma: no cover - reference export is optional
        NOTES["umodel_gltf"] = f"{count} verts / {joints} joints (no reference to diff)"


# ---------------------------------------------------------------------------
# c. the mutate path
# ---------------------------------------------------------------------------


def test_mutate_moves_the_expected_vertices() -> None:
    build_m1()
    assert _CACHE["m1_moved"] == EXPECTED_MOVED

    _clean_pkg, clean = open_mesh(build_clone())
    pkg, bent = open_mesh(build_m1())
    for lod in bent.lods:
        lod.validate()
    assert len(bent.lod0.vertices) == EXPECTED_VERTICES
    assert len(bent.lod0.indices) == EXPECTED_INDICES
    assert bent.bone_names == clean.bone_names
    assert pkg.summary.export_count == 1 + EXPECTED_SOCKETS

    ids = fragment_vertex_ids(clean.lod0)
    moved = [i for i in ids if clean.lod0.vertices[i].y < Y_CUT]
    assert len(moved) == EXPECTED_MOVED
    for i in moved:
        before, after = clean.lod0.vertices[i], bent.lod0.vertices[i]
        assert after.z == struct.unpack("<f", struct.pack("<f", before.z + DZ))[0]
        assert after.x == struct.unpack("<f", struct.pack("<f", before.x * X_SCALE))[0]
        assert after.y == before.y
    untouched = set(range(EXPECTED_VERTICES)) - set(moved)
    for i in sorted(untouched)[::97]:
        assert bent.lod0.vertices[i].position == clean.lod0.vertices[i].position

    entry = pkg.find_export(MESH_NAME, "SkeletalMesh")
    assert bent.serialize(entry.off) == pkg.read_export_bytes(entry)
    NOTES["mutate"] = (
        f"{len(moved)}/{len(ids)} {FRAGMENT} vertices moved (z+{DZ:g}, x*{X_SCALE:g})"
    )


def test_umodel_sees_the_bend() -> None:
    gltf = umodel_gltf(build_m1(), M1_PACKAGE_NAME)
    lo, hi, count, joints = gltf_position_bounds(gltf)
    assert count == EXPECTED_VERTICES and joints == EXPECTED_BONES

    # umodel scales by 1/100 and converts Z-up to Y-up: our x -> x, z -> y, y -> z.
    _pkg, bent = open_mesh(build_m1())
    xs = [v.x for v in bent.lod0.vertices]
    ys = [v.y for v in bent.lod0.vertices]
    zs = [v.z for v in bent.lod0.vertices]
    expect_lo = [min(xs) / 100, min(zs) / 100, min(ys) / 100]
    expect_hi = [max(xs) / 100, max(zs) / 100, max(ys) / 100]
    for got, want in zip(lo + hi, expect_lo + expect_hi):
        assert abs(got - want) < 1e-4, (lo, hi, expect_lo, expect_hi)

    # The moved vertices themselves: umodel must show exactly the 281 deltas we
    # applied, and nothing else.  (The mesh bounding box does *not* change -- the
    # barrel tip is nowhere near an extreme of the gestalt's 47-fragment atlas --
    # so comparing bounds alone would be blind to this edit.)
    clean_gltf = umodel_gltf(build_clone(), CLONE_PACKAGE_NAME)
    before = gltf_positions(clean_gltf)
    after = gltf_positions(gltf)
    assert len(before) == len(after) == EXPECTED_VERTICES
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == EXPECTED_MOVED, len(differing)
    for i in differing:
        bx, by, bz = before[i]
        ax, ay, az = after[i]
        assert abs(ax - bx * X_SCALE) < 1e-6, (i, bx, ax)
        assert abs(ay - (by + DZ / 100)) < 1e-6, (i, by, ay)  # our z -> glTF y
        assert abs(az - bz) < 1e-9

    # umodel's glTF vertex order is a *permutation* of the GPU vertex-buffer
    # order (it rebuilds vertices per chunk), so the changed vertices are matched
    # by position rather than by index.
    _pkg2, clean = open_mesh(build_clone())
    moved_ids = [
        i for i in fragment_vertex_ids(clean.lod0) if clean.lod0.vertices[i].y < Y_CUT
    ]

    def key(point: tuple[float, float, float]) -> tuple[float, float, float]:
        return (round(point[0], 4), round(point[1], 4), round(point[2], 4))

    got = Counter(key(before[i]) for i in differing)
    want = Counter(
        key((v.x / 100, v.z / 100, v.y / 100))
        for v in (clean.lod0.vertices[i] for i in moved_ids)
    )
    assert got == want, (len(got), len(want))
    NOTES["umodel_mutate"] = (
        f"{len(differing)} glTF vertices differ, each x*{X_SCALE:g} and "
        f"y+{DZ / 100:g} (umodel scale 1/100); bounds unchanged as expected"
    )


def main() -> int:
    checks = [
        ("a summary layout", test_summary_matches_derived_layout, "summary"),
        ("a name + import tables", test_name_and_import_tables, ""),
        ("a export paths", test_exports_resolve_to_expected_paths, ""),
        ("a mesh parses + self-checks", test_cloned_mesh_parses_and_self_checks, ""),
        ("a nested property names", test_nested_property_names_are_remapped, ""),
        ("a socket refs + payloads", test_socket_refs_and_socket_payloads, "sockets"),
        ("a builder rejects dangling refs", test_builder_rejects_dangling_references, ""),
        ("b umodel -list", test_umodel_lists_mesh_and_sockets, "umodel_list"),
        ("b umodel -export -gltf", test_umodel_exports_matching_gltf, "umodel_gltf"),
        ("c mutate moves vertices", test_mutate_moves_the_expected_vertices, "mutate"),
        ("c umodel sees the bend", test_umodel_sees_the_bend, "umodel_mutate"),
    ]
    failures = 0
    for label, fn, key in checks:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {label}: {type(exc).__name__}: {exc}")
        else:
            note = NOTES.get(key, "")
            print(f"PASS {label}" + (f"  [{note}]" if note else ""))
    print(f"{len(checks) - failures}/{len(checks)} checks passed")
    print(f"wrote {CLONE_OUT} and {M1_OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
