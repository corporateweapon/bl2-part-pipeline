"""Round-trip tests for the BL2 SkeletalMesh codec.

Runnable either with pytest or directly::

    python tests/test_skelmesh_roundtrip.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_upk import (  # noqa: E402
    GpuVertex,
    Package,
    SkeletalMeshExport,
    replace_export_payload,
)

PACKAGE = REPO / "scratch" / "decomp" / "Startup.upk"
#: every test here parses the real, decompressed Startup.upk (game data, never committed)
pytestmark = pytest.mark.skipif(not PACKAGE.exists(),
                                reason="scratch/decomp/Startup.upk absent (bl2 doctor --fix)")
EXPORT_PATH = "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"
OUT_PACKAGE = REPO / "scratch" / "Startup_roundtrip_test.upk"

EXPECTED_SERIAL_SIZE = 1337044
EXPECTED_VERTICES = 27211
EXPECTED_TRIANGLES = 22695
EXPECTED_INDICES = 68085
EXPECTED_TEX_COORDS = 4
EXPECTED_SOCKETS = 48
BONE_NAMES = [
    "Root", "WeaponOffset", "Trigger", "Mag", "Mag_Spinner",
    "Mag_Bullet02", "Mag_Bullet03", "Mag_Bullet04", "Mag_Bullet05",
    "Mag_Bullet06", "Mag_Bullet07", "Mag_Bullet01", "Barrel_Spinner",
    "ChargeHandle", "hammer", "Loader", "AmmoLid", "Scope", "LeverHandle",
    "Slide", "MagRelease", "Barrel", "BarrelFlap01", "BarrelFlap02",
    "BarrelFlap03", "BarrelFlap04", "BarrelFlap05", "AlienBarrelFlapBL",
    "AlienBarrelFlapBR", "AlienBarrelFlapTL4", "AlienBarrelFlapTR4",
    "AlienBarrelFlapTR3", "AlienBarrelFlapTL3", "AlienBarrelFlapTL2",
    "AlienBarrelFlapTR2", "AlienBarrelFlapTR1", "AlienBarrelFlapTL1",
    "JacobSpinner",
]

_F32 = struct.Struct("<f")

#: Extra detail collected while running, printed by the script entry point.
NOTES: dict[str, str] = {}


def _f32(value: float) -> float:
    return _F32.unpack(_F32.pack(value))[0]


def load() -> tuple[Package, object, bytes, SkeletalMeshExport]:
    package = Package.from_file(PACKAGE)
    entry = package.find_export(EXPORT_PATH, "SkeletalMesh")
    payload = package.read_export_bytes(entry)
    return package, entry, payload, SkeletalMeshExport.parse(payload, package)


# --------------------------------------------------------------------------
# (a) parse consumes exactly SerialSize with all self-checks passing
# --------------------------------------------------------------------------


def test_parse_matches_ground_truth() -> None:
    package, entry, payload, mesh = load()
    assert entry.size == EXPECTED_SERIAL_SIZE
    assert len(payload) == EXPECTED_SERIAL_SIZE
    assert mesh.source_size == EXPECTED_SERIAL_SIZE

    assert mesh.net_index == 48
    assert mesh.skeletal_depth == 4
    assert mesh.materials == [0]
    assert package.object_path(mesh.materials[0]) == "None"
    assert len(mesh.socket_refs) == EXPECTED_SOCKETS
    assert mesh.has_vertex_colors is False
    ox, oy, oz = mesh.bounds_origin
    assert abs(ox) < 1e-3 and abs(oy + 47.74) < 0.01 and abs(oz - 2.25) < 0.01

    assert mesh.bone_names == BONE_NAMES
    assert len(mesh.bones) == 38
    assert mesh.bones[0].parent_index == 0
    assert mesh.bone_index("Barrel") == 21

    lod = mesh.lod0
    lod.validate()
    assert lod.num_vertices == EXPECTED_VERTICES
    assert len(lod.vertices) == EXPECTED_VERTICES
    assert len(lod.indices) == EXPECTED_INDICES
    assert lod.triangle_count == EXPECTED_TRIANGLES
    assert sum(s.num_triangles for s in lod.sections) * 3 == len(lod.indices)
    assert lod.num_tex_coords == EXPECTED_TEX_COORDS
    assert lod.vb_use_full_precision_uvs == 0
    assert lod.vertex_stride == 28 + 4 * EXPECTED_TEX_COORDS == 44
    assert lod.index_data_type_size == 2
    assert len(lod.chunks) == 1
    chunk = lod.chunks[0]
    assert chunk.num_rigid_vertices + chunk.num_soft_vertices == EXPECTED_VERTICES
    assert (chunk.num_rigid_vertices, chunk.num_soft_vertices) == (27209, 2)
    assert chunk.max_bone_influences == 4
    assert len(chunk.bone_map) == 38 == len(lod.active_bone_indices)
    assert len(lod.required_bones) == 38
    assert all(len(v.uvs) == EXPECTED_TEX_COORDS for v in lod.vertices[:64])

    bulk = lod.raw_point_indices
    assert bulk is not None
    assert (bulk.flags, bulk.element_count, bulk.size_on_disk) == (0, 0, 0)
    assert entry.off < bulk.offset_in_file < entry.off + entry.size

    # The tail must account for every remaining byte: NameIndexMap(38),
    # four empty TArrays, then CachedStreamingTextureFactors(4).
    tail = mesh.tail
    pos = 0
    (count,) = struct.unpack_from("<i", tail, pos)
    pos += 4
    assert count == 38
    mapped = {}
    for _ in range(count):
        ni, nn, value = struct.unpack_from("<iii", tail, pos)
        pos += 12
        mapped[package.fname(ni, nn).text] = value
    assert mapped == {name: i for i, name in enumerate(BONE_NAMES)}
    for _ in range(4):
        (empty,) = struct.unpack_from("<i", tail, pos)
        pos += 4
        assert empty == 0
    (factors,) = struct.unpack_from("<i", tail, pos)
    pos += 4
    assert factors == EXPECTED_TEX_COORDS
    pos += 4 * factors
    assert pos == len(tail), f"{len(tail) - pos} unexplained tail bytes"


# --------------------------------------------------------------------------
# (b) byte-exact re-serialization
# --------------------------------------------------------------------------


def test_serialize_is_byte_identical() -> None:
    _package, entry, payload, mesh = load()
    out = mesh.serialize(entry.off)
    assert len(out) == len(payload)
    assert out == payload


def test_property_block_round_trips() -> None:
    package, _entry, payload, mesh = load()
    from bl2_upk import serialize_properties

    block = serialize_properties(mesh.properties, package, mesh.property_terminator)
    start = 4
    assert block == payload[start : start + len(block)]
    names = [t.name.text for t in mesh.properties]
    assert names == ["ReferencePoseBounds", "Sockets", "LODInfo"]


# --------------------------------------------------------------------------
# (c) synthetic edit: move vertices with y < -80 by +8 in z
# --------------------------------------------------------------------------


def test_moved_vertices_round_trip() -> None:
    package, entry, payload, mesh = load()
    lod = mesh.lod0
    moved = [i for i, v in enumerate(lod.vertices) if v.y < -80.0]
    assert moved, "no vertices below y = -80"
    expected = {i: _f32(lod.vertices[i].z + 8.0) for i in moved}
    for i in moved:
        lod.vertices[i].z = _f32(lod.vertices[i].z + 8.0)

    out = mesh.serialize(entry.off)
    assert len(out) == len(payload)
    assert out != payload

    again = SkeletalMeshExport.parse(out, package)
    again.lod0.validate()
    assert len(again.lod0.vertices) == EXPECTED_VERTICES
    for i, z in expected.items():
        assert again.lod0.vertices[i].z == z
    unchanged = [i for i in range(0, EXPECTED_VERTICES, 997) if i not in expected]
    for i in unchanged:
        assert again.lod0.vertices[i].z == lod.vertices[i].z
    NOTES["moved"] = f"{len(moved)} vertices moved, payload still {len(out)} B"


# --------------------------------------------------------------------------
# (d) synthetic append: 3 vertices + 1 triangle, relocated to end of file
# --------------------------------------------------------------------------


def test_append_triangle_and_relocate() -> None:
    package, entry, payload, mesh = load()
    lod = mesh.lod0
    barrel = mesh.bone_index("Barrel")
    local = lod.chunks[0].bone_map.index(barrel)

    source_tri = lod.indices[0:3]
    base = len(lod.vertices)
    for offset, src in enumerate(source_tri):
        vertex: GpuVertex = lod.vertices[src].copy()
        vertex.bone_idx = [local, 0, 0, 0]
        vertex.bone_wt = [255, 0, 0, 0]
        vertex.z = _f32(vertex.z + 5.0 + offset)
        lod.vertices.append(vertex)
    lod.indices.extend([base, base + 1, base + 2])

    # The payload's absolute bulk offsets depend on where it lands, and
    # relocate_to_end always appends at the current end of the source file.
    new_offset = PACKAGE.stat().st_size
    new_payload = mesh.serialize(new_offset)
    assert len(new_payload) > len(payload)
    result = replace_export_payload(
        PACKAGE, EXPORT_PATH, new_payload, OUT_PACKAGE, strategy="relocate_to_end"
    )

    assert result.export_offset == new_offset
    assert result.serial_size == len(new_payload)

    patched = result.package
    entry2 = patched.find_export(EXPORT_PATH, "SkeletalMesh")
    payload2 = patched.read_export_bytes(entry2)
    assert payload2 == new_payload

    mesh2 = SkeletalMeshExport.parse(payload2, patched)
    lod2 = mesh2.lod0
    lod2.validate()
    assert len(lod2.vertices) == EXPECTED_VERTICES + 3
    assert lod2.num_vertices == EXPECTED_VERTICES + 3
    assert len(lod2.indices) == EXPECTED_INDICES + 3
    assert lod2.triangle_count == EXPECTED_TRIANGLES + 1
    assert lod2.sections[0].num_triangles == EXPECTED_TRIANGLES + 1
    assert lod2.chunks[0].num_rigid_vertices == 27209 + 3
    assert lod2.chunks[0].num_soft_vertices == 2
    assert lod2.indices[-3:] == [EXPECTED_VERTICES, EXPECTED_VERTICES + 1,
                                 EXPECTED_VERTICES + 2]
    for i in range(3):
        assert lod2.bone_index_for(EXPECTED_VERTICES + i) == barrel

    bulk = lod2.raw_point_indices
    assert bulk is not None
    assert entry2.off < bulk.offset_in_file < entry2.off + entry2.size
    # Re-serializing at the recorded offset must reproduce the stored bytes,
    # which proves every absolute bulk offset matches the placement.
    assert mesh2.serialize(entry2.off) == payload2

    # Every other export must still point where it did before.
    for before, after in zip(package.exports, patched.exports):
        if before.path == EXPORT_PATH:
            continue
        assert (before.off, before.size) == (after.off, after.size)
    NOTES["relocate"] = (
        f"{result.out_path.name} @ offset {result.export_offset}, "
        f"{result.serial_size} B (+{result.serial_size - len(payload)})"
    )


def test_inplace_strategy_rejects_size_change() -> None:
    _package, entry, payload, _mesh = load()
    try:
        replace_export_payload(
            PACKAGE, EXPORT_PATH, payload + b"\0", OUT_PACKAGE, strategy="inplace"
        )
    except Exception as exc:  # PatchError
        assert "inplace needs exactly" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("inplace accepted a size change")
    assert entry.size == EXPECTED_SERIAL_SIZE


def main() -> int:
    checks = [
        ("a parse + self-checks", test_parse_matches_ground_truth),
        ("b byte-identical serialize", test_serialize_is_byte_identical),
        ("b property block round-trip", test_property_block_round_trips),
        ("c moved vertices", test_moved_vertices_round_trip),
        ("d append + relocate_to_end", test_append_triangle_and_relocate),
        ("d inplace rejects resize", test_inplace_strategy_rejects_size_change),
    ]
    failures = 0
    keys = {"c moved vertices": "moved", "d append + relocate_to_end": "relocate"}
    for label, fn in checks:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {label}: {type(exc).__name__}: {exc}")
        else:
            note = NOTES.get(keys.get(label, ""), "")
            print(f"PASS {label}" + (f"  [{note}]" if note else ""))
    print(f"{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
