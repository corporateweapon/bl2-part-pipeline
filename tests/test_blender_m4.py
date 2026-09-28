"""pytest wrapper around the M4 headless proof.

Runs ``blender --background --python tests/blender_m4_roundtrip.py`` and asserts
the JSON result it writes.  Skipped when Blender is not installed (set
``BL2_BLENDER_EXE`` to point at another build) or when the decompressed
``Startup.upk`` has not been produced yet.

The parts of M4 that do not need Blender (the fragment table, the coordinate
transform, FPackedNormal packing, the unique-name guard) are tested here
directly, so a machine without Blender still checks most of the module.
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

from bl2_blender import api  # noqa: E402
from bl2_upk import pack_normal, unpack_normal  # noqa: E402

DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
SCRIPT = REPO / "tests" / "blender_m4_roundtrip.py"
SOURCE = REPO / "scratch" / "decomp" / "Startup.upk"
OUT_PACKAGE = REPO / "scratch" / "PipelineMeshes_m4.upk"
RESULT_JSON = REPO / "scratch" / "m4_roundtrip.json"

EXPECTED_VERTICES = 28519
EXPECTED_INDICES = 71577


def blender_exe() -> Path | None:
    override = os.environ.get("BL2_BLENDER_EXE")
    candidate = Path(override) if override else DEFAULT_BLENDER
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# no-Blender checks
# ---------------------------------------------------------------------------


def test_api_imports_without_bpy() -> None:
    """``api`` must not pull in bpy at module level (headless use)."""
    assert "bpy" not in sys.modules
    assert api.DEFAULT_FRAGMENT == "AR_Barrel_Vladof"


def test_coordinate_transform_is_its_own_inverse() -> None:
    for point in ((1.5, -80.25, 3.0), (-5.550102710723877, -103.8165054321289, 14.95450210571289)):
        assert api.blender_to_ue(*api.ue_to_blender(*point)) == point


def test_packed_normal_round_trip() -> None:
    for packed in (0x80FF7F01, 0x00000000, 0xFFFFFFFF):
        vector = unpack_normal(packed)[:3]
        assert pack_normal(vector, (packed >> 24) & 0xFF) == packed


@pytest.mark.skipif(not (REPO / "scratch" / "gestalt_AR_fragments.txt").exists(),
                    reason="scratch/gestalt_AR_fragments.txt (the AR fragment table) absent")
def test_unique_name_guard() -> None:
    suggested = api.suggest_fragment_name("AR_Barrel_Vladof")
    assert suggested == "AR_Barrel_Vladof_PL"
    assert api.check_fragment_name(suggested, "AR_Barrel_Vladof").level == "ok"
    # reusing an existing fragment name is the silent load-order failure (F1)
    assert api.check_fragment_name("AR_Barrel_Dahl", "AR_Barrel_Vladof").level == "error"
    assert api.check_fragment_name("AR_Barrel_Vladof", "AR_Barrel_Vladof").level == "error"


def test_addon_zip_is_self_contained(tmp_path) -> None:
    """The installable zip must carry bl2_upk with it (no repo on sys.path)."""
    from bl2_blender.packaging import build_addon_zip

    import zipfile

    zip_path = build_addon_zip(tmp_path / "bl2_blender_addon.zip")
    names = set(zipfile.ZipFile(zip_path).namelist())
    assert "bl2_blender/__init__.py" in names
    assert "bl2_blender/api.py" in names
    assert "bl2_blender/blender_manifest.toml" in names
    assert "bl2_blender/_vendor/bl2_upk/fragment.py" in names
    assert {n.split("/")[0] for n in names} == {"bl2_blender"}


@pytest.mark.skipif(not SOURCE.exists(), reason="scratch/decomp/Startup.upk not built")
def test_source_fragment_reads_bones_and_sockets() -> None:
    src = api.read_source_fragment(fragment_name="AR_Barrel_Vladof")
    assert (src.first_index, src.num_primitives) == (23484, 1164)
    assert len(src.vertex_ids) == 1308
    assert len(src.bone_names) == 38 and src.bone_names[0] == "Root"
    assert src.expected_sockets == [
        "AR_Barrel_Vladof_Muzzle",
        "AR_Barrel_Vladof_EyeSocket2",
        "AR_Barrel_Vladof_FrontSight",
    ]
    assert len(src.triangles()) == 1164
    assert max(max(t) for t in src.triangles()) == len(src.vertex_ids) - 1


# ---------------------------------------------------------------------------
# the real thing
# ---------------------------------------------------------------------------


@pytest.mark.skipif(blender_exe() is None, reason="Blender not installed")
@pytest.mark.skipif(not SOURCE.exists(), reason="scratch/decomp/Startup.upk not built")
def test_blender_roundtrip() -> None:
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
    assert result["counts"] == {"vertices": EXPECTED_VERTICES, "indices": EXPECTED_INDICES}
    assert result["moved"] == 281
    assert result["m2_reference"]["max_delta"] <= 1e-4
    export = result["export"]
    assert export["fragment"] == "AR_Barrel_PL_Bent"
    assert (export["first_index"], export["num_primitives"]) == (68085, 1164)
    assert export["dz"] == 8.0
    assert export["normal_agreement"] > 0.9
    assert OUT_PACKAGE.with_suffix(".fragment.json").exists()
    umodel = result["umodel"]
    assert umodel.get("skipped") or umodel["ok"], umodel


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
