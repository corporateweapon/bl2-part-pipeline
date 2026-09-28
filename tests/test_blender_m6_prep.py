"""pytest wrapper around the M6 prep proof, plus the no-Blender unit checks.

``tests/blender_m6_prep.py`` is run under ``blender --background`` (skipped when
Blender is not installed; set ``BL2_BLENDER_EXE`` to point at another build) and
its JSON result is asserted here.

``bl2_upk.fragment.build_multi_fragment_package`` itself needs no Blender, so it
is tested directly with synthetic vertex arrays: two brand-new fragments built
from plain Python tuples, appended to the stock gestalt mesh and read back off
disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_upk import Package, SkeletalMeshExport  # noqa: E402
from bl2_upk import fragment as fragment_module  # noqa: E402
from bl2_upk.fragment import (  # noqa: E402
    MAX_GPU_VERTICES,
    FragmentSpec,
    NewVertex,
    build_multi_fragment_package,
    standard_deformation_edit,
)
from bl2_upk.skelmesh import SkelMeshError, float_to_half, half_to_float  # noqa: E402

DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
SCRIPT = REPO / "tests" / "blender_m6_prep.py"
SOURCE = REPO / "scratch" / "decomp" / "Startup.upk"
OUT_PACKAGE = REPO / "scratch" / "PipelineMeshes_multi.upk"
RESULT_JSON = REPO / "scratch" / "m6_prep.json"

MESH_NAME = "PL_AR_Gestalt_Mesh"
TEMPLATE_FRAGMENT = "AR_Barrel_Vladof"
STOCK_VERTICES = 27211
STOCK_INDICES = 68085


def blender_exe() -> Path | None:
    override = os.environ.get("BL2_BLENDER_EXE")
    candidate = Path(override) if override else DEFAULT_BLENDER
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# no-Blender checks: build_multi_fragment_package with synthetic vertex arrays
# ---------------------------------------------------------------------------


def _quad(y: float, bone: str, uv: float = 0.25) -> tuple[list[NewVertex], list[tuple]]:
    """Two triangles, four vertices, rigidly weighted to one bone."""
    half = float_to_half(uv)
    corners = [(-2.0, y - 2.0, 0.0), (2.0, y - 2.0, 0.0), (2.0, y + 2.0, 4.0),
               (-2.0, y + 2.0, 4.0)]
    vertices = [
        NewVertex(x=x, y=vy, z=z, bones=[bone, 0, 0, 0], weights=[255, 0, 0, 0],
                  uvs=[(half, half)])
        for x, vy, z in corners
    ]
    return vertices, [(0, 1, 2), (0, 2, 3)]


@pytest.fixture(scope="module")
def stock_mesh() -> SkeletalMeshExport:
    if not SOURCE.exists():
        pytest.skip("scratch/decomp/Startup.upk not built")
    package = Package.from_file(SOURCE)
    entry = package.find_export(
        "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh", "SkeletalMesh")
    return SkeletalMeshExport.parse(package.read_export_bytes(entry), package)


@pytest.mark.skipif(not SOURCE.exists(), reason="scratch/decomp/Startup.upk not built")
def test_build_multi_fragment_package_with_synthetic_vertices(tmp_path) -> None:
    out = tmp_path / "PipelineMeshes_unit.upk"
    barrel_verts, barrel_tris = _quad(-90.0, "Barrel")
    mag_verts, mag_tris = _quad(-30.0, "Mag", uv=0.75)
    info = build_multi_fragment_package(
        out,
        [
            FragmentSpec("PL_Unit_A", TEMPLATE_FRAGMENT,
                         vertices=barrel_verts, triangles=barrel_tris, sockets=["Muzzle"]),
            FragmentSpec("PL_Unit_B", TEMPLATE_FRAGMENT,
                         vertices=mag_verts, triangles=mag_tris, dz=2.0),
            # the M2/M4 shape still works in the same call
            FragmentSpec("PL_Unit_Bent", TEMPLATE_FRAGMENT, edit=standard_deformation_edit()),
        ],
        source_package=SOURCE,
        package_name="PipelineMeshes",
        mesh_name=MESH_NAME,
    )

    assert info["reparsed_ok"] is True
    assert [f["fragment"] for f in info["fragments"]] == ["PL_Unit_A", "PL_Unit_B",
                                                          "PL_Unit_Bent"]
    assert [f["new_vertices"] for f in info["fragments"]] == [4, 4, 1308]
    assert [f["num_primitives"] for f in info["fragments"]] == [2, 2, 1164]
    # the three ranges tile consecutively onto the end of the stock buffer
    assert [f["first_index"] for f in info["fragments"]] == [
        STOCK_INDICES, STOCK_INDICES + 6, STOCK_INDICES + 12]
    assert info["total_vertices"] == STOCK_VERTICES + 4 + 4 + 1308
    assert info["total_indices"] == STOCK_INDICES + 3 * (2 + 2 + 1164)
    # the first fragment is repeated at the top level for single-fragment consumers
    assert info["fragment"] == "PL_Unit_A"
    assert info["first_index"] == STOCK_INDICES
    assert info["num_primitives"] == 2

    sidecar = json.loads(Path(info["sidecar"]).read_text())
    assert sidecar["fragments"] == info["fragments"]
    assert sidecar["mesh_path"] == f"PipelineMeshes.{MESH_NAME}"
    assert sidecar["total_vertices"] == info["total_vertices"]
    for key in ("package", "mesh_path", "fragment", "template_fragment", "first_index",
                "num_primitives", "dz", "part_name", "reparsed_ok", "out"):
        assert key in sidecar, key

    package = Package.from_file(out)
    mesh = SkeletalMeshExport.parse(
        package.read_export_bytes(package.find_export(MESH_NAME, "SkeletalMesh")), package)
    lod = mesh.lod0
    assert len(lod.vertices) == info["total_vertices"]
    assert len(lod.indices) == info["total_indices"]
    assert max(lod.indices) < len(lod.vertices) < MAX_GPU_VERTICES

    # bone names resolved through the chunk bone map, weights normalised
    for entry, bone in ((info["fragments"][0], "Barrel"), (info["fragments"][1], "Mag")):
        first, num = entry["first_index"], entry["num_primitives"]
        vids = sorted(set(lod.indices[first: first + 3 * num]))
        assert len(vids) == entry["new_vertices"]
        for vid in vids:
            vertex = lod.vertices[vid]
            chunk = lod.chunk_for_vertex_index(vid)
            assert mesh.bone_names[chunk.bone_map[vertex.bone_idx[0]]] == bone
            assert vertex.bone_wt == [255, 0, 0, 0]
            assert len(vertex.uvs) == lod.num_tex_coords
    # raw UVs survive as halves, unused sets are zeroed
    first_vertex = lod.vertices[STOCK_VERTICES]  # the first appended vertex
    assert half_to_float(first_vertex.uvs[0][0]) == pytest.approx(0.25)
    assert first_vertex.uvs[1:] == [(0, 0)] * (lod.num_tex_coords - 1)


@pytest.mark.skipif(not SOURCE.exists(), reason="scratch/decomp/Startup.upk not built")
def test_multi_fragment_package_rejects_bad_input(tmp_path, monkeypatch) -> None:
    verts, tris = _quad(-40.0, "Mag")
    out = tmp_path / "nope.upk"

    with pytest.raises(KeyError):
        build_multi_fragment_package(
            out, [FragmentSpec("PL_X", "AR_Barrel_NotAFragment")], source_package=SOURCE)
    with pytest.raises(ValueError):
        build_multi_fragment_package(out, [], source_package=SOURCE)
    with pytest.raises(ValueError):  # duplicate fragment names
        build_multi_fragment_package(
            out,
            [FragmentSpec("PL_X", TEMPLATE_FRAGMENT, vertices=verts, triangles=tris),
             FragmentSpec("PL_X", TEMPLATE_FRAGMENT, vertices=verts, triangles=tris)],
            source_package=SOURCE,
        )
    with pytest.raises(SkelMeshError, match="references local vertex"):
        build_multi_fragment_package(
            out,
            [FragmentSpec("PL_X", TEMPLATE_FRAGMENT, vertices=verts, triangles=[(0, 1, 9)])],
            source_package=SOURCE,
        )
    with pytest.raises(SkelMeshError, match="no bone influence"):
        broken = [NewVertex(x=v.x, y=v.y, z=v.z, bones=v.bones, weights=[0, 0, 0, 0],
                            uvs=v.uvs) for v in verts]
        build_multi_fragment_package(
            out,
            [FragmentSpec("PL_X", TEMPLATE_FRAGMENT, vertices=broken, triangles=tris)],
            source_package=SOURCE,
        )
    # the uint16 index buffer is the hard ceiling (lowered here, not the mesh)
    monkeypatch.setattr(fragment_module, "MAX_GPU_VERTICES", STOCK_VERTICES + 2)
    with pytest.raises(SkelMeshError, match="uint16"):
        build_multi_fragment_package(
            out,
            [FragmentSpec("PL_X", TEMPLATE_FRAGMENT, vertices=verts, triangles=tris)],
            source_package=SOURCE,
        )
    assert not out.exists()


def test_unknown_bone_name_is_refused(stock_mesh) -> None:
    """A bone name the RefSkeleton does not have stops before anything is written."""
    verts, tris = _quad(-40.0, "NotABone")
    before = len(stock_mesh.lod0.vertices)
    with pytest.raises(KeyError):
        fragment_module.append_new_geometry(stock_mesh, verts, tris)
    assert len(stock_mesh.lod0.vertices) == before


# ---------------------------------------------------------------------------
# the real thing
# ---------------------------------------------------------------------------


@pytest.mark.skipif(blender_exe() is None, reason="Blender not installed")
@pytest.mark.skipif(not SOURCE.exists(), reason="scratch/decomp/Startup.upk not built")
def test_blender_m6_prep() -> None:
    if RESULT_JSON.exists():
        RESULT_JSON.unlink()
    proc = subprocess.run(
        [str(blender_exe()), "-noaudio", "--background", "--python", str(SCRIPT),
         "--", "--out", str(OUT_PACKAGE), "--json", str(RESULT_JSON)],
        capture_output=True, text=True, cwd=str(REPO), timeout=900,
    )
    output = proc.stdout + proc.stderr
    assert RESULT_JSON.exists(), f"the script wrote no result:\n{output[-4000:]}"
    result = json.loads(RESULT_JSON.read_text())
    failed = [c for c in result["checks"] if not c["ok"]]
    assert not failed, f"{failed}\n{output[-4000:]}"
    assert proc.returncode == 0, output[-4000:]

    assert result["ok"] is True
    export = result["export"]
    assert [f["fragment"] for f in export["fragments"]] == [
        "PL_Test_Cyl", "PL_Test_Box", "AR_Barrel_PL_Bent"]
    assert [f["new_geometry"] for f in export["fragments"]] == [True, True, False]
    assert export["fragments"][0]["first_index"] == STOCK_INDICES
    assert result["counts"]["vertices"] == STOCK_VERTICES + sum(
        o["vertices"] for o in result["objects"].values())
    assert result["bones"] == {"PL_Test_Cyl": ["Barrel"], "PL_Test_Box": ["Mag"]}
    assert result["sidecar"]["total_vertices"] == result["counts"]["vertices"]
    umodel = result["umodel"]
    assert umodel.get("skipped") or umodel["ok"], umodel


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
